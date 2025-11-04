#!/usr/bin/env python3
"""
Game coordinator - orchestrates engine and betting logic.
Bridges pure game logic with Telegram bot operations.
"""

import datetime
import json
import logging
from typing import Optional, Tuple, Union

from pokerapp.game_engine import PokerEngine, TurnResult
from pokerapp.betting import SidePotCalculator
from pokerapp.entities import (
    Game,
    GameState,
    Player,
    PlayerState,
    Money,
)
from pokerapp.notify_utils import LoggerHelper
from pokerapp.i18n import translation_manager
from pokerapp.winnerdetermination import WinnerDetermination
from pokerapp.kvstore import ensure_kv

logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


class GameCoordinator:
    """
    Coordinates game engine, betting, and winner determination.
    Replaces complex logic in pokerbotmodel.py
    """

    def __init__(self, view=None, kv=None):
        self.engine = PokerEngine()
        self.pot_calculator = SidePotCalculator()
        self.winner_determine = WinnerDetermination()
        self._view = view  # Optional PokerBotViewer for UI updates
        self._chat_id: Optional[int] = None
        self._kv = ensure_kv(kv) if kv is not None else None

    async def _send_or_update_game_state(
        self,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = "",
        chat_id: Optional[Union[int, str]] = None,
    ) -> None:
        """
        Send or update the single living game message.

        Args:
            game: Current game state
            current_player: Player whose turn it is (for button generation)
            action_prompt: Custom prompt text to append
            chat_id: Explicit chat identifier for viewer updates
        """

        if self._view is None:
            log_helper.warn(
                "CoordinatorViewMissing",
                "View not initialized; cannot update game state UI",
            )
            return

        effective_chat_id = (
            chat_id if chat_id is not None else getattr(self, "_chat_id", None)
        )
        if effective_chat_id is None:
            effective_chat_id = getattr(game, "chat_id", None)

        if isinstance(effective_chat_id, str) and effective_chat_id.isdigit():
            effective_chat_id = int(effective_chat_id)

        if (
            game.state == GameState.FINISHED
            and game.has_group_message()
            and effective_chat_id is not None
        ):
            try:
                await self._view.remove_message(
                    chat_id=effective_chat_id,
                    message_id=game.group_message_id,
                )
                log_helper.info(
                    "CoordinatorFinishedCleanup",
                    "Deleted finished game message",
                    message_id=game.group_message_id,
                )
            except Exception as exc:  # pragma: no cover - Telegram failures
                log_helper.debug(
                    "CoordinatorFinishedCleanupFailed",
                    "Could not delete finished game message",
                    message_id=game.group_message_id,
                    error=str(exc),
                )
            finally:
                game.group_message_id = None
            return

        live_manager = getattr(self._view, "_live_manager", None)
        if live_manager is not None:
            resolved_player = current_player
            if resolved_player is None and game.players:
                index = getattr(game, "current_player_index", -1)
                if 0 <= index < len(game.players):
                    resolved_player = game.players[index]

            if (
                resolved_player is not None
                and game.state != GameState.FINISHED
            ):
                send_or_update = live_manager.send_or_update_game_state
                message_id = await send_or_update(
                    chat_id=effective_chat_id,
                    game=game,
                    current_player=resolved_player,
                )

                if message_id is not None:
                    return

            log_helper.debug(
                "CoordinatorFallback",
                "LiveMessageManager unavailable or no player resolved",
            )

        # Edit existing message or send new
        if game.has_group_message():
            updated = await self._view.update_game_state(
                chat_id=effective_chat_id,
                message_id=game.group_message_id,
                game=game,
                current_player=current_player,
                action_prompt=action_prompt,
            )

            if updated:
                return

            log_helper.warn(
                "CoordinatorMessageRetry",
                message=(
                    "Failed to edit message; attempting to send new message"
                ),
                message_id=game.group_message_id,
            )

        message_id = await self._view.send_game_state(
            chat_id=effective_chat_id,
            game=game,
            current_player=current_player,
            action_prompt=action_prompt,
        )

        if message_id is not None:
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

        Returns:
            (TurnResult, next_player_or_None)
        """
        result = self.engine.process_turn(game)

        if result == TurnResult.CONTINUE_ROUND:
            current_player = game.players[game.current_player_index]

            # Auto all-in if player has no money
            if current_player.wallet.value() <= 0:
                log_helper.info(
                    "CoordinatorAutoAllIn",
                    "Player has zero balance, forcing ALL_IN",
                    user_id=current_player.user_id,
                )
                current_player.state = PlayerState.ALL_IN

                # Recursively process next turn since this player cannot act
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

        big_blind_amount = (
            big_blind if big_blind is not None else small_blind * 2
        )

        self.player_raise_bet(game, game.players[0], small_blind)
        self.player_raise_bet(game, game.players[1], big_blind_amount)

    def player_raise_bet(
        self,
        game: Game,
        player: Player,
        amount: int,
    ) -> Money:
        """Handle raise/bet action for a player."""

        call_amount = max(game.max_round_rate - player.round_rate, 0)
        raise_increment = max(amount - game.max_round_rate, 0)
        total_needed = call_amount + raise_increment

        log_helper.debug(
            "CoordinatorRaise",
            "Player raising action",
            user_id=player.user_id,
            call_amount=call_amount,
            raise_increment=raise_increment,
            total_needed=total_needed,
        )

        if total_needed <= 0:
            return 0

        player.wallet.authorize(
            game_id=game.id,
            amount=total_needed,
        )
        player.round_rate += total_needed

        if player.round_rate > game.max_round_rate:
            game.max_round_rate = player.round_rate
            game.trading_end_user_id = player.user_id

        return total_needed

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

    async def register_webapp_game(
        self, game_id: str, chat_id: int, game: "Game"
    ) -> None:
        """Register a game snapshot for discovery by the web frontend."""

        if self._kv is None:
            logger.debug(
                "Skipping webapp game registration – no KV backend configured",
                extra={"game_id": game_id},
            )
            return

        stake_config = getattr(game, "stake_config", None)
        small_blind = getattr(stake_config, "small_blind", None)
        big_blind = getattr(stake_config, "big_blind", None)

        if small_blind is None:
            small_blind = getattr(game, "table_stake", 10) or 10
        if big_blind is None:
            big_blind = small_blind * 2

        payload = {
            "game_id": str(game_id),
            "chat_id": str(chat_id),
            "mode": getattr(game.mode, "value", str(getattr(game, "mode", "unknown"))),
            "state": getattr(game.state, "name", "UNKNOWN"),
            "pot": str(getattr(game, "pot", 0)),
            "players_count": len(getattr(game, "players", [])),
            "small_blind": str(small_blind),
            "big_blind": str(big_blind),
            "max_players": 8,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }

        stake_level = getattr(stake_config, "name", None)
        if stake_level:
            payload["stake_level"] = stake_level

        players = getattr(game, "players", [])
        if players:
            host_id = getattr(players[0], "user_id", None)
            if host_id is not None:
                payload["host"] = str(host_id)

        try:
            key = f"game:{game_id}:meta"
            self._kv.set(key, json.dumps(payload), ex=86400)
            logger.info("Registered game %s for webapp visibility", game_id)
        except Exception as exc:  # pragma: no cover - Redis failures
            logger.error(
                "Failed to register game %s for webapp visibility: %s",
                game_id,
                exc,
            )

    def _move_bets_to_pot(self, game: Game) -> None:
        """Move all round bets to main pot (replaces RoundRateModel.to_pot)"""
        for player in game.players:
            game.pot += player.round_rate
            player.round_rate = 0

        game.max_round_rate = 0

        if game.players:
            dealer_index = game.dealer_index % len(game.players)
            dealer_player = game.players[dealer_index]
            game.trading_end_user_id = dealer_player.user_id
        else:
            game.trading_end_user_id = 0

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
        user_id = getattr(player, "user_id", None)

        if action == "fold":
            return translation_manager.t(
                "msg.player_folded",
                user_id=user_id,
                player=name,
            )
        if action == "check":
            return translation_manager.t(
                "msg.player_checked",
                user_id=user_id,
                player=name,
            )
        if action == "call":
            return translation_manager.t(
                "msg.player_called",
                user_id=user_id,
                player=name,
                amount=amount,
            )
        if action == "raise":
            return translation_manager.t(
                "msg.player_raised",
                user_id=user_id,
                player=name,
                amount=amount,
            )
        if action == "all-in":
            return translation_manager.t(
                "msg.player_all_in",
                user_id=user_id,
                player=name,
                amount=amount,
            )

        return translation_manager.t(
            "msg.player_action.generic",
            user_id=user_id,
            player=name,
            action=action,
            amount=amount,
        )
