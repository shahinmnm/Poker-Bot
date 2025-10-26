#!/usr/bin/env python3

"""Handles stake selection, player invites, and private chat game state."""

import asyncio
import datetime
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from pokerapp.entities import ChatId, StakeConfig, STAKE_PRESETS, UserId
from pokerapp.kvstore import ensure_kv

logger = logging.getLogger(__name__)


DEFAULT_WALLET_BALANCE = 1000


class PrivateGameState(Enum):
    """Private game session states."""

    WAITING_FOR_STAKE = "waiting_for_stake"
    WAITING_FOR_PLAYERS = "waiting_for_players"
    READY_TO_START = "ready_to_start"
    LOBBY = "lobby"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"


@dataclass
class PrivateGameInvite:
    """Represents an invitation for a private game lobby."""

    user_id: UserId
    username: str
    invited_at: int
    accepted: bool = False
    accepted_at: Optional[int] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "invited_at": self.invited_at,
            "accepted": self.accepted,
            "accepted_at": self.accepted_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "PrivateGameInvite":
        return cls(
            user_id=int(data["user_id"]),
            username=str(data.get("username", "")),
            invited_at=int(data.get("invited_at", 0)),
            accepted=bool(data.get("accepted", False)),
            accepted_at=(
                int(data["accepted_at"])
                if data.get("accepted_at") is not None
                else None
            ),
        )


@dataclass
class PrivateGame:
    """Serializable private game lobby stored in Redis."""

    game_code: str
    host_user_id: UserId
    stake_level: str
    state: PrivateGameState = PrivateGameState.LOBBY
    players: List[UserId] = field(default_factory=list)
    invited_players: Dict[UserId, PrivateGameInvite] = field(
        default_factory=dict
    )
    created_at: int = field(
        default_factory=lambda: int(datetime.datetime.utcnow().timestamp())
    )

    def to_json(self) -> str:
        return json.dumps(
            {
                "game_code": self.game_code,
                "host_user_id": self.host_user_id,
                "stake_level": self.stake_level,
                "state": self.state.value,
                "created_at": self.created_at,
                "players": [int(player_id) for player_id in self.players],
                "invited_players": {
                    str(user_id): invite.to_dict()
                    for user_id, invite in self.invited_players.items()
                },
            }
        )

    @classmethod
    def from_json(cls, json_data: str) -> "PrivateGame":
        payload = json.loads(json_data)
        raw_invites = payload.get("invited_players", {})
        invited_players = {
            int(user_id): PrivateGameInvite.from_dict(invite_data)
            for user_id, invite_data in raw_invites.items()
        }
        players = [int(player_id) for player_id in payload.get("players", [])]
        state_value = payload.get("state", PrivateGameState.LOBBY.value)
        return cls(
            game_code=str(payload["game_code"]),
            host_user_id=int(payload["host_user_id"]),
            stake_level=str(payload["stake_level"]),
            state=PrivateGameState(state_value),
            players=players,
            invited_players=invited_players,
            created_at=int(payload.get("created_at", 0)),
        )


@dataclass
class PlayerInvite:
    """Represents a player invitation."""

    user_id: UserId
    username: str
    invited_at: datetime.datetime
    accepted: bool = False


