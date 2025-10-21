import json
import unittest

from pokerapp.entities import GameState, Player, PlayerState, Wallet
from pokerapp.game_engine import GameEngine
from pokerapp.kvstore import InMemoryKV


class DummyWallet(Wallet):
    def __init__(self, balance: int = 1_000) -> None:
        self._balance = balance
        self._authorised: dict[str, int] = {}

    @staticmethod
    def _prefix(id: int, suffix: str = ""):
        return f"wallet:{id}{suffix}"

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

    def authorize_all(self, game_id: str) -> int:  # pragma: no cover - not used
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
        self.turn_prompts: list[tuple[int, int, int]] = []
        self.table_updates: list[tuple[int, list[str]]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent_messages.append((chat_id, text))

    async def send_turn_actions(self, chat_id: int, game, player, money: int) -> None:
        self.turn_prompts.append((chat_id, player.user_id, money))

    async def send_desk_cards_img(self, chat_id: int, cards, caption: str = "") -> None:
        self.table_updates.append((chat_id, list(cards)))


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

        # Blinds applied to the first two players.
        self.assertEqual(players[0].round_rate, 10)
        self.assertEqual(players[1].round_rate, 20)

        # Private hands sent and the first turn announced.
        self.assertEqual(len(view.sent_messages), len(players))
        self.assertEqual(len(view.turn_prompts), 1)
        self.assertEqual(view.turn_prompts[0][0], 42)
        self.assertEqual(view.turn_prompts[0][1], players[2].user_id)

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
        self.assertEqual(state["current_player"], players[2].user_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
