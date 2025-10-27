import json
import unittest
from typing import Optional

from pokerapp.entities import Game, GameState, Player, PlayerState, Wallet
from pokerapp.game_engine import GameEngine, PokerEngine, TurnResult
from pokerapp.kvstore import InMemoryKV


class DummyWallet(Wallet):
    def __init__(self, balance: int = 1_000) -> None:
        self._balance = balance
        self._authorised: dict[str, int] = {}

    @staticmethod
    def _prefix(id: int, suffix: str = ""):
        return ":".join(["wallet", str(id)]) + suffix

    def add_daily(self):  # pragma: no cover - not used in test
        return 0

    def inc(self, amount: int = 0) -> None:
        if self._balance + amount < 0:
            raise ValueError("insufficient funds")
        self._balance += amount

    def inc_authorized_money(self, game_id: str, amount: int) -> None:
        self._authorised[game_id] = self._authorised.get(game_id, 0) + amount

    def authorized_money(self, game_id: str) -> int:
        return self._authorised.get(game_id, 0)

    def authorize(self, game_id: str, amount: int) -> None:
        self.inc_authorized_money(game_id, amount)
        self.inc(-amount)

    def authorize_all(self, game_id: str) -> int:  # pragma: no cover
        amount = self._balance
        self._authorised[game_id] = self._authorised.get(game_id, 0) + amount
        self._balance = 0
        return amount

    def value(self) -> int:
        return self._balance

    def approve(self, game_id: str) -> None:
        self._authorised.pop(game_id, None)


