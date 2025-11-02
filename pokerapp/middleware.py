#!/usr/bin/env python3
"""
Middleware for analytics tracking and rate limiting.
Provides monitoring and abuse prevention for the poker bot.
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, Deque, Optional

from telegram import Update
from telegram.ext import CallbackContext

from pokerapp.notify_utils import LoggerHelper
from pokerapp.entities import MenuContext
from pokerapp.i18n import translation_manager
from .menu_state import MenuStateManager, MenuLocation, MenuState

logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


@dataclass
class NavigationMetrics:
    """Track navigation performance metrics."""

    total_navigations: int = 0
    back_actions: int = 0
    home_actions: int = 0
    state_cache_hits: int = 0
    state_cache_misses: int = 0
    avg_build_time_ms: float = 0.0

    def record_navigation(self, nav_type: str):
        """Record a navigation action."""

        self.total_navigations += 1
        if nav_type == "back":
            self.back_actions += 1
        elif nav_type == "home":
            self.home_actions += 1

    def record_build_time(self, duration_ms: float):
        """Update average build time with new sample."""

        n = self.total_navigations
        if n == 0:
            self.avg_build_time_ms = duration_ms
        else:
            self.avg_build_time_ms = (
                (self.avg_build_time_ms * n + duration_ms) / (n + 1)
            )

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dictionary."""

        return {
            "total_navigations": self.total_navigations,
            "back_actions": self.back_actions,
            "home_actions": self.home_actions,
            "state_cache_hits": self.state_cache_hits,
            "state_cache_misses": self.state_cache_misses,
            "avg_build_time_ms": round(self.avg_build_time_ms, 2),
        }


class AnalyticsMiddleware:
    """Track command usage and user activity for analytics."""

    def __init__(self) -> None:
        self._command_counts: Dict[str, int] = defaultdict(int)
        self._user_activity: Dict[int, int] = defaultdict(int)
        self._start_time = time.time()

    async def track_command(
        self,
        update: Update,
        context: CallbackContext,  # noqa: D401 - PTB callback signature
    ) -> None:
        """Track command execution for analytics."""
        del context

        if not update.effective_message:
            return

        user_id = update.effective_user.id
        self._user_activity[user_id] += 1

        if (
            update.effective_message.text
            and update.effective_message.text.startswith('/')
        ):
            command = update.effective_message.text.split()[0]
            self._command_counts[command] += 1

            log_helper.info(
                "AnalyticsCommand",
                command=command,
                user_id=user_id,
                total=self._command_counts[command],
            )

    def get_stats(self) -> Dict[str, object]:
        """Return current analytics statistics."""
        uptime = time.time() - self._start_time
        return {
            'uptime_seconds': uptime,
            'total_commands': sum(self._command_counts.values()),
            'unique_users': len(self._user_activity),
            'command_breakdown': dict(self._command_counts),
            'top_users': sorted(
                self._user_activity.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:10],
        }


