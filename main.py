#!/usr/bin/env python3
"""Main entry point for Poker Telegram Bot."""

import logging
import os
import sys

from dotenv import load_dotenv

from pokerapp.config import Config
from pokerapp.pokerbot import PokerBot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Initialize and run the poker bot."""
    # Load variables from a .env file when present so that local development
    # environments and containerized deployments receive the expected
    # configuration without additional setup.
    load_dotenv()

    token = os.getenv("POKERBOT_TOKEN")
    if not token:
        logger.error("POKERBOT_TOKEN environment variable not set")
        sys.exit(1)

    cfg = Config()

    logger.info("Configured startup mode: %s", cfg.preferred_mode)
    mode = "webhook" if cfg.use_webhook else "polling"
    if cfg.preferred_mode == "auto":
        logger.info("Auto mode resolved to %s", mode)
    else:
        logger.info("Starting Poker Bot in %s mode", mode)
    logger.info("Debug mode: %s", cfg.DEBUG)

    bot = PokerBot(token=token, cfg=cfg)

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Fatal error: %s", exc)
        sys.exit(1)


if __name__ == '__main__':
    main()
