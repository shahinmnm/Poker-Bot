#!/usr/bin/env python3

from abc import abstractmethod
import enum
import datetime
from typing import Tuple, List, Optional
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


class Game:
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

    def players_by(self, states: Tuple[PlayerState]) -> List[Player]:
        return list(filter(lambda p: p.state in states, self.players))

    def __repr__(self):
        return "{}({!r})".format(self.__class__.__name__, self.__dict__)


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
