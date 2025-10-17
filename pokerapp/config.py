#!/usr/bin/env python3
"""Configuration management for Poker Telegram Bot."""

import os
from typing import Literal, cast


class Config:
    """Load and validate configuration from environment variables."""

    def __init__(self) -> None:
        # Existing Redis configuration
        self.REDIS_HOST: str = os.getenv("REDIS_HOST", "redis")
        self.REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
        self.REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
        self.REDIS_PASS: str = os.getenv("REDIS_PASS", "")

        # Debug mode
        self.DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

        preferred_mode = (
            os.getenv("POKERBOT_PREFERRED_MODE", "auto")
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
        self.WEBHOOK_LISTEN: str = os.getenv(
            "POKERBOT_WEBHOOK_LISTEN", "0.0.0.0"
        )
        self.WEBHOOK_PORT: int = int(
            os.getenv("POKERBOT_WEBHOOK_PORT", "8443")
        )
        self.WEBHOOK_PATH: str = os.getenv(
            "POKERBOT_WEBHOOK_PATH", "/telegram/webhook"
        )
        self.WEBHOOK_PUBLIC_URL: str = os.getenv(
            "POKERBOT_WEBHOOK_PUBLIC_URL", ""
        )
        self.WEBHOOK_SECRET: str = os.getenv(
            "POKERBOT_WEBHOOK_SECRET", ""
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
            if self.WEBHOOK_PORT < 1 or self.WEBHOOK_PORT > 65535:
                raise ValueError(
                    f"Invalid webhook port: {self.WEBHOOK_PORT}"
                )
