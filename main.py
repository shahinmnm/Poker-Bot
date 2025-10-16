#!/usr/bin/env python3

import asyncio
import logging

from dotenv import load_dotenv

from pokerapp.config import Config
from pokerapp.pokerbot import PokerBot


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for the poker bot."""
    load_dotenv()

    cfg = Config()

    if cfg.TOKEN == "":
        logger.error("Environment variable POKERBOT_TOKEN is not set")
        return

    logger.info("Starting Poker Bot...")
    bot = PokerBot(token=cfg.TOKEN, cfg=cfg)

    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        await bot.shutdown()
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Fatal error: %s", exc)
        await bot.shutdown()
        raise
    finally:
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")
