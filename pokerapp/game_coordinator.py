#!/usr/bin/env python3
"""
Game coordinator - orchestrates engine and betting logic.
Bridges pure game logic with Telegram bot operations.
"""

import logging
from typing import Optional, Tuple

from pokerapp.game_engine import PokerEngine, TurnResult
from pokerapp.betting import SidePotCalculator
from pokerapp.entities import (
    Game,
    GameState,
    Player,
    PlayerState,
    Money,
)
from pokerapp.winnerdetermination import WinnerDetermination

logger = logging.getLogger(__name__)


class GameCoordinator:
    """
    Coordinates game engine, betting, and winner determination.
    Replaces complex logic in pokerbotmodel.py
    """

    def __init__(self, view=None):
        self.engine = PokerEngine()
        self.pot_calculator = SidePotCalculator()
        self.winner_determine = WinnerDetermination()
        self._view = view  # Optional PokerBotViewer for UI updates

    async def _send_or_update_game_state(
        self,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = "",
    ) -> None:
        """
        Send or update the single living game message.

        Args:
            game: Current game state
            current_player: Player whose turn it is (for button generation)
            action_prompt: Custom prompt text to append
        """

        if self._view is None:
            logger.warning("View not initialized; cannot update game state UI")
            return

        # Format current game state
        state_text = self._view.format_game_state(game)

        # Add custom prompt if provided
        if action_prompt:
            state_text += f"\n\n{action_prompt}"

        # Build action buttons if a player is acting
        reply_markup = None

        if current_player is not None:
            reply_markup = self._view.build_action_buttons(game, current_player)

        # Edit existing message or send new one
        if game.has_group_message():
            try:
                await self._view.update_game_state(
                    chat_id=game.chat_id,
                    message_id=game.group_message_id,
                    text=state_text,
                    reply_markup=reply_markup,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to edit message %s, sending new: %s",
                    game.group_message_id,
                    exc,
                )

                # Fallback: send new message
                message_id = await self._view.send_game_state(
                    chat_id=game.chat_id,
                    text=state_text,
                    reply_markup=reply_markup,
                )
                game.set_group_message(message_id)
        else:
            # No existing message - send new
            message_id = await self._view.send_game_state(
                chat_id=game.chat_id,
                text=state_text,
                reply_markup=reply_markup,
            )
            game.set_group_message(message_id)

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
                logger.info(
                    "Player %s has $0 - setting ALL_IN and advancing turn",
                    current_player.user_id,
                )
                current_player.state = PlayerState.ALL_IN

                # Call process_turn() once more to advance to the next player.
                # Avoid recursion and skip ALL_IN players.
                result = self.engine.process_turn(game)

                if result == TurnResult.CONTINUE_ROUND:
                    return result, game.players[game.current_player_index]
                else:
                    return result, None

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

        big_blind_amount = (
            big_blind if big_blind is not None else small_blind * 2
        )

        self.player_raise_bet(game, game.players[0], small_blind)
        big_blind_raise = max(big_blind_amount - game.max_round_rate, 0)
        self.player_raise_bet(game, game.players[1], big_blind_raise)

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

    def _format_action_text(
        self,
        player: Player,
        action: str,
        amount: int = 0,
    ) -> str:
        """
        Format player action for the activity feed.

        Args:
            player: Player who took action
            action: Action type (fold/call/raise/check/all-in)
            amount: Money involved (0 for fold/check)

        Returns:
            Formatted string like "Alice called $50"
        """

        name = player.first_name

        if action == "fold":
            return f"{name} folded"
        elif action == "check":
            return f"{name} checked"
        elif action == "call":
            return f"{name} called ${amount}"
        elif action == "raise":
            return f"{name} raised to ${amount}"
        elif action == "all-in":
            return f"{name} went ALL-IN (${amount})"
        else:
            return f"{name} {action} ${amount}"
