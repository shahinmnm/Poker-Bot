"""Tests for configuration loading helpers."""

import os

import pytest

from pokerapp.config import Config


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure config-related environment variables are isolated per test."""

    for key in list(os.environ):
        if key.startswith("POKERBOT_"):
            monkeypatch.delenv(key, raising=False)


def test_webhook_url_appends_path_when_base_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKERBOT_WEBHOOK_PUBLIC_URL", "https://example.com")
    monkeypatch.setenv("POKERBOT_WEBHOOK_PATH", "/telegram/webhook")

    cfg = Config()

    assert cfg.webhook_url == "https://example.com/telegram/webhook"
    assert cfg.use_webhook is True


def test_webhook_url_respects_existing_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "POKERBOT_WEBHOOK_PUBLIC_URL",
        "https://shahin8n.sbs/telegram/webhook",
    )

    cfg = Config()

    assert cfg.webhook_url == "https://shahin8n.sbs/telegram/webhook"


def test_webhook_path_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKERBOT_WEBHOOK_PUBLIC_URL", "https://example.com")
    monkeypatch.setenv("POKERBOT_WEBHOOK_PATH", "telegram/webhook")

    cfg = Config()

    assert cfg.WEBHOOK_PATH == "/telegram/webhook"
    assert cfg.webhook_url == "https://example.com/telegram/webhook"


def test_webhook_url_handles_nested_base_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKERBOT_WEBHOOK_PUBLIC_URL", "https://example.com/bot")

    cfg = Config()

    assert cfg.webhook_url == "https://example.com/bot/telegram/webhook"


def test_webhook_url_preserves_query_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "POKERBOT_WEBHOOK_PUBLIC_URL",
        "https://example.com/telegram/webhook?token=abc",
    )

    cfg = Config()

    assert cfg.webhook_url == "https://example.com/telegram/webhook?token=abc"