class UserRateLimiter:
    """Prevent spam and abuse with rate limiting per user."""

    def __init__(
        self,
        max_requests: int = 20,
        window_seconds: int = 60,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._user_requests: Dict[int, Deque[float]] = defaultdict(deque)

    async def check_rate_limit(
        self,
        update: Update,
        context: CallbackContext,
    ) -> Optional[bool]:
        """
        Check if user has exceeded rate limit.

        Returns:
            None if allowed, True if blocked (stops propagation)
        """
        del context

        if not update.effective_user:
            return None

        user_id = update.effective_user.id
        now = time.time()

        user_queue = self._user_requests[user_id]
        while user_queue and user_queue[0] < now - self._window_seconds:
            user_queue.popleft()

        if len(user_queue) >= self._max_requests:
            log_helper.warn(
                "RateLimitExceeded",
                user_id=user_id,
                request_count=len(user_queue),
                window_seconds=self._window_seconds,
            )

            if update.effective_message:
                rate_limit_message = translation_manager.t(
                    "msg.error.rate_limited",
                    user_id=user_id,
                )
                await update.effective_message.reply_text(
                    rate_limit_message,
                    disable_notification=True,
                )

            return True

        user_queue.append(now)
        return None


class PokerBotMiddleware:
    """Resolve per-chat menu context for rendering dynamic menus."""

    def __init__(
        self,
        model,
        store,
        translation_manager_module=translation_manager,
    ) -> None:
        self._model = model
        self._translation_manager = translation_manager_module
        self._menu_state_manager = MenuStateManager(store=store)
        self._metrics = NavigationMetrics()
        self._logger = logging.getLogger(__name__)
        self._kv = store

    @property
    def menu_state(self) -> MenuStateManager:
        """Expose menu state manager."""

        return self._menu_state_manager

    def get_navigation_metrics(self) -> Dict[str, Any]:
        """Get current navigation performance metrics."""

        return self._metrics.to_dict()

    async def log_metrics_periodic(self, interval_seconds: int = 300):
        """Periodically log navigation metrics (every 5 minutes by default)."""

        while True:
            await asyncio.sleep(interval_seconds)
            metrics = self.get_navigation_metrics()
            if metrics["total_navigations"] > 0:
                self._logger.info("Navigation metrics: %s", metrics)

    async def build_menu_context(
        self,
        chat_id: int,
        chat_type: str,
        user_id: int,
        language_code: Optional[str] = None,
        *,
        chat: Optional[Any] = None,
    ) -> MenuContext:
        """Build a :class:`MenuContext` describing the active chat state."""

        start_time = time.perf_counter()

        if language_code:
            resolved_language = self._translation_manager.get_user_language_or_detect(
                user_id,
                telegram_language_code=language_code,
            )
        else:
            resolved_language = self._translation_manager.get_user_language_or_detect(
                user_id,
            )

        if chat_type in ("group", "supergroup"):
            chat_language = None
            if hasattr(self._kv, "get_chat_language"):
                try:
                    chat_language = self._kv.get_chat_language(chat_id)
                except Exception:  # pragma: no cover - kv access guard
                    chat_language = None

            if chat_language:
                resolved_language = self._translation_manager.resolve_language(
                    lang=chat_language
                )
            else:
                resolved_language = self._translation_manager.DEFAULT_LANGUAGE

        model = self._model

        current_menu_state: Optional[MenuState] = await self._menu_state_manager.get_state(
            chat_id,
        )

        if current_menu_state is None:
            self._metrics.state_cache_misses += 1
        else:
            self._metrics.state_cache_hits += 1

        if current_menu_state is None:
            current_location = MenuLocation.MAIN
            context_data: Dict[str, Any] = {}
        else:
            try:
                current_location = MenuLocation(current_menu_state.location)
            except ValueError:
                current_location = MenuLocation.MAIN
            context_data = current_menu_state.context_data

        in_active_game = False
        is_game_host = False
        has_pending_invite = await model.has_pending_invite(user_id)
        active_private_game_code: Optional[str] = None
        group_game = None

        if chat_type == "private":
            private_game = await model.get_user_private_game(user_id)
            if private_game:
                in_active_game = True
                is_game_host = private_game.get("host_id") == user_id
                active_private_game_code = private_game.get("code")
        else:
            group_game = await model.get_active_group_game(chat_id)
            if group_game:
                players = group_game.get("players", [])
                in_active_game = user_id in players
                is_game_host = group_game.get("host_id") == user_id

        user_is_group_admin = False
        if chat_type in ("group", "supergroup") and chat is not None:
            try:
                member = await chat.get_member(user_id)
                user_is_group_admin = member.status in ("administrator", "creator")
            except Exception:
                pass

        if chat_type != "private" and group_game is None:
            group_game = await model.get_active_group_game(chat_id)

        duration_ms = (time.perf_counter() - start_time) * 1000
        self._metrics.record_build_time(duration_ms)

        if duration_ms > 100:
            self._logger.warning(
                "Slow menu context build: %.2f ms for chat %d",
                duration_ms,
                chat_id,
            )

        return MenuContext(
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            language_code=resolved_language,
            current_menu_location=current_location.value,
            menu_context_data=context_data,
            in_active_game=in_active_game,
            is_game_host=is_game_host,
            has_pending_invite=has_pending_invite,
            group_has_active_game=bool(group_game)
            if chat_type != "private"
            else False,
            user_is_group_admin=user_is_group_admin,
            active_private_game_code=active_private_game_code,
        )
