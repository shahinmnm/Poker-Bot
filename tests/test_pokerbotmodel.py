#!/usr/bin/env python3

import unittest
from typing import Dict, Tuple

import redis

from pokerapp.cards import Cards, Card
from pokerapp.config import Config
from pokerapp.entities import Money, Player, Game, Score
from pokerapp.game_coordinator import GameCoordinator
from pokerapp.pokerbotmodel import WalletManagerModel


def with_cards(p: Player) -> Tuple[Player, Cards]:
    return (p, [Card("6♥"), Card("A♥"), Card("A♣"), Card("A♠")])


class DummyWinnerDetermination:
    def __init__(self):
        self._scores: Dict[Score, Tuple[Tuple[Player, Cards], ...]] = {}

    def set_scores(self, scores: Dict[Score, Tuple[Tuple[Player, Cards], ...]]) -> None:
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
        self._kv = redis.Redis(
            host=cfg.REDIS_HOST,
            port=cfg.REDIS_PORT,
            db=cfg.REDIS_DB,
            password=cfg.REDIS_PASS if cfg.REDIS_PASS != "" else None
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
            self.assertEqual(0, authorized, "player[" + str(i) + "]")

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
            1: (with_cards(first_winner), with_cards(second_winner)),
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
            2: (with_cards(first_winner), with_cards(second_winner)),
            1: (with_cards(extra_winner),),
            0: (with_cards(loser),),
        })

        self._coordinator.finish_game_with_winners(g)
        self._approve_all(g)

        # Winners split matching pots; remaining unmatched chips return to bigger stack
        self.assertAlmostEqual(40, first_winner.wallet.value(), places=1)
        self.assertAlmostEqual(10, second_winner.wallet.value(), places=1)
        self.assertAlmostEqual(150, extra_winner.wallet.value(), places=1)

        self.assertAlmostEqual(0, loser.wallet.value(), places=1)

        self.assert_authorized_money_zero(
            g.id, first_winner, second_winner, extra_winner, loser,
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
            100, second_winner.wallet.value(), places=1)
        self.assertAlmostEqual(150, third_winner.wallet.value(), places=1)
        self.assert_authorized_money_zero(
            g.id, first_winner, second_winner, third_winner,
        )

    def test_finish_rate_all_in_all(self):
        g = Game()

        first_winner = self._next_player(g, 3)  # All in.
        second_winner = self._next_player(g, 60)  # All in.
        third_loser = self._next_player(g, 10)  # All in.
        fourth_loser = self._next_player(g, 10)  # All in.

        self._winner_stub.set_scores({
            3: (with_cards(first_winner), with_cards(second_winner)),
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
            g.id, first_winner, second_winner, third_loser, fourth_loser
        )


if __name__ == '__main__':
    unittest.main()
