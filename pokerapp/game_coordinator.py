"""Utility helpers that coordinate poker engine flow with wallet handling."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pokerapp.cards import Cards
from pokerapp.entities import (
    Game,
    GameState,
    Money,
    Player,
    PlayerState,
    Score,
    StakeConfig,
)
from pokerapp.game_engine import PokerEngine, TurnResult
from pokerapp.winnerdetermination import WinnerDetermination


class GameCoordinator:
    """Coordinate player actions with the pure poker engine."""

    def __init__(self) -> None:
        self._engine = PokerEngine()
        self._winner_determination = WinnerDetermination()

    def prepare_pre_flop(self, game: Game, stake_config: StakeConfig) -> None:
        """Post blinds and configure the table for a new hand."""

        if len(game.players) < 2:
            return

        small_blind_player = game.players[0]
        big_blind_player = game.players[1]

        self.raise_bet(game, small_blind_player, stake_config.small_blind)

        big_blind_raise = max(stake_config.big_blind - game.max_round_rate, 0)
        self.raise_bet(game, big_blind_player, big_blind_raise)

        dealer_index = 2 % len(game.players)
        game.trading_end_user_id = game.players[dealer_index].user_id

    def raise_bet(self, game: Game, player: Player, amount: int) -> None:
        """Raise the current rate for a player by ``amount``."""

        total_amount = amount + (game.max_round_rate - player.round_rate)
        player.wallet.authorize(game.id, total_amount)
        player.round_rate += total_amount

        game.max_round_rate = player.round_rate
        game.trading_end_user_id = player.user_id

    def call_or_check(self, game: Game, player: Player) -> None:
        amount = game.max_round_rate - player.round_rate
        player.wallet.authorize(game.id, amount)
        player.round_rate += amount

    def all_in(self, game: Game, player: Player) -> Money:
        amount = player.wallet.authorize_all(game.id)
        player.round_rate += amount
        if game.max_round_rate < player.round_rate:
            game.max_round_rate = player.round_rate
            game.trading_end_user_id = player.user_id
        return amount

    def move_round_bets_to_pot(self, game: Game) -> None:
        for p in game.players:
            game.pot += p.round_rate
            p.round_rate = 0

        game.max_round_rate = 0
        if game.players:
            game.trading_end_user_id = game.players[0].user_id

    def process_game_turn(self, game: Game) -> Tuple[TurnResult, Optional[Player]]:
        result = self._engine.process_turn(game)

        if result == TurnResult.CONTINUE_ROUND:
            return result, game.players[game.current_player_index]

        self.move_round_bets_to_pot(game)
        return result, None

    def advance_game_street(self, game: Game) -> Tuple[GameState, int]:
        new_state = self._engine.advance_to_next_street(game)
        cards_count = self._engine.get_cards_to_deal(new_state)
        return new_state, cards_count

    def calculate_payouts(
        self,
        game: Game,
        player_scores: Dict[Score, List[Tuple[Player, Cards]]],
    ) -> List[Tuple[Player, Cards, Money]]:
        sorted_player_scores_items = sorted(
            player_scores.items(),
            reverse=True,
            key=lambda x: x[0],
        )
        player_scores_values = [value for _, value in sorted_player_scores_items]

        results: List[Tuple[Player, Cards, Money]] = []
        for win_players in player_scores_values:
            players_authorized = sum(
                player.wallet.authorized_money(game_id=game.id)
                for player, _ in win_players
            )
            if players_authorized <= 0:
                continue

            game_pot = game.pot
            for win_player, best_hand in win_players:
                if game.pot <= 0:
                    break

                authorized = win_player.wallet.authorized_money(game_id=game.id)

                win_money_real = game_pot * (authorized / players_authorized)
                win_money_real = round(win_money_real)

                win_money_can_get = authorized * len(game.players)
                win_money = min(win_money_real, win_money_can_get)

                win_player.wallet.inc(win_money)
                game.pot -= win_money
                results.append((win_player, best_hand, win_money))

        return results

    def finish_game_with_winners(
        self,
        game: Game,
    ) -> List[Tuple[Player, Cards, Money]]:
        active_players = game.players_by(
            states=(PlayerState.ACTIVE, PlayerState.ALL_IN)
        )
        if not active_players:
            return []

        player_scores = self._winner_determination.determinate_scores(
            players=active_players,
            cards_table=game.cards_table,
        )

        return self.calculate_payouts(game=game, player_scores=player_scores)
