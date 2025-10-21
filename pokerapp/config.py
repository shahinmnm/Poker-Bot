#!/usr/bin/env python3
"""Configuration management for Poker Telegram Bot."""

import os
from typing import Dict, Iterable, Literal, Optional, cast
from urllib.parse import urlparse, urlunparse


from pokerapp.entities import STAKE_PRESETS


def _first_env(
    names: Iterable[str],
    default: Optional[str] = None,
) -> Optional[str]:
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
            _first_env(
                ("POKERBOT_REDIS_PORT", "REDIS_PORT"),
                default="6379",
            )
        )
        self.REDIS_DB: int = int(
            _first_env(
                ("POKERBOT_REDIS_DB", "REDIS_DB"),
                default="0",
            )
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
            _first_env(
                ("POKERBOT_PREFERRED_MODE", "PREFERRED_MODE"),
                "auto",
            )
            .strip()
            .lower()
        )
        if preferred_mode not in {"auto", "webhook", "polling"}:
            raise ValueError(
                "POKERBOT_PREFERRED_MODE must be one of: "
                "'auto', 'webhook', 'polling'"
            )
        self.PREFERRED_MODE: Literal["auto", "webhook", "polling"] = cast(
            Literal["auto", "webhook", "polling"],
            preferred_mode,
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
            _first_env(
                ("POKERBOT_WEBHOOK_PORT", "WEBHOOK_PORT"),
                default="8443",
            )
        )
        raw_path = (
            _first_env(
                ("POKERBOT_WEBHOOK_PATH", "WEBHOOK_PATH"),
                default="/telegram/webhook",
            )
            or "/telegram/webhook"
        ).strip()
        if not raw_path.startswith("/"):
            raw_path = f"/{raw_path.lstrip('/')}"
        self.WEBHOOK_PATH: str = raw_path
        self.WEBHOOK_PUBLIC_URL: str = (
            _first_env(
                ("POKERBOT_WEBHOOK_PUBLIC_URL", "WEBHOOK_PUBLIC_URL"),
                default="",
            )
            or ""
        ).strip()
        self.WEBHOOK_SECRET: str = (
            _first_env(
                ("POKERBOT_WEBHOOK_SECRET", "WEBHOOK_SECRET"),
                default="",
            )
            or ""
        ).strip()

        # Rate Limiting Settings
        self.RATE_LIMIT_PER_MINUTE: int = int(
            os.getenv("POKERBOT_RATE_LIMIT_PER_MINUTE", "500")
        )
        self.RATE_LIMIT_PER_SECOND: int = int(
            os.getenv("POKERBOT_RATE_LIMIT_PER_SECOND", "10")
        )

        # Q9: Private game stake configurations
        self.DEFAULT_STAKE_LEVEL: str = "micro"
        self.ALLOW_CUSTOM_STAKES: bool = True
        self.PRIVATE_MAX_PLAYERS: int = int(
            os.getenv("POKERBOT_PRIVATE_MAX_PLAYERS", "6")
        )
        # Minimum players required to start a private game
        self.PRIVATE_MIN_PLAYERS: int = 2
        self.INITIAL_MONEY: int = int(
            os.getenv("POKERBOT_INITIAL_MONEY", "1000")
        )
        self.PRIVATE_STAKES: Dict[str, Dict[str, object]] = {
            key: {
                "name": preset.name,
                "min_buyin": preset.min_buy_in,
                "max_buyin": preset.min_buy_in * 5,
                "small_blind": preset.small_blind,
                "big_blind": preset.big_blind,
            }
            for key, preset in STAKE_PRESETS.items()
        }

        # Q10: Redis key expiration settings
        self.PRIVATE_GAME_TTL_SECONDS: int = int(
            os.getenv("POKERBOT_PRIVATE_GAME_TTL", "3600")
        )

        # Q7: Balance validation settings
        self.MINIMUM_BALANCE_MULTIPLIER: int = 20
        self.ENFORCE_BALANCE_CHECK: bool = True

        # Q8: Re-buy settings
        self.ALLOW_REBUY_BETWEEN_GAMES: bool = True
        self.REBUY_COOLDOWN_SECONDS: int = 30

    @property
    def webhook_url(self) -> str:
        """Return the absolute webhook URL if configured."""

        if not self.WEBHOOK_PUBLIC_URL:
            return ""

        parsed = urlparse(self.WEBHOOK_PUBLIC_URL)
        current_path = parsed.path.rstrip("/")

        if current_path.endswith(self.WEBHOOK_PATH):
            new_path = current_path
        elif current_path:
            new_path = f"{current_path}{self.WEBHOOK_PATH}"
        else:
            new_path = self.WEBHOOK_PATH

        rebuilt = parsed._replace(path=new_path or "/")
        return urlunparse(rebuilt)

    @property
    def use_webhook(self) -> bool:
        """Check if webhook mode is enabled."""
        if self.PREFERRED_MODE == "webhook":
            return True
        if self.PREFERRED_MODE == "polling":
            return False
        return bool(self.webhook_url)

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
            if not self.webhook_url:
                raise ValueError("Webhook URL could not be constructed")
            if not self.WEBHOOK_SECRET:
                raise ValueError(
                    "POKERBOT_WEBHOOK_SECRET required for webhook mode"
                )
            if self.WEBHOOK_PORT < 1 or self.WEBHOOK_PORT > 65535:
                raise ValueError(
                    f"Invalid webhook port: {self.WEBHOOK_PORT}"
                )
