#!/usr/bin/env python3

from abc import abstractmethod
import enum
import datetime
from typing import Tuple, List, Optional, Literal, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4
from pokerapp.cards import get_cards


MessageId = str
ChatId = str
UserId = str
Mention = str
Score = int
Money = int


@abstractmethod
class Wallet:
    @staticmethod
    def _prefix(id: int, suffix: str = ""):
        pass

    def add_daily(self) -> Money:
        pass

    def inc(self, amount: Money = 0) -> None:
        pass

    def inc_authorized_money(self, game_id: str, amount: Money) -> None:
        pass

    def authorized_money(self, game_id: str) -> Money:
        pass

    def authorize(self, game_id: str, amount: Money) -> None:
        pass

    def authorize_all(self, game_id: str) -> Money:
        pass

    def value(self) -> Money:
        pass

    def approve(self, game_id: str) -> None:
        pass


class Player:
    def __init__(
        self,
        user_id: UserId,
        mention_markdown: Mention,
        wallet: Wallet,
        ready_message_id: Optional[MessageId],
    ):
        self.user_id = user_id
        self.mention_markdown = mention_markdown
        self.state = PlayerState.ACTIVE
        self.wallet = wallet
        self.cards = []
        self.round_rate = 0
        self.ready_message_id = ready_message_id

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, self.__dict__)


class PlayerState(enum.Enum):
    ACTIVE = 1
    FOLD = 0
    ALL_IN = 10


class GameMode(Enum):
    """Game mode: group chat vs private chat."""
    GROUP = "group"
    PRIVATE = "private"


@dataclass
class Game:
    group_message_id: Optional[int] = None
    recent_actions: List[str] = field(default_factory=list)

    def __init__(self):
        self.reset()

    def reset(self, rotate_dealer: bool = False):
        previous_players = getattr(self, "players", [])
        if rotate_dealer and previous_players:
            next_dealer_index = (self.dealer_index + 1) % len(previous_players)
        else:
            next_dealer_index = 0

        self.id = str(uuid4())
        self.pot = 0
        self.max_round_rate = 0
        self.state = GameState.INITIAL
        self.players: List[Player] = []
        self.cards_table = []
        self.current_player_index = -1
        self.remain_cards = get_cards()
        self.trading_end_user_id = 0
        self.closer_has_acted = False
        # Track the nominal dealer button position so it can rotate between
        # games. Public games currently infer the button from blind
        # assignments, but multi-hand sessions may rely on this field.
        self.dealer_index = next_dealer_index
        self.table_stake = 0  # Small blind amount for this game
        self.ready_users = set()
        self.last_turn_time = datetime.datetime.now()
        # Game mode (Phase 2)
        self.mode: GameMode = GameMode.GROUP
        self.stake_config: Optional[StakeConfig] = None
        self.group_message_id = None
        self.recent_actions = []
        self.round_has_started = False
        # Version number used to invalidate stale inline keyboard callbacks.
        self.live_message_version = 0

    def set_mode_from_chat(self, chat_type: str) -> None:
        """Configure game mode using the provided Telegram ``chat_type``.

        Args:
            chat_type: Telegram chat type string (``"private"``, ``"group"``,
                or ``"supergroup"``).

        Raises:
            TypeError: If *chat_type* is not a string.
            ValueError: If *chat_type* is not a supported Telegram chat type.
        """

        if not isinstance(chat_type, str):
            raise TypeError("chat_type must be a string")

        normalized = chat_type.strip().lower()
        if normalized in ("group", "supergroup"):
            self.mode = GameMode.GROUP
            # Stakes only apply to private games – clear any previous config.
            self.stake_config = None
        elif normalized == "private":
            self.mode = GameMode.PRIVATE
        else:
            raise ValueError(f"Unsupported chat type: {chat_type!r}")

    def players_by(self, states: Tuple[PlayerState]) -> List[Player]:
        return list(filter(lambda p: p.state in states, self.players))

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, self.__dict__)

    def add_action(self, action_text: str) -> None:
        """Record a human-readable action and keep the last three entries."""

        action = action_text.strip()
        if not action:
            return

        self.recent_actions.append(action)
        if len(self.recent_actions) > 3:
            self.recent_actions.pop(0)

    def set_group_message(self, message_id: int) -> None:
        """Store the persistent live message identifier."""

        self.group_message_id = message_id

    def has_group_message(self) -> bool:
        """Return ``True`` when a live message has already been created."""

        return self.group_message_id is not None

    def get_live_message_version(self) -> int:
        """Return the current live message version counter."""

        return getattr(self, "live_message_version", 0)

    def next_live_message_version(self) -> int:
        """Return the version number used for the next live message update."""

        return self.get_live_message_version() + 1

    def mark_live_message_version(self, version: int) -> None:
        """Persist the provided live message version counter."""

        if version < 0:
            version = 0

        self.live_message_version = version

    def get_recent_actions_text(self) -> str:
        """Return a bulleted list summarizing the most recent actions."""

        if not self.recent_actions:
            return "No actions yet."

        return "\n".join(f"• {action}" for action in self.recent_actions)


