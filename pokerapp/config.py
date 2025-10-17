#!/usr/bin/env python3
"""Configuration management for Poker Telegram Bot."""

import os
from typing import Iterable, Literal, Optional, cast


def _first_env(names: Iterable[str], default: Optional[str] = None) -> Optional[str]:
    """Return the first environment variable that is set from ``names``."""

    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse a boolean environment variable value."""

    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Load and validate configuration from environment variables."""

    def __init__(self) -> None:
        # Existing Redis configuration
        self.REDIS_HOST: str = _first_env(
            ("POKERBOT_REDIS_HOST", "REDIS_HOST"),
            default="redis",
        )
        self.REDIS_PORT: int = int(
            _first_env(("POKERBOT_REDIS_PORT", "REDIS_PORT"), default="6379")
        )
        self.REDIS_DB: int = int(
            _first_env(("POKERBOT_REDIS_DB", "REDIS_DB"), default="0")
        )
        self.REDIS_PASS: str = _first_env(
            ("POKERBOT_REDIS_PASS", "REDIS_PASS"),
            default="",
        ) or ""

        # Debug mode
        self.DEBUG: bool = _parse_bool(
            _first_env(("POKERBOT_DEBUG", "DEBUG")),
            default=False,
        )

        preferred_mode = (
            _first_env(("POKERBOT_PREFERRED_MODE", "PREFERRED_MODE"), "auto")
            .strip()
            .lower()
        )
        if preferred_mode not in {"auto", "webhook", "polling"}:
            raise ValueError(
                "POKERBOT_PREFERRED_MODE must be one of: "
                "'auto', 'webhook', 'polling'"
            )
        self.PREFERRED_MODE: Literal["auto", "webhook", "polling"] = cast(
            Literal["auto", "webhook", "polling"], preferred_mode
        )

        # PTB 21.x Connection Settings
        self.CONCURRENT_UPDATES: int = int(
            os.getenv("CONCURRENT_UPDATES", "256")
        )
        self.CONNECT_TIMEOUT: int = int(
            os.getenv("CONNECT_TIMEOUT", "30")
        )
        self.POOL_TIMEOUT: int = int(
            os.getenv("POOL_TIMEOUT", "30")
        )
        self.READ_TIMEOUT: int = int(
            os.getenv("READ_TIMEOUT", "30")
        )
        self.WRITE_TIMEOUT: int = int(
            os.getenv("WRITE_TIMEOUT", "30")
        )

        # Webhook Settings (from your .env.example)
        self.WEBHOOK_LISTEN: str = _first_env(
            ("POKERBOT_WEBHOOK_LISTEN", "WEBHOOK_LISTEN"),
            default="0.0.0.0",
        ) or "0.0.0.0"
        self.WEBHOOK_PORT: int = int(
            _first_env(("POKERBOT_WEBHOOK_PORT", "WEBHOOK_PORT"), default="8443")
        )
        self.WEBHOOK_PATH: str = _first_env(
            ("POKERBOT_WEBHOOK_PATH", "WEBHOOK_PATH"),
            default="/telegram/webhook",
        ) or "/telegram/webhook"
        self.WEBHOOK_PUBLIC_URL: str = (
            _first_env(("POKERBOT_WEBHOOK_PUBLIC_URL", "WEBHOOK_PUBLIC_URL"), default="")
            or ""
        )
        self.WEBHOOK_SECRET: str = (
            _first_env(("POKERBOT_WEBHOOK_SECRET", "WEBHOOK_SECRET"), default="")
            or ""
        )

        # Rate Limiting Settings
        self.RATE_LIMIT_PER_MINUTE: int = int(
            os.getenv("POKERBOT_RATE_LIMIT_PER_MINUTE", "500")
        )
        self.RATE_LIMIT_PER_SECOND: int = int(
            os.getenv("POKERBOT_RATE_LIMIT_PER_SECOND", "10")
        )

    @property
    def use_webhook(self) -> bool:
        """Check if webhook mode is enabled."""
        if self.PREFERRED_MODE == "webhook":
            return True
        if self.PREFERRED_MODE == "polling":
            return False
        return bool(self.WEBHOOK_PUBLIC_URL)

    @property
    def preferred_mode(self) -> Literal["auto", "webhook", "polling"]:
        """Return the configured startup preference."""
        return self.PREFERRED_MODE

    def validate(self) -> None:
        """Validate configuration and raise if invalid."""
        if self.PREFERRED_MODE == "webhook" and not self.WEBHOOK_PUBLIC_URL:
            raise ValueError(
                "POKERBOT_WEBHOOK_PUBLIC_URL required when "
                "POKERBOT_PREFERRED_MODE=webhook"
            )

        if self.use_webhook:
            if not self.WEBHOOK_PUBLIC_URL:
                raise ValueError(
                    "POKERBOT_WEBHOOK_PUBLIC_URL required for webhook mode"
                )
            if not self.WEBHOOK_SECRET:
                raise ValueError(
                    "POKERBOT_WEBHOOK_SECRET required for webhook mode"
                )
            if self.WEBHOOK_PORT < 1 or self.WEBHOOK_PORT > 65535:
                raise ValueError(
                    f"Invalid webhook port: {self.WEBHOOK_PORT}"
                )
