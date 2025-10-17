"""Tests for configuration loading helpers."""

import os
import unittest

from pokerapp.config import Config


class ConfigEnvTestCase(unittest.TestCase):
    """Ensure config-related environment variables are isolated per test."""

    def setUp(self) -> None:  # noqa: D401 - short description inherited
        self._original_env = {
            key: os.environ[key]
            for key in os.environ
            if key.startswith("POKERBOT_")
        }
        for key in list(os.environ):
            if key.startswith("POKERBOT_"):
                del os.environ[key]

    def tearDown(self) -> None:
        for key in list(os.environ):
            if key.startswith("POKERBOT_"):
                del os.environ[key]
        for key, value in self._original_env.items():
            os.environ[key] = value


class TestConfig(ConfigEnvTestCase):
    def test_webhook_url_appends_path_when_base_only(self) -> None:
        os.environ["POKERBOT_WEBHOOK_PUBLIC_URL"] = "https://example.com"
        os.environ["POKERBOT_WEBHOOK_PATH"] = "/telegram/webhook"

        cfg = Config()

        self.assertEqual(
            cfg.webhook_url,
            "https://example.com/telegram/webhook",
        )
        self.assertTrue(cfg.use_webhook)

    def test_webhook_url_respects_existing_path(self) -> None:
        os.environ["POKERBOT_WEBHOOK_PUBLIC_URL"] = (
            "https://shahin8n.sbs/telegram/webhook"
        )

        cfg = Config()

        self.assertEqual(
            cfg.webhook_url,
            "https://shahin8n.sbs/telegram/webhook",
        )

    def test_webhook_path_normalised(self) -> None:
        os.environ["POKERBOT_WEBHOOK_PUBLIC_URL"] = "https://example.com"
        os.environ["POKERBOT_WEBHOOK_PATH"] = "telegram/webhook"

        cfg = Config()

        self.assertEqual(cfg.WEBHOOK_PATH, "/telegram/webhook")
        self.assertEqual(
            cfg.webhook_url,
            "https://example.com/telegram/webhook",
        )

    def test_webhook_url_handles_nested_base_path(self) -> None:
        os.environ["POKERBOT_WEBHOOK_PUBLIC_URL"] = "https://example.com/bot"

        cfg = Config()

        self.assertEqual(
            cfg.webhook_url,
            "https://example.com/bot/telegram/webhook",
        )

    def test_webhook_url_preserves_query_string(self) -> None:
        os.environ["POKERBOT_WEBHOOK_PUBLIC_URL"] = (
            "https://example.com/telegram/webhook?token=abc"
        )

        cfg = Config()

        self.assertEqual(
            cfg.webhook_url, "https://example.com/telegram/webhook?token=abc"
        )


if __name__ == "__main__":
    unittest.main()
