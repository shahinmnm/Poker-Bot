#!/usr/bin/env python3
"""
Game coordinator - orchestrates engine and betting logic.
Bridges pure game logic with Telegram bot operations.
"""

import logging
from typing import Optional, Tuple

from pokerapp.game_engine import PokerEngine, TurnResult
from pokerapp.betting import SidePotCalculator
from pokerapp.entities import Game, GameState, Player, PlayerState, Money
from pokerapp.winnerdetermination import WinnerDetermination

logger = logging.getLogger(__name__)


class GameCoordinator:
    """
    Coordinates game engine, betting, and winner determination.
    Replaces complex logic in pokerbotmodel.py
    """

    def __init__(self):
        self.engine = PokerEngine()
        self.pot_calculator = SidePotCalculator()
        self.winner_determine = WinnerDetermination()

    def can_player_join(self, player_balance: int, table_stake: int) -> bool:
        """
        Q7: Validate player can afford to join table.

        Args:
            player_balance: Current wallet balance
            table_stake: Small blind amount

        Returns:
            True if player meets minimum balance requirement
        """
        return self.engine.validate_join_balance(player_balance, table_stake)

    def process_game_turn(
        self,
        game: Game,
    ) -> Tuple[TurnResult, Optional[Player]]:
        """
        Process one game turn iteration.
        Replaces legacy _process_playing recursion.

        Returns:
            (TurnResult, next_player_or_None)
        """
        result = self.engine.process_turn(game)

        if result == TurnResult.CONTINUE_ROUND:
            current_player = game.players[game.current_player_index]

            # Auto all-in if player has no money
            if current_player.wallet.value() <= 0:
                current_player.state = PlayerState.ALL_IN
                # Recursively check again after state change
                return self.process_game_turn(game)

            return result, current_player

        return result, None

    def advance_game_street(self, game: Game) -> Tuple[GameState, int]:
        """
        Move to next betting round and get cards to deal.

        Returns:
            (new_game_state, cards_to_deal_count)
        """
        new_state = self.engine.advance_to_next_street(game)
        cards_count = self.engine.get_cards_to_deal(new_state)

        return new_state, cards_count

    def commit_round_bets(self, game: Game) -> None:
        """Move current round bets into the pot."""

        self._move_bets_to_pot(game)

    def apply_pre_flop_blinds(
        self,
        game: Game,
        small_blind: int,
        big_blind: Optional[int] = None,
    ) -> None:
        """Apply small and big blinds at the start of the hand."""

        if len(game.players) < 2:
            return

        big_blind_amount = big_blind if big_blind is not None else small_blind * 2

        self.player_raise_bet(game, game.players[0], small_blind)
        self.player_raise_bet(game, game.players[1], big_blind_amount)

    def player_raise_bet(
        self,
        game: Game,
        player: Player,
        amount: int,
    ) -> Money:
        """Handle raise/bet action for a player."""

        total_amount = amount + (game.max_round_rate - player.round_rate)

        player.wallet.authorize(
            game_id=game.id,
            amount=total_amount,
        )
        player.round_rate += total_amount

        game.max_round_rate = player.round_rate
        game.trading_end_user_id = player.user_id

        return total_amount

    def player_call_or_check(self, game: Game, player: Player) -> Money:
        """Handle call/check action for a player."""

        amount = game.max_round_rate - player.round_rate

        player.wallet.authorize(
            game_id=game.id,
            amount=amount,
        )
        player.round_rate += amount

        return amount

    def player_all_in(self, game: Game, player: Player) -> Money:
        """Handle all-in action for a player."""

        amount = player.wallet.authorize_all(
            game_id=game.id,
        )
        player.round_rate += amount

        if game.max_round_rate < player.round_rate:
            game.max_round_rate = player.round_rate
            game.trading_end_user_id = player.user_id

        return amount

    def finish_game_with_winners(self, game: Game):
        """
        Calculate winners and distribute pots using side pot logic.
        REPLACES old RoundRateModel.finish_rate()

        Returns:
            List of (player, winning_hand, money_won)
        """
        # Move all round bets to pot
        self._move_bets_to_pot(game)

        # Get active players for winner determination
        active_players = game.players_by(
            states=(PlayerState.ACTIVE, PlayerState.ALL_IN)
        )

        # Determine hand rankings
        player_scores = self.winner_determine.determinate_scores(
            players=active_players,
            cards_table=game.cards_table,
        )

        # Calculate side pots
        side_pots = self.pot_calculator.calculate_side_pots(game)

        # Distribute winnings
        winners_results = self.pot_calculator.distribute_pots(
            side_pots=side_pots,
            player_scores=player_scores,
        )

        return winners_results

    def _move_bets_to_pot(self, game: Game) -> None:
        """Move all round bets to main pot (replaces RoundRateModel.to_pot)"""
        for player in game.players:
            game.pot += player.round_rate
            player.round_rate = 0

        game.max_round_rate = 0
        game.trading_end_user_id = game.players[0].user_id