@dataclass
class PrivateGameSession:
    """
    Manages private game sessions in DM with the bot.
    Q9: Allows stake selection before game start.
    """

    host_user_id: UserId
    chat_id: ChatId
    created_at: datetime.datetime = field(
        default_factory=datetime.datetime.now
    )
    state: PrivateGameState = PrivateGameState.WAITING_FOR_STAKE

    # Stake configuration
    stake_config: Optional[StakeConfig] = None
    custom_stake_enabled: bool = False

    # Player management
    invited_players: Dict[UserId, PlayerInvite] = field(default_factory=dict)
    ready_players: Set[UserId] = field(default_factory=set)

    # Game settings
    max_players: int = 8
    min_players: int = 2
    auto_start: bool = False

    def set_stake(self, stake_level: str) -> bool:
        """
        Set stake configuration for the game.

        Args:
            stake_level: Key from STAKE_PRESETS or 'custom'

        Returns:
            True if stake was set successfully
        """
        if stake_level == "custom":
            self.custom_stake_enabled = True
            return True

        if stake_level not in STAKE_PRESETS:
            logger.warning(
                "Invalid stake level %s for private game %s",
                stake_level,
                self.chat_id,
            )
            return False

        self.stake_config = STAKE_PRESETS[stake_level]
        self.state = PrivateGameState.WAITING_FOR_PLAYERS
        logger.info(
            "Stake set to %s for private game %s",
            stake_level,
            self.chat_id,
        )
        return True

    def set_custom_stake(
        self,
        small_blind: int,
        big_blind: int,
        min_buy_in: int,
    ) -> bool:
        """
        Set custom stake amounts.

        Args:
            small_blind: Small blind amount
            big_blind: Big blind amount (must be 2x small blind)
            min_buy_in: Minimum buy-in (recommended 20x big blind)

        Returns:
            True if custom stake was valid
        """
        if big_blind != 2 * small_blind:
            logger.warning("Invalid big blind: must be 2x small blind")
            return False

        if min_buy_in < 20 * big_blind:
            logger.warning(
                "Min buy-in too low: should be at least 20x big blind",
            )
            return False

        stake = StakeConfig(
            small_blind=small_blind,
            name="Custom",
            min_buy_in=min_buy_in,
        )
        stake.big_blind = big_blind
        stake.default_buy_in = min_buy_in
        stake.max_buy_in = min_buy_in * 5
        self.stake_config = stake
        self.state = PrivateGameState.WAITING_FOR_PLAYERS
        logger.info(
            "Custom stake set for private game %s: SB=%s BB=%s",
            self.chat_id,
            small_blind,
            big_blind,
        )
        return True

    def invite_player(self, user_id: UserId, username: str) -> bool:
        """
        Invite a player to the private game.

        Args:
            user_id: Telegram user ID
            username: Display name

        Returns:
            True if invitation was sent
        """
        if len(self.invited_players) >= self.max_players - 1:  # -1 for host
            logger.warning("Private game %s is full", self.chat_id)
            return False

        if user_id in self.invited_players:
            logger.debug(
                "Player %s already invited to %s",
                user_id,
                self.chat_id,
            )
            return False

        self.invited_players[user_id] = PlayerInvite(
            user_id=user_id,
            username=username,
            invited_at=datetime.datetime.now(),
        )
        logger.info(
            "Player %s invited to private game %s",
            user_id,
            self.chat_id,
        )
        return True

    def accept_invite(self, user_id: UserId) -> bool:
        """
        Player accepts invitation.

        Args:
            user_id: User accepting invite

        Returns:
            True if acceptance was successful
        """
        if user_id not in self.invited_players:
            logger.warning(
                "No invite found for user %s in game %s",
                user_id,
                self.chat_id,
            )
            return False

        invite = self.invited_players[user_id]
        if invite.accepted:
            logger.debug(
                "User %s already accepted invite to %s",
                user_id,
                self.chat_id,
            )
            return False

        invite.accepted = True
        self.ready_players.add(user_id)

        # Check if we can start
        if self.can_start():
            self.state = PrivateGameState.READY_TO_START

        logger.info(
            "User %s accepted invite to private game %s",
            user_id,
            self.chat_id,
        )
        return True

    def can_start(self) -> bool:
        """Check if game has minimum players and stake configured."""
        if self.stake_config is None:
            return False

        accepted_count = sum(
            1
            for inv in self.invited_players.values()
            if inv.accepted
        )
        total_players = accepted_count + 1  # +1 for host

        return total_players >= self.min_players

    def get_accepted_players(self) -> List[UserId]:
        """Get list of user IDs who accepted invites."""
        return [
            inv.user_id
            for inv in self.invited_players.values()
            if inv.accepted
        ]

    def start_game(self) -> bool:
        """
        Mark game as in progress.

        Returns:
            True if game started successfully
        """
        if not self.can_start():
            logger.warning(
                "Cannot start private game %s: not ready",
                self.chat_id,
            )
            return False

        self.state = PrivateGameState.IN_PROGRESS
        logger.info("Private game %s started", self.chat_id)
        return True


class PrivateGameManager:
    """
    Manages multiple private game sessions.
    Stores session state in memory (will use Redis in Phase 3).
    """

    def __init__(self):
        self._sessions: Dict[ChatId, PrivateGameSession] = {}
        logger.info("PrivateGameManager initialized")

    def create_session(
        self,
        host_user_id: UserId,
        chat_id: ChatId,
    ) -> PrivateGameSession:
        """Create new private game session."""
        session = PrivateGameSession(
            host_user_id=host_user_id,
            chat_id=chat_id,
        )
        self._sessions[chat_id] = session
        logger.info(
            "Created private game session for host %s in chat %s",
            host_user_id,
            chat_id,
        )
        return session

    def get_session(self, chat_id: ChatId) -> Optional[PrivateGameSession]:
        """Get existing session by chat ID."""
        return self._sessions.get(chat_id)

    def remove_session(self, chat_id: ChatId) -> None:
        """Remove session after game ends."""
        if chat_id in self._sessions:
            del self._sessions[chat_id]
            logger.info("Removed private game session for chat %s", chat_id)

    def get_user_sessions(self, user_id: UserId) -> List[PrivateGameSession]:
        """Get all sessions where user is host or invited."""
        sessions = []
        for session in self._sessions.values():
            if (
                session.host_user_id == user_id
                or user_id in session.invited_players
            ):
                sessions.append(session)
        return sessions


class PrivateGameModel:
    """Lightweight player registry used for public/private lobby flows."""

    def __init__(self, kvstore, logger: logging.Logger):
        self._kv = ensure_kv(kvstore)
        self._logger = logger
        self._chat_players: Dict[int, Set[int]] = {}

    async def register_player(self, chat_id: int, user_id: int, view) -> bool:
        """Register a player for a chat lobby and ensure wallet exists."""

        players = self._chat_players.setdefault(chat_id, set())
        if user_id in players:
            return False

        try:
            await asyncio.to_thread(self._ensure_wallet, user_id)
        except Exception as exc:  # pragma: no cover - Redis failure
            self._logger.error(
                "Failed to create wallet for user %s in chat %s: %s",
                user_id,
                chat_id,
                exc,
            )
            return False

        players.add(user_id)
        return True

    def get_registered_players(self, chat_id: int) -> Set[int]:
        """Return registered player IDs for a chat lobby."""

        return set(self._chat_players.get(chat_id, set()))

    def _ensure_wallet(self, user_id: int) -> None:
        key = "pokerbot:" + str(user_id)
        setnx = getattr(self._kv, "setnx", None)
        if callable(setnx):
            setnx(key, DEFAULT_WALLET_BALANCE)
            return

        if self._kv.get(key) is None:
            self._kv.set(key, DEFAULT_WALLET_BALANCE)
