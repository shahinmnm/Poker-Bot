"""Tests for language switching workflows using mocked Telegram components."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from telegram import Bot

from pokerapp.config import Config
from pokerapp.entities import Game
from pokerapp.kvstore import InMemoryKV
from pokerapp.pokerbotmodel import KEY_CHAT_DATA_GAME, PokerBotModel


def _build_model(chat_data: dict) -> PokerBotModel:
    """Create a PokerBotModel instance wired with lightweight test doubles."""

    cfg = Config()
    kv_store = InMemoryKV()

    bot = AsyncMock(spec=Bot)
    view = MagicMock()
    application = MagicMock()
    application.chat_data = chat_data

    model = PokerBotModel(
        view=view,
        bot=bot,
        cfg=cfg,
        kv=kv_store,
        application=application,
    )

    model._coordinator._send_or_update_game_state = AsyncMock()  # type: ignore[attr-defined]
    return model


def _make_game_with_player(user_id: int, *, current_index: int = 0) -> Game:
    """Return a Game stub whose current player has the provided ``user_id``."""

    game = Game()
    players = [SimpleNamespace(user_id=user_id), SimpleNamespace(user_id=user_id + 1)]
    game.players = players
    game.current_player_index = current_index
    return game


def test_refresh_language_updates_active_games() -> None:
    """Refreshing a user's language should trigger UI updates for their games."""

    target_user = 42
    game_with_user = _make_game_with_player(target_user, current_index=0)
    another_game = _make_game_with_player(99, current_index=1)

    chat_data = {
        100: {KEY_CHAT_DATA_GAME: game_with_user},
        "200": {KEY_CHAT_DATA_GAME: another_game},  # User not part of this game
        300: {},
        400: {KEY_CHAT_DATA_GAME: "not-a-game"},
    }

    model = _build_model(chat_data)

    asyncio.run(model.refresh_language_for_user(target_user))

    send_mock = model._coordinator._send_or_update_game_state  # type: ignore[attr-defined]
    assert send_mock.await_count == 1

    call = send_mock.await_args
    _, kwargs = call

    assert kwargs["chat_id"] == 100
    assert kwargs["game"] is game_with_user
    assert kwargs["current_player"] is game_with_user.players[0]


def test_refresh_language_handles_missing_games_gracefully() -> None:
    """If no games reference the user, refresh should be a no-op."""

    chat_data = {
        101: {KEY_CHAT_DATA_GAME: _make_game_with_player(77)},
        "orphan": {},
        303: None,
    }

    model = _build_model(chat_data)

    asyncio.run(model.refresh_language_for_user(2024))

    send_mock = model._coordinator._send_or_update_game_state  # type: ignore[attr-defined]
    assert send_mock.await_count == 0


def test_apply_user_language_prefers_chat_setting() -> None:
    """Group chats should use the stored chat language over user defaults."""

    model = _build_model({})

    model._kv.set_chat_language(-123, "ru")

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-123, type="group"),
        effective_user=SimpleNamespace(id=7, language_code="es"),
        effective_message=SimpleNamespace(message_id=1),
    )

    resolved = model._apply_user_language(update)

    assert resolved == "ru"
    model._view.set_language_context.assert_called_with("ru", user_id=7)


def test_apply_user_language_stores_chat_default_when_missing() -> None:
    """When a group has no stored language, use the user's choice and persist it."""

    model = _build_model({})

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-555, type="group"),
        effective_user=SimpleNamespace(id=9, language_code="es"),
        effective_message=SimpleNamespace(message_id=2),
    )

    resolved = model._apply_user_language(update)

    assert resolved == "es"
    assert model._kv.get_chat_language(-555) == "es"
    model._view.set_language_context.assert_called_with("es", user_id=9)
