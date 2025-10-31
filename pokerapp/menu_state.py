"""Menu state tracking infrastructure for poker bot navigation."""

from __future__ import annotations

import inspect
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .kvstore import RedisKVStore as KVStoreRedis

logger = logging.getLogger(__name__)


class MenuLocation(str, Enum):
    """Known menu locations for bot navigation."""

    MAIN = "main"
    MAIN_MENU = "main"
    PRIVATE_GAME_SETUP = "private_setup"
    PRIVATE_GAME_VIEW = "private_view"
    PRIVATE_GAME_MANAGEMENT = "private_manage"
    PRIVATE_GAME_CREATION = "private_create"
    INVITATIONS = "invitations"
    STAKE_SELECTION = "stake_select"
    PLAYER_MANAGEMENT = "player_mgmt"
    GROUP_GAME_SETUP = "group_setup"
    GROUP_GAME_VIEW = "group_view"
    GROUP_LOBBY = "group_lobby"
    ACTIVE_GAME = "active_game"
    ADMIN_PANEL = "admin_panel"
    SETTINGS = "settings"
    LANGUAGE_SELECT = "lang_select"
    HELP = "help"


@dataclass
class MenuState:
    """Represents a chat's current menu navigation state."""

    chat_id: int
    location: str
    context_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: time.time())

    def __post_init__(self) -> None:  # pragma: no cover - defensive normalization
        if not isinstance(self.context_data, dict):
            self.context_data = {}


MENU_HIERARCHY: Dict[MenuLocation, Optional[MenuLocation]] = {
    MenuLocation.PRIVATE_GAME_SETUP: MenuLocation.MAIN,
    MenuLocation.PRIVATE_GAME_VIEW: MenuLocation.MAIN,
    MenuLocation.PRIVATE_GAME_MANAGEMENT: MenuLocation.MAIN,
    MenuLocation.PRIVATE_GAME_CREATION: MenuLocation.PRIVATE_GAME_MANAGEMENT,
    MenuLocation.STAKE_SELECTION: MenuLocation.PRIVATE_GAME_CREATION,
    MenuLocation.PLAYER_MANAGEMENT: MenuLocation.PRIVATE_GAME_MANAGEMENT,
    MenuLocation.INVITATIONS: MenuLocation.MAIN,
    MenuLocation.GROUP_GAME_SETUP: MenuLocation.GROUP_LOBBY,
    MenuLocation.GROUP_GAME_VIEW: MenuLocation.GROUP_LOBBY,
    MenuLocation.GROUP_LOBBY: MenuLocation.MAIN,
    MenuLocation.ACTIVE_GAME: MenuLocation.GROUP_LOBBY,
    MenuLocation.ADMIN_PANEL: MenuLocation.GROUP_LOBBY,
    MenuLocation.SETTINGS: MenuLocation.MAIN,
    MenuLocation.LANGUAGE_SELECT: MenuLocation.SETTINGS,
    MenuLocation.HELP: MenuLocation.MAIN,
    MenuLocation.MAIN: None,
}


class MenuStateManager:
    """Persist and retrieve menu states for chats."""

    TTL = 3600

    def __init__(self, store: KVStoreRedis) -> None:
        """Initialize manager with backing key-value store."""

        self._store = store
        self._logger = logging.getLogger(__name__)
        self._recovery = MenuStateRecovery(store)

    async def _maybe_await(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    def _make_key(self, chat_id: int) -> str:
        """Build redis key for storing menu state."""

        return f"menu_state:{chat_id}"

    async def get_state(self, chat_id: int) -> Optional[MenuState]:
        """Retrieve the current :class:`MenuState` for a chat with validation."""

        key = self._make_key(chat_id)

        try:
            data = await self._maybe_await(self._store.get(key))
            if data is None:
                return None

            if isinstance(data, bytes):
                data = data.decode("utf-8")

            state_dict = json.loads(data)
            state_dict.setdefault("chat_id", chat_id)
            raw_state = MenuState(**state_dict)

            validated_state = await self._recovery.validate_and_repair(
                chat_id,
                raw_state,
            )

            if validated_state != raw_state and validated_state is not None:
                await self.set_state(validated_state)

            return validated_state

        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            self._logger.error(
                "Failed to parse menu state for chat %d: %s",
                chat_id,
                exc,
            )
            return None

    async def set_state(self, state: MenuState) -> None:
        """Set menu state with detailed logging."""

        key = self._make_key(state.chat_id)
        data = json.dumps(asdict(state))

        await self._maybe_await(self._store.set(key, data, ex=self.TTL))

        self._logger.debug(
            "Menu state updated: chat=%d, location=%s, context_keys=%s",
            state.chat_id,
            state.location,
            list(state.context_data.keys()),
        )

    async def clear_state(self, chat_id: int) -> None:
        """Clear menu state with logging."""

        key = self._make_key(chat_id)
        await self._maybe_await(self._store.delete(key))

        self._logger.info(
            "Menu state cleared for chat %d",
            chat_id,
        )

    async def get_parent_location(self, chat_id: int) -> Optional[MenuLocation]:
        """Return the parent menu location for the stored state."""

        state = await self.get_state(chat_id)
        if not state:
            return None

        try:
            location = MenuLocation(state.location)
        except ValueError:
            return None

        return MENU_HIERARCHY.get(location)


class MenuStateRecovery:
    """Recovery utilities for corrupted or invalid menu states."""

    def __init__(self, kvstore: "KVStoreRedis"):
        self._kvstore = kvstore
        self._logger = logging.getLogger(__name__)

    async def validate_and_repair(
        self,
        chat_id: int,
        current_state: Optional[MenuState],
    ) -> Optional[MenuState]:
        """
        Validate menu state and repair if corrupted.

        Returns:
            Repaired state or None if unrecoverable
        """
        if current_state is None:
            return None

        try:
            MenuLocation(current_state.location)
        except ValueError:
            self._logger.warning(
                "Invalid menu location '%s' for chat %d, resetting to MAIN",
                current_state.location,
                chat_id,
            )
            return MenuState(
                chat_id=chat_id,
                location=MenuLocation.MAIN.value,
                context_data={},
                timestamp=time.time(),
            )

        now = time.time()
        if current_state.timestamp > now + 60:
            self._logger.warning(
                "Future timestamp detected for chat %d, correcting",
                chat_id,
            )
            current_state.timestamp = now

        if now - current_state.timestamp > 86400:
            self._logger.info(
                "Stale menu state for chat %d (age: %d sec), resetting",
                chat_id,
                int(now - current_state.timestamp),
            )
            return None

        if not isinstance(current_state.context_data, dict):
            self._logger.warning(
                "Invalid context_data type for chat %d, resetting to empty dict",
                chat_id,
            )
            current_state.context_data = {}

        return current_state

    async def cleanup_orphaned_states(self) -> int:
        """
        Remove menu states for inactive chats (older than 7 days).

        Returns:
            Number of states cleaned up
        """
        self._logger.info("Orphaned state cleanup not yet implemented")
        return 0


def get_breadcrumb_path(location: MenuLocation) -> List[MenuLocation]:
    """Return menu path from root to the provided location."""

    path: List[MenuLocation] = []
    current: Optional[MenuLocation] = location
    visited: set[MenuLocation] = set()
    while current is not None and current not in visited:
        path.append(current)
        visited.add(current)
        current = MENU_HIERARCHY.get(current)
    path.reverse()
    return path
