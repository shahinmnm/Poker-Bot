"""Centralized helpers for notifications and structured logging."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from telegram.error import BadRequest, TelegramError

__all__ = ["LoggerHelper", "NotificationManager"]


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

        try:
            if text is None:
                await query.answer(show_alert=show_alert)
            else:
                await query.answer(text=text, show_alert=show_alert)

            cls._log.info(
                event,
                message=text or "Callback acknowledged",
                user_id=user_id,
                alert=show_alert,
            )
            return True
        except BadRequest as exc:
            reason = str(exc)
            if cls._STALE_QUERY_MESSAGE.lower() in reason.lower():
                cls._log.debug(
                    f"{event}Stale",
                    "Ignoring stale callback query",
                    user_id=user_id,
                    error=reason,
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
