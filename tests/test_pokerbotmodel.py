#!/usr/bin/env python3

import unittest
from types import SimpleNamespace
from typing import Dict, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import redis
from telegram import Bot

from pokerapp.cards import Cards, Card
from pokerapp.config import Config
from pokerapp.entities import (
    Game,
    GameState,
    Money,
    Player,
    PlayerState,
    Score,
)
from pokerapp.game_coordinator import GameCoordinator
from pokerapp.game_engine import TurnResult
from pokerapp.kvstore import InMemoryKV
from pokerapp.pokerbotmodel import (
    KEY_CHAT_DATA_GAME,
    PokerBotModel,
    WalletManagerModel,
)


def with_cards(p: Player) -> Tuple[Player, Cards]:
    return (p, [Card("6♥"), Card("A♥"), Card("A♣"), Card("A♠")])


class DummyWinnerDetermination:
    def __init__(self):
        self._scores: Dict[Score, Tuple[Tuple[Player, Cards], ...]] = {}

    def set_scores(
        self,
        scores: Dict[Score, Tuple[Tuple[Player, Cards], ...]],
    ) -> None:
        self._scores = scores

    def determinate_scores(self, players, cards_table):
        return {score: list(pairs) for score, pairs in self._scores.items()}


class TestGameCoordinatorPayouts(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        super(TestGameCoordinatorPayouts, self).__init__(*args, **kwargs)
        self._user_id = 0
        self._coordinator = GameCoordinator()
        self._winner_stub = DummyWinnerDetermination()
        self._coordinator.winner_determine = self._winner_stub
        cfg: Config = Config()
        password = cfg.REDIS_PASS or None
        self._kv = redis.Redis(
            host=cfg.REDIS_HOST,
            port=cfg.REDIS_PORT,
            db=cfg.REDIS_DB,
            password=password,
        )

    def _next_player(self, game: Game, autorized: Money) -> Player:
        self._user_id += 1
        wallet_manager = WalletManagerModel(self._user_id, kv=self._kv)
        wallet_manager.authorize_all("clean_wallet_game")
        wallet_manager.inc(autorized)
        wallet_manager.authorize(game.id, autorized)
        game.pot += autorized
        p = Player(
            user_id=self._user_id,
            mention_markdown="@test",
            wallet=wallet_manager,
            ready_message_id="",
        )
        game.players.append(p)

        return p

    def _approve_all(self, game: Game) -> None:
        for player in game.players:
            player.wallet.approve(game.id)

    def assert_authorized_money_zero(self, game_id: str, *players: Player):
        for (i, p) in enumerate(players):
            authorized = p.wallet.authorized_money(game_id=game_id)
            self.assertEqual(0, authorized, f"player[{i}]")

    def test_finish_rate_single_winner(self):
        g = Game()
        winner = self._next_player(g, 50)
        loser = self._next_player(g, 50)

        self._winner_stub.set_scores({
            1: (with_cards(winner),),
            0: (with_cards(loser),),
        })

        self._coordinator.finish_game_with_winners(g)
        self._approve_all(g)

        self.assertAlmostEqual(100, winner.wallet.value(), places=1)
        self.assertAlmostEqual(0, loser.wallet.value(), places=1)
        self.assert_authorized_money_zero(g.id, winner, loser)

    def test_finish_rate_two_winners(self):
        g = Game()
        first_winner = self._next_player(g, 50)
        second_winner = self._next_player(g, 50)
        loser = self._next_player(g, 100)

        self._winner_stub.set_scores({
            1: (
                with_cards(first_winner),
                with_cards(second_winner),
            ),
            0: (with_cards(loser),),
        })

        self._coordinator.finish_game_with_winners(g)
        self._approve_all(g)

        self.assertAlmostEqual(75, first_winner.wallet.value(), places=1)
        self.assertAlmostEqual(75, second_winner.wallet.value(), places=1)
        self.assertAlmostEqual(50, loser.wallet.value(), places=1)
        self.assert_authorized_money_zero(
            g.id,
            first_winner,
            second_winner,
            loser,
        )

    def test_finish_rate_all_in_one_extra_winner(self):
        g = Game()
        first_winner = self._next_player(g, 15)  # All in.
        second_winner = self._next_player(g, 5)  # All in.
        extra_winner = self._next_player(g, 90)  # All in.
        loser = self._next_player(g, 90)  # Call.

        self._winner_stub.set_scores({
            2: (
                with_cards(first_winner),
                with_cards(second_winner),
            ),
            1: (with_cards(extra_winner),),
            0: (with_cards(loser),),
        })

        self._coordinator.finish_game_with_winners(g)
        self._approve_all(g)

        # Winners split matching pots.
        # Remaining unmatched chips return to the bigger stack.
        self.assertAlmostEqual(40, first_winner.wallet.value(), places=1)
        self.assertAlmostEqual(10, second_winner.wallet.value(), places=1)
        self.assertAlmostEqual(150, extra_winner.wallet.value(), places=1)

        self.assertAlmostEqual(0, loser.wallet.value(), places=1)

        self.assert_authorized_money_zero(
            g.id,
            first_winner,
            second_winner,
            extra_winner,
            loser,
        )

    def test_finish_rate_all_winners(self):
        g = Game()
        first_winner = self._next_player(g, 50)
        second_winner = self._next_player(g, 100)
        third_winner = self._next_player(g, 150)

        self._winner_stub.set_scores({
            1: (
                with_cards(first_winner),
                with_cards(second_winner),
                with_cards(third_winner),
            ),
        })

        self._coordinator.finish_game_with_winners(g)
        self._approve_all(g)

        self.assertAlmostEqual(50, first_winner.wallet.value(), places=1)
        self.assertAlmostEqual(
            100,
            second_winner.wallet.value(),
            places=1,
        )
        self.assertAlmostEqual(150, third_winner.wallet.value(), places=1)
        self.assert_authorized_money_zero(
            g.id,
            first_winner,
            second_winner,
            third_winner,
        )

    def test_finish_rate_all_in_all(self):
        g = Game()

        first_winner = self._next_player(g, 3)  # All in.
        second_winner = self._next_player(g, 60)  # All in.
        third_loser = self._next_player(g, 10)  # All in.
        fourth_loser = self._next_player(g, 10)  # All in.

        self._winner_stub.set_scores({
            3: (
                with_cards(first_winner),
                with_cards(second_winner),
            ),
            2: (with_cards(third_loser),),
            1: (with_cards(fourth_loser),),
        })

        self._coordinator.finish_game_with_winners(g)
        self._approve_all(g)

        # Winners share only eligible side pots
        self.assertAlmostEqual(6, first_winner.wallet.value(), places=1)
        self.assertAlmostEqual(77, second_winner.wallet.value(), places=1)

        self.assertAlmostEqual(0, third_loser.wallet.value(), places=1)
        self.assertAlmostEqual(0, fourth_loser.wallet.value(), places=1)

        self.assert_authorized_money_zero(
            g.id,
            first_winner,
            second_winner,
            third_loser,
            fourth_loser,
        )


class HandlePlayerActionStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.kv_store = InMemoryKV()
        self.mock_view = MagicMock()
        self.mock_view.send_message = AsyncMock()
        self.mock_bot = AsyncMock(spec=Bot)
        self.mock_bot.send_message = AsyncMock()

        cfg = Config()
        self.application = SimpleNamespace(chat_data={})

        self.model = PokerBotModel(
            view=self.mock_view,
            bot=self.mock_bot,
            cfg=cfg,
            kv=self.kv_store,
            application=self.application,
        )

        coordinator = MagicMock()
        coordinator.player_call_or_check = MagicMock(return_value=0)
        coordinator.player_raise_bet = MagicMock()
        coordinator.player_all_in = MagicMock(return_value=0)
        coordinator.process_game_turn = MagicMock(
            return_value=(TurnResult.CONTINUE_ROUND, None)
        )
        coordinator._send_or_update_game_state = AsyncMock()
        coordinator.advance_game_street = MagicMock(
            return_value=(GameState.FINISHED, 0)
        )
        coordinator.commit_round_bets = MagicMock()
        coordinator.finish_game_with_winners = MagicMock(return_value=[])
        self.model._coordinator = coordinator

        self.chat_id = 987654
        self.game = Game()
        self.player = Player(
            user_id=123,
            mention_markdown="@player",
            wallet=MagicMock(),
            ready_message_id=None,
        )
        self.player.state = PlayerState.ACTIVE
        self.player.round_rate = 0
        self.game.players = [self.player]
        self.game.current_player_index = 0
        self.game.max_round_rate = 0
        self.application.chat_data[self.chat_id] = {
            KEY_CHAT_DATA_GAME: self.game
        }

    async def test_blocks_initial_and_finished_states(self) -> None:
        for state in (GameState.INITIAL, GameState.FINISHED):
            with self.subTest(state=state):
                self.game.state = state
                result = await self.model.handle_player_action(
                    self.player.user_id,
                    self.chat_id,
                    "check",
                )
                self.assertFalse(result)

    async def test_allows_actions_during_active_rounds(self) -> None:
        active_states = (
            GameState.ROUND_PRE_FLOP,
            GameState.ROUND_FLOP,
            GameState.ROUND_TURN,
            GameState.ROUND_RIVER,
        )

        for state in active_states:
            with self.subTest(state=state):
                self.game.state = state
                self.player.state = PlayerState.ACTIVE
                self.player.round_rate = 0
                self.game.max_round_rate = 0
                self.game.recent_actions.clear()
                self.model._coordinator.process_game_turn.reset_mock()
                self.model._coordinator._send_or_update_game_state.reset_mock()

                result = await self.model.handle_player_action(
                    self.player.user_id,
                    self.chat_id,
                    "check",
                )

                self.assertTrue(result)
                recent_actions = self.game.recent_actions
                self.assertTrue(
                    any("checked" in action for action in recent_actions)
                )
                coordinator = self.model._coordinator
                coordinator.process_game_turn.assert_called_once()
                coordinator._send_or_update_game_state.assert_awaited()

    async def test_advance_to_finished_triggers_game_finish(self) -> None:
        self.game.state = GameState.ROUND_RIVER

        coordinator = self.model._coordinator
        coordinator.commit_round_bets.reset_mock()
        coordinator.advance_game_street.reset_mock()
        coordinator.process_game_turn.reset_mock()

        with patch.object(
            self.model,
            "_finish_game",
            new=AsyncMock(),
        ) as mock_finish:
            await self.model._advance_to_next_street(self.game, self.chat_id)

        coordinator.commit_round_bets.assert_called_once_with(self.game)
        coordinator.advance_game_street.assert_called_once_with(self.game)
        mock_finish.assert_awaited_once_with(self.game, self.chat_id)
        coordinator.process_game_turn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
