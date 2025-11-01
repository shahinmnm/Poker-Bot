"""Group poker lobby management utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from pokerapp.i18n import translation_manager
from pokerapp.kvstore import ensure_kv


@dataclass
class GroupLobbyState:
    """Track lobby metadata for a group chat."""

    chat_id: int
    message_id: Optional[int] = None
    seated_players: Set[int] = field(default_factory=set)
    player_names: Dict[int, str] = field(default_factory=dict)

    def add_player(self, user_id: int) -> bool:
        """Add player to lobby; return True if newly added."""

        if user_id in self.seated_players:
            return False

        self.seated_players.add(user_id)
        return True

    def remove_player(self, user_id: int) -> bool:
        """Remove player; return True if they were seated."""

        if user_id not in self.seated_players:
            return False

        self.seated_players.remove(user_id)
        self.player_names.pop(user_id, None)
        return True

    def has_player(self, user_id: int) -> bool:
        """Check if user currently seated in lobby."""

        return user_id in self.seated_players

    def player_count(self) -> int:
        """Number of seated players."""

        return len(self.seated_players)

    def can_start_game(self) -> bool:
        """Return True when enough players present to start."""

        return self.player_count() >= 2


class GroupLobbyManager:
    """Manage lobby messages and persistence for group games."""

    def __init__(self, bot, kvstore, logger: logging.Logger):
        self._bot = bot
        self._kv = ensure_kv(kvstore)
        self._logger = logger
        self._lobbies: Dict[int, GroupLobbyState] = {}

    def has_lobby(self, chat_id: int) -> bool:
        """Return True if lobby exists in memory."""

        return chat_id in self._lobbies

    def get_seated_players(self, chat_id: int) -> Set[int]:
        """Return copy of seated player IDs for chat."""

        lobby = self._lobbies.get(chat_id)
        if not lobby:
            lobby = self._restore_lobby(chat_id)
        return set(lobby.seated_players) if lobby else set()

    def get_or_create_lobby(self, chat_id: int) -> GroupLobbyState:
        """Return in-memory lobby state, restoring from Redis if needed."""

        if chat_id in self._lobbies:
            return self._lobbies[chat_id]

        lobby = self._restore_lobby(chat_id)
        if not lobby:
            lobby = GroupLobbyState(chat_id=chat_id)
            self._lobbies[chat_id] = lobby
        return lobby

    async def add_player(
        self, chat_id: int, user_id: int, user_name: str
    ) -> GroupLobbyState:
        """Add player to lobby, persist, and update message."""

        lobby = self.get_or_create_lobby(chat_id)
        added = lobby.add_player(user_id)
        lobby.player_names[user_id] = user_name
        self._logger.info(
            "Adding player %s to lobby %s (already_present=%s)",
            user_id,
            chat_id,
            not added,
        )
        await self._send_or_update_lobby(chat_id, lobby)
        self._save_lobby_state(lobby)
        return lobby

    async def remove_player(self, chat_id: int, user_id: int) -> None:
        """Remove a player; delete lobby when empty."""

        lobby = self._lobbies.get(chat_id)
        if not lobby:
            lobby = self._restore_lobby(chat_id)

        if not lobby:
            self._logger.debug(
                "Attempted to remove player %s from missing lobby %s",
                user_id,
                chat_id,
            )
            return

        removed = lobby.remove_player(user_id)
        self._logger.info(
            "Removing player %s from lobby %s (removed=%s)",
            user_id,
            chat_id,
            removed,
        )

        if lobby.player_count() == 0:
            await self.delete_lobby(chat_id)
            return

        await self._send_or_update_lobby(chat_id, lobby)
        self._save_lobby_state(lobby)

    async def delete_lobby(self, chat_id: int) -> None:
        """Remove lobby message and Redis state."""

        lobby = self._lobbies.pop(chat_id, None)
        self._kv.delete("lobby:" + str(chat_id))

        if not lobby or not lobby.message_id:
            return

        try:
            await self._bot.delete_message(
                chat_id=chat_id,
                message_id=lobby.message_id,
            )
        except TelegramError as exc:  # pragma: no cover - network side effects
            self._logger.warning(
                "Failed to delete lobby message for %s: %s",
                chat_id,
                exc,
            )

    async def _send_or_update_lobby(
        self, chat_id: int, lobby: GroupLobbyState
    ) -> None:
        """Send or edit lobby message with current players."""

        language_code = (
            self._kv.get_chat_language(chat_id)
            or translation_manager.DEFAULT_LANGUAGE
        )
        translator = translation_manager.get_translator(language_code)

        text = self._format_lobby_message(lobby, translator)
        keyboard = self._build_lobby_keyboard(lobby, translator)

        if lobby.message_id:
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=lobby.message_id,
                    text=text,
                    reply_markup=keyboard,
                )
                return
            except TelegramError as exc:  # pragma: no cover - Telegram API
                self._logger.warning(
                    (
                        "Failed to edit lobby message %s in chat %s: %s. "
                        "Recreating."
                    ),
                    lobby.message_id,
                    chat_id,
                    exc,
                )
                lobby.message_id = None

        try:
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )
            lobby.message_id = message.message_id
            self._lobbies[chat_id] = lobby
        except TelegramError as exc:  # pragma: no cover - Telegram API
            self._logger.error(
                "Failed to send lobby message to chat %s: %s",
                chat_id,
                exc,
            )

    def _format_lobby_message(
        self,
        lobby: GroupLobbyState,
        translator,
    ) -> str:
        """Return formatted lobby message text."""

        lines = [
            translator("group_lobby.title"),
            "",
            translator("group_lobby.players_header"),
        ]

        if lobby.seated_players:
            for user_id in sorted(lobby.seated_players):
                name = lobby.player_names.get(user_id, str(user_id))
                lines.append(
                    translator("group_lobby.player_entry", name=name)
                )
        else:
            lines.append(translator("group_lobby.no_players"))

        total = lobby.player_count()
        lines.append("")
        lines.append(
            translator("group_lobby.total_players", count=total)
        )

        if lobby.can_start_game():
            lines.append(
                translator(
                    "group_lobby.status.ready",
                    min_players=2,
                )
            )
        else:
            lines.append(
                translator(
                    "group_lobby.status.waiting",
                    min_players=2,
                )
            )

        lines.append("")
        lines.append(translator("group_lobby.footer.manage"))

        return "\n".join(lines)

    def _build_lobby_keyboard(
        self,
        lobby: GroupLobbyState,
        translator,
    ) -> InlineKeyboardMarkup:
        """Construct inline keyboard for lobby controls."""

        buttons = [
            [
                InlineKeyboardButton(
                    translator("group_lobby.buttons.sit"),
                    callback_data="lobby_sit",
                ),
                InlineKeyboardButton(
                    translator("group_lobby.buttons.leave"),
                    callback_data="lobby_leave",
                ),
            ]
        ]

        if lobby.can_start_game():
            buttons.append(
                [
                    InlineKeyboardButton(
                        translator("group_lobby.buttons.start"),
                        callback_data="lobby_start",
                    )
                ]
            )

        return InlineKeyboardMarkup(buttons)

    def _save_lobby_state(self, lobby: GroupLobbyState) -> None:
        """Persist lobby state to Redis with TTL."""

        payload = {
            "message_id": lobby.message_id,
            "players": list(lobby.seated_players),
            "player_names": lobby.player_names,
        }
        try:
            self._kv.set(
                "lobby:" + str(lobby.chat_id),
                json.dumps(payload),
                ex=3600,
            )
        except Exception as exc:  # pragma: no cover - kvstore errors
            self._logger.error(
                "Failed to persist lobby for chat %s: %s",
                lobby.chat_id,
                exc,
            )

    def _restore_lobby(self, chat_id: int) -> Optional[GroupLobbyState]:
        """Restore lobby state from Redis if available."""

        try:
            raw = self._kv.get("lobby:" + str(chat_id))
        except Exception as exc:  # pragma: no cover - kvstore errors
            self._logger.error(
                "Failed to load lobby for chat %s: %s",
                chat_id,
                exc,
            )
            return None

        if not raw:
            return None

        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            self._logger.error(
                "Invalid lobby payload for chat %s: %s",
                chat_id,
                exc,
            )
            return None

        lobby = GroupLobbyState(chat_id=chat_id)
        lobby.message_id = data.get("message_id")
        lobby.seated_players = set(map(int, data.get("players", [])))
        lobby.player_names = {
            int(user_id): name
            for user_id, name in data.get("player_names", {}).items()
        }
        self._lobbies[chat_id] = lobby
        return lobby
