#!/usr/bin/env python3
"""
Middleware for analytics tracking and rate limiting.
Provides monitoring and abuse prevention for the poker bot.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Dict, Deque, Optional

from telegram import Update
from telegram.ext import CallbackContext, ContextTypes

from pokerapp.notify_utils import LoggerHelper
from pokerapp.entities import MenuContext
from pokerapp.i18n import translation_manager
from .menu_state import MenuStateManager, MenuLocation, MenuState

logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


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
                await update.effective_message.reply_text(
                    (
                        "⚠️ Too many requests. Please slow down and "
                        "try again later."
                    ),
                    disable_notification=True,
                )

            return True

        user_queue.append(now)
        return None


class PokerBotMiddleware:
    """Resolve per-chat menu context for rendering dynamic menus."""

    def __init__(self, model, store) -> None:
        self._model = model
        self._menu_state_manager = MenuStateManager(store=store)

    @property
    def menu_state(self) -> MenuStateManager:
        """Expose menu state manager."""

        return self._menu_state_manager

    async def get_user_language(self, user_id: int) -> str:
        """Return the preferred language code for ``user_id``."""

        return translation_manager.get_user_language_or_detect(user_id)

    async def build_menu_context(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> MenuContext:
        """Build a :class:`MenuContext` describing the active chat state."""

        del context  # Unused PTB parameter

        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            raise ValueError("Cannot build menu context without user/chat")

        chat_id = chat.id
        chat_type = chat.type  # "private", "group", or "supergroup"
        user_id = user.id

        # Get language preference
        language_code = await self.get_user_language(user_id)

        # Query game state from model
        model = self._model

        current_menu_state: Optional[MenuState] = await self._menu_state_manager.get_state(
            user_id=user_id,
            chat_id=chat_id,
        )
        current_location = (
            current_menu_state.location
            if current_menu_state
            else MenuLocation.MAIN_MENU
        )

        # Check if user is in any active game
        in_active_game = False
        is_game_host = False
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

        # Check pending invites
        has_pending_invite = await model.has_pending_invite(user_id)

        # Group admin check
        user_is_group_admin = False
        if chat_type in ("group", "supergroup"):
            try:
                member = await chat.get_member(user_id)
                user_is_group_admin = member.status in ("administrator", "creator")
            except Exception:
                pass

        if chat_type != "private" and group_game is None:
            group_game = await model.get_active_group_game(chat_id)

        return MenuContext(
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            language_code=language_code,
            current_menu_location=current_location.value,
            menu_context_data=(
                current_menu_state.context_data if current_menu_state else {}
            ),
            in_active_game=in_active_game,
            is_game_host=is_game_host,
            has_pending_invite=has_pending_invite,
            group_has_active_game=bool(group_game) if chat_type != "private" else False,
            user_is_group_admin=user_is_group_admin,
            active_private_game_code=active_private_game_code,
        )
