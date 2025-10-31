"""Menu state tracking infrastructure for poker bot navigation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from .kvstore import RedisKVStore as KVStoreRedis


class MenuLocation(str, Enum):
    """Known menu locations for bot navigation."""

    MAIN_MENU = "main"
    PRIVATE_GAME_SETUP = "private_setup"
    PRIVATE_GAME_VIEW = "private_view"
    STAKE_SELECTION = "stake_select"
    PLAYER_MANAGEMENT = "player_mgmt"
    GROUP_GAME_SETUP = "group_setup"
    GROUP_GAME_VIEW = "group_view"
    SETTINGS = "settings"
    LANGUAGE_SELECT = "lang_select"


@dataclass
class MenuState:
    """Represents a user's current menu navigation state."""

    location: MenuLocation
    parent: Optional[MenuLocation]
    context_data: Dict[str, Any]
    timestamp: float


MENU_HIERARCHY: Dict[MenuLocation, Optional[MenuLocation]] = {
    MenuLocation.PRIVATE_GAME_SETUP: MenuLocation.MAIN_MENU,
    MenuLocation.STAKE_SELECTION: MenuLocation.PRIVATE_GAME_SETUP,
    MenuLocation.PLAYER_MANAGEMENT: MenuLocation.PRIVATE_GAME_VIEW,
    MenuLocation.GROUP_GAME_SETUP: MenuLocation.MAIN_MENU,
    MenuLocation.SETTINGS: MenuLocation.MAIN_MENU,
    MenuLocation.LANGUAGE_SELECT: MenuLocation.SETTINGS,
    MenuLocation.MAIN_MENU: None,
    MenuLocation.PRIVATE_GAME_VIEW: None,
    MenuLocation.GROUP_GAME_VIEW: None,
}


class MenuStateManager:
    """Persist and retrieve menu states for chats."""

    def __init__(self, store: KVStoreRedis) -> None:
        """Initialize manager with backing key-value store."""

        self._store = store

    def _make_key(self, user_id: int, chat_id: int) -> str:
        """Build redis key for storing menu state."""

        return f"menu_state:{user_id}:{chat_id}"

    async def get_state(self, user_id: int, chat_id: int) -> Optional[MenuState]:
        """Retrieve the current :class:`MenuState` for a user/chat."""

        key = self._make_key(user_id, chat_id)
        raw = self._store.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        location = MenuLocation(data["location"])
        parent_value = data.get("parent")
        parent: Optional[MenuLocation] = None
        if parent_value:
            try:
                parent = MenuLocation(parent_value)
            except ValueError:
                parent = None
        context_data = data.get("context_data", {})
        if not isinstance(context_data, dict):
            context_data = {}
        timestamp = float(data.get("timestamp", time.time()))
        return MenuState(
            location=location,
            parent=parent,
            context_data=context_data,
            timestamp=timestamp,
        )

    async def set_state(
        self,
        user_id: int,
        chat_id: int,
        state: MenuState,
    ) -> None:
        """Persist the provided :class:`MenuState` for a user/chat."""

        key = self._make_key(user_id, chat_id)
        payload = json.dumps(
            {
                "location": state.location.value,
                "parent": state.parent.value if state.parent else None,
                "context_data": state.context_data,
                "timestamp": state.timestamp,
            }
        )
        self._store.set(key, payload, ex=3600)

    async def clear_state(self, user_id: int, chat_id: int) -> None:
        """Remove any stored menu state for the user/chat."""

        key = self._make_key(user_id, chat_id)
        self._store.delete(key)

    async def get_parent_location(
        self,
        user_id: int,
        chat_id: int,
    ) -> Optional[MenuLocation]:
        """Return the parent menu location for the stored state."""

        state = await self.get_state(user_id, chat_id)
        return state.parent if state else None


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
