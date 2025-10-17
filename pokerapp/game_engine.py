#!/usr/bin/env python3
"""
Pure poker game engine - no Telegram/UI dependencies.
Handles state transitions, player turns, and game flow.
"""

import logging
from typing import Optional
from enum import Enum
from pokerapp.entities import Game, GameState, Player, PlayerState

logger = logging.getLogger(__name__)


class TurnResult(Enum):
    """Result of processing a player turn"""
    CONTINUE_ROUND = "continue_round"
    END_ROUND = "end_round"
    END_GAME = "end_game"


class PokerEngine:
    """
    Mode-agnostic poker engine.
    Handles game flow without knowledge of Telegram/UI layer.
    """

    def __init__(self):
        pass

    def validate_join_balance(
        self,
        player_balance: int,
        table_stake: int,
    ) -> bool:
        """
        Q7: Check if player has sufficient balance to join.
        Requires at least 20 big blinds minimum.

        Args:
            player_balance: Current wallet balance
            table_stake: Small blind amount (5, 10, 25, etc.)

        Returns:
            True if player can afford to play
        """
        big_blind = table_stake * 2
        minimum_balance = big_blind * 20  # 20 big blinds minimum
        return player_balance >= minimum_balance

    def get_next_active_player(self, game: Game) -> Optional[Player]:
        """
        Find next player who can act (not folded, not all-in).

        Returns:
            Next active player or None if round should end
        """
        start_index = game.current_player_index
        players_count = len(game.players)

        for offset in range(1, players_count + 1):
            next_index = (start_index + offset) % players_count
            player = game.players[next_index]

            if player.state == PlayerState.ACTIVE:
                return player

        return None

    def should_end_round(self, game: Game) -> bool:
        """
        Check if betting round is complete.
        Round ends when all players matched max bet OR folded/all-in
        """
        active_players = [
            p
            for p in game.players
            if p.state == PlayerState.ACTIVE
        ]

        # Only one active player left
        if len(active_players) <= 1:
            return True

        # All active players matched the max bet
        all_matched = all(
            p.round_rate == game.max_round_rate
            for p in active_players
        )

        # Check if we've completed the circle back to last raiser
        current_player = game.players[game.current_player_index]
        at_round_initiator = (
            current_player.user_id == game.trading_end_user_id
        )

        return all_matched and at_round_initiator

    def process_turn(self, game: Game) -> TurnResult:
        """
        Process one player turn iteration.
        Replaces recursive _process_playing helper.

        Returns:
            TurnResult indicating whether to continue,
            end round, or end game
        """
        # Count players still in game
        active_or_allin = [
            p for p in game.players
            if p.state in (PlayerState.ACTIVE, PlayerState.ALL_IN)
        ]

        # Only one player left → end game immediately
        if len(active_or_allin) == 1:
            return TurnResult.END_GAME

        # Check if betting round is complete
        if self.should_end_round(game):
            return TurnResult.END_ROUND

        # Move to next active player
        next_player = self.get_next_active_player(game)

        if next_player is None:
            # No active players left (all folded or all-in)
            return TurnResult.END_ROUND

        # Update game state to next player's turn
        game.current_player_index = game.players.index(next_player)

        return TurnResult.CONTINUE_ROUND

    def advance_to_next_street(self, game: Game) -> GameState:
        """
        Transition to next betting round.
        Handles flop → turn → river → showdown progression.

        Returns:
            New game state
        """
        state_transitions = {
            GameState.ROUND_PRE_FLOP: GameState.ROUND_FLOP,
            GameState.ROUND_FLOP: GameState.ROUND_TURN,
            GameState.ROUND_TURN: GameState.ROUND_RIVER,
            GameState.ROUND_RIVER: GameState.FINISHED,
        }

        current_state = game.state

        if current_state not in state_transitions:
            raise ValueError(f"Cannot advance from state: {current_state}")

        new_state = state_transitions[current_state]
        game.state = new_state

        # Reset round betting
        for player in game.players:
            player.round_rate = 0
        game.max_round_rate = 0

        # Set first active player for new round
        game.current_player_index = 0
        game.trading_end_user_id = game.players[0].user_id

        return new_state

    def get_cards_to_deal(self, game_state: GameState) -> int:
        """
        Get number of community cards to deal for this street.

        Returns:
            Card count (0=pre-flop, 3=flop, 1=turn/river)
        """
        card_counts = {
            GameState.ROUND_PRE_FLOP: 0,
            GameState.ROUND_FLOP: 3,
            GameState.ROUND_TURN: 1,
            GameState.ROUND_RIVER: 1,
            GameState.FINISHED: 0,
        }
        return card_counts.get(game_state, 0)