class GameState(enum.Enum):
    INITIAL = 0
    ROUND_PRE_FLOP = 1  # No cards on the table.
    ROUND_FLOP = 2  # Three cards.
    ROUND_TURN = 3  # Four cards.
    ROUND_RIVER = 4  # Five cards.
    FINISHED = 5  # The end.


class PlayerAction(enum.Enum):
    CHECK = "check"
    CALL = "call"
    FOLD = "fold"
    RAISE_RATE = "raise rate"
    BET = "bet"
    ALL_IN = "all in"
    SMALL = 10
    NORMAL = 25
    BIG = 50


class UserException(Exception):
    pass


class StakeConfig:
    """Q9: Stake configuration for private games"""

    def __init__(self, small_blind: int, name: str, min_buy_in: int):
        self.small_blind = small_blind
        self.big_blind = small_blind * 2
        self.name = name
        self.min_buy_in = min_buy_in  # 20 big blinds

    def __repr__(self):
        return (
            "StakeConfig("
            f"{self.name}: {self.small_blind}/{self.big_blind}, "
            f"min: {self.min_buy_in})"
        )


class BalanceValidator:
    """Q7: Balance validation utilities"""

    @staticmethod
    def can_afford_table(balance: int, stake_config: 'StakeConfig') -> bool:
        """Check if player can afford minimum buy-in"""
        return balance >= stake_config.min_buy_in

    @staticmethod
    def can_afford_bet(balance: int, bet_amount: int) -> bool:
        """Check if player can afford specific bet"""
        return balance >= bet_amount


@dataclass
class MenuContext:
    """Context information for building chat-appropriate menus."""

    chat_id: int
    chat_type: Literal["private", "group", "supergroup"]
    user_id: int
    language_code: str = "en"
    current_menu_location: Optional[str] = None
    menu_context_data: Dict[str, Any] = None

    # User state
    in_active_game: bool = False
    is_game_host: bool = False
    has_pending_invite: bool = False

    # Group-specific
    group_has_active_game: bool = False
    user_is_group_admin: bool = False

    # Private-specific
    active_private_game_code: Optional[str] = None

    def __post_init__(self) -> None:
        """Ensure optional context containers are initialized."""

        if self.menu_context_data is None:
            self.menu_context_data = {}

    def is_private_chat(self) -> bool:
        """Check if this is a private (1-on-1) conversation."""

        return self.chat_type == "private"

    def is_group_chat(self) -> bool:
        """Check if this is a group or supergroup."""

        return self.chat_type in ("group", "supergroup")

    def can_access_group_commands(self) -> bool:
        """Determine if group-specific commands should be visible."""

        return self.is_group_chat()

    def can_access_private_commands(self) -> bool:
        """Determine if private game commands should be visible."""

        return self.is_private_chat()

    def get_context_value(self, key: str, default: Any = None) -> Any:
        """Retrieve value from menu context data."""

        return self.menu_context_data.get(key, default)

    def has_back_navigation(self) -> bool:
        """Check if back button should be shown."""

        if self.current_menu_location is None:
            return False

        from .menu_state import MenuLocation, MENU_HIERARCHY

        try:
            location = MenuLocation(self.current_menu_location)
            parent = MENU_HIERARCHY.get(location)
            return parent is not None
        except (ValueError, KeyError):
            return False


# Q9: Predefined stake levels for private games
STAKE_PRESETS = {
    "micro": StakeConfig(
        small_blind=5,
        name="Micro (5/10)",
        min_buy_in=200,
    ),
    "low": StakeConfig(
        small_blind=10,
        name="Low (10/20)",
        min_buy_in=400,
    ),
    "medium": StakeConfig(
        small_blind=25,
        name="Medium (25/50)",
        min_buy_in=1000,
    ),
    "high": StakeConfig(
        small_blind=50,
        name="High (50/100)",
        min_buy_in=2000,
    ),
    "premium": StakeConfig(
        small_blind=100,
        name="Premium (100/200)",
        min_buy_in=4000,
    ),
}
