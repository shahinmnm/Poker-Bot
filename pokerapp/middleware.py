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
from telegram.ext import CallbackContext

from pokerapp.notify_utils import LoggerHelper

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
