#!/usr/bin/env python3

import logging

import redis
from telegram.ext import Application, AIORateLimiter, ContextTypes

from pokerapp.config import Config
from pokerapp.pokerbotcontrol import PokerBotCotroller
from pokerapp.pokerbotmodel import PokerBotModel
from pokerapp.pokerbotview import PokerBotViewer


logger = logging.getLogger(__name__)


class PokerBot:
    def __init__(
        self,
        token: str,
        cfg: Config,
    ) -> None:
        self._application: Application = (
            Application.builder()
            .token(token)
            .rate_limiter(AIORateLimiter())
            .build()
        )

        kv = redis.Redis(
            host=cfg.REDIS_HOST,
            port=cfg.REDIS_PORT,
            db=cfg.REDIS_DB,
            password=cfg.REDIS_PASS if cfg.REDIS_PASS else None,
        )

        self._view = PokerBotViewer(bot=self._application.bot)
        self._model = PokerBotModel(
            view=self._view,
            bot=self._application.bot,
            kv=kv,
            cfg=cfg,
            application=self._application,
        )
        self._controller = PokerBotCotroller(self._model, self._application)
        self._is_shutdown: bool = False

    async def run(self) -> None:
        """Start the bot with polling."""
        self._application.add_error_handler(self._error_handler)

        try:
            await self._application.initialize()
            await self._application.start()
            await self._application.updater.start_polling()
            await self._application.updater.wait()
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Fatal error during bot execution: %s", exc)
            raise
        finally:
            await self.shutdown()

    async def _error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Log errors caused by updates."""
        del update  # update is unused but kept for signature compatibility
        exception = context.error
        if isinstance(exception, BaseException):
            logger.error(
                "Exception while handling an update: %s",
                exception,
                exc_info=(
                    type(exception),
                    exception,
                    exception.__traceback__,
                ),
            )
        else:
            logger.error("Exception while handling an update with unknown error")

    async def shutdown(self) -> None:
        """Gracefully shutdown the bot."""
        if self._is_shutdown:
            logger.info("Bot shutdown already completed")
            return

        try:
            if self._application.updater.running:
                await self._application.updater.stop()
            if self._application.running:
                await self._application.stop()
            await self._application.shutdown()
            logger.info("Bot shutdown completed")
            self._is_shutdown = True
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("Error during shutdown: %s", exc)
