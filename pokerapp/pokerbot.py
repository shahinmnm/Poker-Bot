#!/usr/bin/env python3

import asyncio
import logging

import redis
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackContext,
    MessageHandler,
    filters,
)

from pokerapp.config import Config
from pokerapp.middleware import AnalyticsMiddleware, UserRateLimiter
from pokerapp.notify_utils import LoggerHelper
from pokerapp.pokerbotcontrol import PokerBotController
from pokerapp.pokerbotmodel import PokerBotModel
from pokerapp.pokerbotview import PokerBotViewer
from pokerapp.kvstore import ensure_kv
from pokerapp.i18n import translation_manager

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


class PokerBot:
    """Main Poker Bot application with webhook/polling support."""

    def __init__(
        self,
        token: str,
        cfg: Config,
    ) -> None:
        """
        Initialize PokerBot with modern PTB 21.x features.

        Args:
            token: Telegram bot token
            cfg: Configuration object with all settings
        """
        cfg.validate()

        self._application: Application = (
            Application.builder()
            .token(token)
            .rate_limiter(
                AIORateLimiter(
                    max_retries=3,
                    overall_max_rate=cfg.RATE_LIMIT_PER_SECOND,
                    group_max_rate=cfg.RATE_LIMIT_PER_MINUTE / 60,
                )
            )
            .connect_timeout(cfg.CONNECT_TIMEOUT)
            .pool_timeout(cfg.POOL_TIMEOUT)
            .read_timeout(cfg.READ_TIMEOUT)
            .write_timeout(cfg.WRITE_TIMEOUT)
            .concurrent_updates(cfg.CONCURRENT_UPDATES)
            .build()
        )

        redis_backend = redis.Redis(
            host=cfg.REDIS_HOST,
            port=cfg.REDIS_PORT,
            db=cfg.REDIS_DB,
            password=cfg.REDIS_PASS or None,
            decode_responses=False,
        )
        self._kv = ensure_kv(redis_backend)

        translation_manager.attach_kvstore(self._kv)

        self._analytics = AnalyticsMiddleware()
        self._rate_limiter = UserRateLimiter(
            max_requests=cfg.RATE_LIMIT_PER_MINUTE,
            window_seconds=60,
        )

        initial_language = translation_manager.get_language_context()
        self._view = PokerBotViewer(
            bot=self._application.bot,
            kv=self._kv,
            language_context=initial_language,
        )
        self._model = PokerBotModel(
            view=self._view,
            bot=self._application.bot,
            kv=self._kv,
            cfg=cfg,
            application=self._application,
        )
        self._controller = PokerBotController(
            model=self._model,
            application=self._application,
            kv=self._kv,
        )

        self._cfg = cfg

        log_helper.info("BotInit", "PokerBot initialized successfully")

    async def _error_handler(
        self,
        update: object,
        context: CallbackContext,
    ) -> None:
        """Global error handler for all bot operations."""
        log_helper.error(
            "BotError",
            "Exception while handling update",
            update=update,
            exc_info=context.error,
        )

        if update and isinstance(update, Update) and update.effective_message:
            try:
                user = update.effective_user
                user_id = getattr(user, "id", None)
                language_code = getattr(user, "language_code", None)
                error_text = translation_manager.t(
                    "msg.error.generic",
                    user_id=user_id,
                    lang=language_code,
                )
                await update.effective_message.reply_text(error_text)
            except TelegramError:
                pass

    def run(self) -> None:
        """Start the bot in polling or webhook mode based on config."""
        self._application.add_handler(
            MessageHandler(filters.ALL, self._analytics.track_command),
            group=-100,
        )
        self._application.add_handler(
            MessageHandler(filters.ALL, self._rate_limiter.check_rate_limit),
            group=-50,
        )

        self._application.add_error_handler(self._error_handler)

        try:
            if self._cfg.use_webhook:
                log_helper.info(
                    "BotWebhook",
                    "Starting webhook mode",
                    listen=self._cfg.WEBHOOK_LISTEN,
                    port=self._cfg.WEBHOOK_PORT,
                )

                webhook_url = self._cfg.webhook_url

                try:
                    self._application.run_webhook(
                        listen=self._cfg.WEBHOOK_LISTEN,
                        port=self._cfg.WEBHOOK_PORT,
                        url_path=self._cfg.WEBHOOK_PATH,
                        webhook_url=webhook_url,
                        secret_token=self._cfg.WEBHOOK_SECRET or None,
                        drop_pending_updates=True,
                        allowed_updates=Update.ALL_TYPES,
                    )

                    log_helper.info(
                        "BotWebhook",
                        "Webhook configured",
                        url=webhook_url,
                    )
                    return
                except Exception as exc:
                    log_helper.error(
                        "BotWebhook",
                        "Webhook mode failed; falling back to polling",
                        error=str(exc),
                        exc_info=True,
                    )

                    # When run_webhook() fails, PTB closes the
                    # current event loop, which would make the
                    # subsequent run_polling() call fail with
                    # "Event loop is closed".
                    # Ensure a fresh loop for the polling fallback so
                    # the bot can recover gracefully.
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)

            log_helper.info("BotPolling", "Starting polling mode")
            self._application.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )

        except Exception as exc:  # pragma: no cover - safety net
            log_helper.error(
                "BotRun",
                "Fatal error during bot execution",
                error=str(exc),
                exc_info=True,
            )
            raise
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Log shutdown statistics for the bot."""
        log_helper.info("BotShutdown", "Shutting down bot...")

        try:
            stats = self._analytics.get_stats()
            log_helper.info(
                "BotShutdown",
                "Final stats collected",
                stats=stats,
            )
        except Exception as exc:  # pragma: no cover - safety net
            log_helper.error(
                "BotShutdown",
                "Error collecting shutdown stats",
                error=str(exc),
            )

        log_helper.info("BotShutdown", "Bot shut down complete")
