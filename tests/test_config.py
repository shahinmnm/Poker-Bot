"""Tests for configuration helpers."""

from __future__ import annotations

import pytest

from pokerapp.config import Config


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure config-related environment variables don't leak between tests."""

    keys = [
        "POKERBOT_WEBHOOK_PATH",
        "POKERBOT_WEBHOOK_PUBLIC_URL",
        "POKERBOT_PREFERRED_MODE",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def test_webhook_path_normalizes_leading_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """The webhook path keeps exactly one leading slash for URLs."""

    monkeypatch.setenv("POKERBOT_WEBHOOK_PUBLIC_URL", "https://example.com")
    monkeypatch.setenv("POKERBOT_WEBHOOK_PATH", "telegram/webhook")

    cfg = Config()

    assert cfg.webhook_path == "/telegram/webhook"
    assert cfg.webhook_url_path == "telegram/webhook"


def test_webhook_path_trims_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace around the configured path is ignored."""

    monkeypatch.setenv("POKERBOT_WEBHOOK_PUBLIC_URL", "https://example.com")
    monkeypatch.setenv("POKERBOT_WEBHOOK_PATH", "  /custom/path  ")

    cfg = Config()

    assert cfg.webhook_path == "/custom/path"
    assert cfg.webhook_url_path == "custom/path"


def test_webhook_path_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty paths fall back to the default webhook endpoint."""

    monkeypatch.setenv("POKERBOT_WEBHOOK_PUBLIC_URL", "https://example.com")
    monkeypatch.setenv("POKERBOT_WEBHOOK_PATH", "   ")

    cfg = Config()

    assert cfg.webhook_path == "/telegram/webhook"
    assert cfg.webhook_url_path == "telegram/webhook"
