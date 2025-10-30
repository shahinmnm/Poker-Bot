"""Centralized helpers for notifications and structured logging."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any, Iterable

from telegram.error import BadRequest, TelegramError

__all__ = ["LoggerHelper", "NotificationManager"]


if TYPE_CHECKING:
    from telegram import CallbackQuery


class LoggerHelper:
    """Format log records with consistent emoji-prefixed tags."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    @classmethod
    def for_logger(cls, logger: logging.Logger) -> "LoggerHelper":
        """Return a helper instance bound to *logger*."""

        return cls(logger)

    @staticmethod
    def _compose(message: str | None, items: Iterable[tuple[str, Any]]) -> str:
        parts: list[str] = []
        if message:
            parts.append(str(message))
        formatted = ", ".join(f"{key}={value}" for key, value in items)
        if formatted:
            parts.append(formatted)
        return " | ".join(parts) if parts else "-"

    def _log(
        self,
        level: str,
        prefix: str,
        event: str,
        message: str | None,
        kwargs: dict[str, Any],
    ) -> None:
        log_kwargs: dict[str, Any] = {}
        for key in ("exc_info", "stack_info", "extra"):
            if key in kwargs:
                log_kwargs[key] = kwargs.pop(key)

        payload = self._compose(message, kwargs.items())
        getattr(self._logger, level)(
            f"{prefix} [%s] %s",
            event,
            payload,
            **log_kwargs,
        )

    def debug(
        self,
        event: str,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._log("debug", "🧪", event, message, kwargs)

    def info(
        self,
        event: str,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._log("info", "🎯", event, message, kwargs)

    def warn(
        self,
        event: str,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._log("warning", "⚠️", event, message, kwargs)

    def error(
        self,
        event: str,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._log("error", "❌", event, message, kwargs)


class NotificationManager:
    """Unified helper for Telegram popup style notifications."""

    _log = LoggerHelper.for_logger(logging.getLogger("pokerapp.notifications"))
    _STALE_QUERY_MESSAGE = (
        "Query is too old and response timeout expired or query id is invalid"
    )
    _FRESH_WINDOW_SECONDS = 8.0
    _CACHE_TTL_SECONDS = 30.0

    @dataclass
    class _CallbackState:
        first_seen: float
        answered: bool = False

    _callback_states: dict[str, "NotificationManager._CallbackState"] = {}

    @classmethod
    def _prune_cache(cls, now: float) -> None:
        """Remove cached callback entries that have outlived the TTL."""

        before_count = len(cls._callback_states)
        expired = [
            query_id
            for query_id, state in cls._callback_states.items()
            if now - state.first_seen > cls._CACHE_TTL_SECONDS
        ]
        for query_id in expired:
            cls._callback_states.pop(query_id, None)

        after_count = len(cls._callback_states)
        if before_count > 100 and expired:
            cls._log.info(
                "CachePruned",
                f"Removed {before_count - after_count} expired callbacks",
                active_count=after_count,
            )

    @classmethod
    def _should_answer(
        cls,
        query,
    ) -> tuple[
        bool,
        str | None,
        "NotificationManager._CallbackState" | None,
        float,
    ]:
        """Determine whether the callback query should be answered."""

        query_id = getattr(query, "id", None)
        now = monotonic()
        if not query_id:
            return True, None, None, now

        cls._prune_cache(now)

        state = cls._callback_states.get(query_id)
        if state is None:
            state = cls._CallbackState(first_seen=now)
            cls._callback_states[query_id] = state
        else:
            age = now - state.first_seen
            if state.answered:
                cls._log.debug(
                    "PopupSkip",
                    "Callback already answered",
                    query_id=query_id,
                    age=f"{age:.3f}",
                )
                return False, query_id, state, now
            if age > cls._FRESH_WINDOW_SECONDS:
                cls._log.debug(
                    "PopupSkip",
                    "Callback too old for popup",
                    query_id=query_id,
                    age=f"{age:.3f}",
                )
                state.answered = True
                return False, query_id, state, now

        return True, query_id, state, now

    @classmethod
    async def popup(
        cls,
        query,
        text: str | None = None,
        *,
        show_alert: bool = False,
        event: str = "Popup",
    ) -> bool:
        """Attempt to answer a callback query and log the outcome."""

        if query is None:
            cls._log.warn(event, "Popup skipped", reason="missing_query")
            return False

        user_id = getattr(getattr(query, "from_user", None), "id", "?")

        should_answer, query_id, state, now = cls._should_answer(query)
        if not should_answer:
            return False

        try:
            if text is None:
                await query.answer(show_alert=show_alert)
            else:
                await query.answer(text=text, show_alert=show_alert)

            if state:
                state.answered = True

            cls._log.info(
                event,
                message=text or "Callback acknowledged",
                user_id=user_id,
                alert=show_alert,
            )
            return True
        except BadRequest as exc:
            reason = str(exc)
            is_stale = cls._STALE_QUERY_MESSAGE.lower() in reason.lower()

            if state:
                state.answered = True

            if is_stale:
                cls._log.debug(
                    f"{event}Stale",
                    "Ignoring stale callback query",
                    user_id=user_id,
                    query_id=query_id,
                    age=f"{(now - state.first_seen):.3f}" if state else "?",
                )
            else:
                cls._log.error(
                    f"{event}Error",
                    "Failed answering callback query",
                    user_id=user_id,
                    error=reason,
                )
                cls._log.warn(
                    f"{event}Fail",
                    "Popup delivery failed",
                    user_id=user_id,
                    alert=show_alert,
                    error=reason,
                )
        except TelegramError as exc:  # pragma: no cover
            cls._log.warn(
                f"{event}Fail",
                "Telegram error during popup",
                user_id=user_id,
                alert=show_alert,
                error=str(exc),
            )
        return False

    @classmethod
    async def toast(
        cls,
        query: "CallbackQuery",
        text: str,
        event: str = "Toast",
    ) -> bool:
        """
        Send a subtle toast notification (non-blocking, no alert popup).

        Args:
            query: CallbackQuery object from button press
            text: Toast message (keep under 200 chars for best UX)
            event: Log event name for telemetry

        Returns:
            True if toast was shown successfully, False if skipped/failed

        Example:
            await NotificationManager.toast(
                query,
                text="✅ Bet placed",
                event="BetToast"
            )
        """
        should_answer, query_id, state, now = cls._should_answer(query)

        if not should_answer:
            cls._log.debug(
                f"{event}Skip",
                "Toast skipped (already answered or stale)",
                query_id=query_id,
            )
            return False

        try:
            # Non-blocking toast (show_alert=False means it appears as subtle notification)
            await query.answer(text=text, show_alert=False)

            # Mark as answered in guard
            if state:
                state.answered = True

            cls._log.debug(
                event,
                f"Toast shown: {text}",
                query_id=query_id,
                user_id=getattr(query.from_user, "id", None),
            )

            return True

        except BadRequest as exc:
            error_text = str(exc)
            is_stale = cls._STALE_QUERY_MESSAGE in error_text

            if is_stale:
                cls._log.debug(
                    f"{event}Stale",
                    "Attempted toast on stale query (8s+ old)",
                    query_id=query_id,
                )
            else:
                cls._log.warning(
                    f"{event}Failed",
                    f"Toast failed: {error_text}",
                    query_id=query_id,
                )

            return False

        except Exception as exc:  # pragma: no cover - defensive logging
            cls._log.error(
                f"{event}Error",
                f"Unexpected toast error: {exc}",
                query_id=query_id,
                exc_info=True,
            )
            return False

    @classmethod
    async def popup_with_fallback(
        cls,
        query,
        *,
        text: str,
        bot=None,
        fallback_chat_id: int | None = None,
        show_alert: bool = True,
        event: str = "Popup",
    ) -> bool:
        """Show a popup with optional fallback chat message."""

        answered = await cls.popup(
            query,
            text=text,
            show_alert=show_alert,
            event=event,
        )

        if answered or not (bot and fallback_chat_id and show_alert):
            return answered

        try:
            send_method = getattr(bot, "send_message", None)
            if send_method is None:
                raise AttributeError("Bot object missing send_message")
            await send_method(chat_id=fallback_chat_id, text=text)
            cls._log.info(
                f"{event}Fallback",
                message=text,
                chat_id=fallback_chat_id,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            cls._log.error(
                f"{event}FallbackError",
                "Fallback message failed",
                chat_id=fallback_chat_id,
                error=str(exc),
            )
        return answered