class DummyView:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        self.live_updates: list[tuple[int, int]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent_messages.append((chat_id, text))

    async def send_or_update_private_hand(
        self,
        chat_id: int,
        cards,
        *,
        table_cards=None,
        mention_markdown: Optional[str] = None,
        message_id: Optional[int] = None,
        disable_notification: bool = True,
        footer: Optional[str] = None,
    ) -> Optional[int]:
        self.sent_messages.append((chat_id, "private"))
        return message_id or 456

    async def send_player_turn_with_cards(
        self,
        chat_id: int,
        player,
        game,
        mention: str,
    ) -> None:
        # Legacy compatibility path; should not be used in new flow
        self.live_updates.append((chat_id, player.user_id))

    async def send_or_update_live_message(
        self,
        chat_id: int,
        game,
        current_player,
    ) -> Optional[int]:
        self.live_updates.append((chat_id, current_player.user_id))
        return game.group_message_id


class GameEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_new_hand_deals_cards_and_persists_state(self) -> None:
        kv = InMemoryKV()
        view = DummyView()

        players = [
            Player(
                user_id=1,
                mention_markdown="@alice",
                wallet=DummyWallet(1_000),
                ready_message_id=None,
            ),
            Player(
                user_id=2,
                mention_markdown="@bob",
                wallet=DummyWallet(1_000),
                ready_message_id=None,
            ),
            Player(
                user_id=3,
                mention_markdown="@carol",
                wallet=DummyWallet(1_000),
                ready_message_id=None,
            ),
        ]

        engine = GameEngine(
            game_id="test-game",
            chat_id=42,
            players=players,
            small_blind=10,
            big_blind=20,
            kv_store=kv,
            view=view,
        )

        game = await engine.start_new_hand()

        # All players receive two cards and remain active.
        for player in players:
            self.assertEqual(len(player.cards), 2)
            self.assertEqual(player.state, PlayerState.ACTIVE)

        # Blinds applied to the seats immediately after the dealer.
        self.assertEqual(game.players[0].round_rate, 10)
        self.assertEqual(game.players[1].round_rate, 20)
        self.assertEqual(game.players[0].user_id, players[1].user_id)
        self.assertEqual(game.players[1].user_id, players[2].user_id)

        # Private hands sent and the first turn announced.
        self.assertEqual(len(view.sent_messages), len(players))
        self.assertEqual(len(view.live_updates), 1)
        self.assertEqual(view.live_updates[0][0], 42)
        self.assertEqual(view.live_updates[0][1], players[0].user_id)

        # Game state persisted to Redis-compatible KV store.
        raw_state = kv.get("game_state:test-game")
        self.assertIsNotNone(raw_state)
        state = json.loads(raw_state.decode("utf-8"))

        self.assertEqual(state["state"], GameState.ROUND_PRE_FLOP.name)
        self.assertEqual(state["hand_number"], 1)
        self.assertEqual(len(state["players"]), len(players))
        for snapshot in state["players"]:
            self.assertEqual(len(snapshot["cards"]), 2)

        # The current player should be the seat after the big blind.
        self.assertEqual(state["current_player"], players[0].user_id)

    async def test_heads_up_pre_flop_turn_order(self) -> None:
        kv = InMemoryKV()
        view = DummyView()

        players = [
            Player(
                user_id="1",
                mention_markdown="@alice",
                wallet=DummyWallet(1_000),
                ready_message_id=None,
            ),
            Player(
                user_id="2",
                mention_markdown="@bob",
                wallet=DummyWallet(1_000),
                ready_message_id=None,
            ),
        ]

        engine = GameEngine(
            game_id="heads-up",
            chat_id=42,
            players=players,
            small_blind=10,
            big_blind=20,
            kv_store=kv,
            view=view,
        )

        game = await engine.start_new_hand()

        # Small blind posts first and should act first.
        self.assertEqual(game.current_player_index, 0)
        self.assertEqual(view.live_updates[0][1], game.players[0].user_id)

        coordinator = engine._coordinator

        # Small blind checks/calls and the turn moves to the big blind.
        coordinator.player_call_or_check(game, game.players[0])
        result, next_player = coordinator.process_game_turn(game)
        self.assertEqual(result, TurnResult.CONTINUE_ROUND)
        self.assertIsNotNone(next_player)
        self.assertEqual(next_player.user_id, game.players[1].user_id)

        # Big blind acts and the round should conclude without repeating turns.
        coordinator.player_call_or_check(game, game.players[1])
        result, next_player = coordinator.process_game_turn(game)
        self.assertEqual(result, TurnResult.END_ROUND)
        self.assertIsNone(next_player)


class PokerEngineRoundTests(unittest.TestCase):
    def _create_player(self, user_id: int) -> Player:
        return Player(
            user_id=user_id,
            mention_markdown=f"@player{user_id}",
            wallet=DummyWallet(1_000),
            ready_message_id=None,
        )

    def test_heads_up_post_flop_checks_end_round(self) -> None:
        engine = PokerEngine()
        game = Game()
        players = [self._create_player(1), self._create_player(2)]

        game.players = players
        game.state = GameState.ROUND_PRE_FLOP
        game.dealer_index = 0

        new_state = engine.advance_to_next_street(game)

        self.assertEqual(new_state, GameState.ROUND_FLOP)
        self.assertEqual(game.current_player_index, 0)
        self.assertEqual(game.trading_end_user_id, players[1].user_id)

        result = engine.process_turn(game)
        self.assertEqual(result, TurnResult.CONTINUE_ROUND)
        self.assertEqual(game.current_player_index, 1)

        result = engine.process_turn(game)
        self.assertEqual(result, TurnResult.END_ROUND)

    def test_multiway_post_flop_turn_rotation(self) -> None:
        engine = PokerEngine()
        game = Game()
        players = [
            self._create_player(1),
            self._create_player(2),
            self._create_player(3),
        ]

        game.players = players
        game.state = GameState.ROUND_PRE_FLOP
        game.dealer_index = 2

        new_state = engine.advance_to_next_street(game)

        self.assertEqual(new_state, GameState.ROUND_FLOP)
        self.assertEqual(game.current_player_index, 2)
        self.assertEqual(game.trading_end_user_id, players[2].user_id)

        result = engine.process_turn(game)
        self.assertEqual(result, TurnResult.CONTINUE_ROUND)
        self.assertEqual(game.current_player_index, 0)

        result = engine.process_turn(game)
        self.assertEqual(result, TurnResult.CONTINUE_ROUND)
        self.assertEqual(game.current_player_index, 1)

        result = engine.process_turn(game)
        self.assertEqual(result, TurnResult.CONTINUE_ROUND)
        self.assertEqual(game.current_player_index, 2)

        result = engine.process_turn(game)
        self.assertEqual(result, TurnResult.END_ROUND)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
