#!/usr/bin/env python3
"""Game coordinator - orchestrates engine and betting logic."""

import json
import logging
from typing import Optional, Tuple, Union

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import TelegramError

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
from pokerapp.winnerdetermination import (
    WinnerDetermination,
    determine_winner_with_rank,
)

logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


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
        self._chat_id: Optional[int] = None
        self._bot = getattr(view, "_bot", None)
        self._kv = getattr(view, "_kv", None)
        if view is not None:
            live_manager = getattr(view, "_live_manager", None)
            if live_manager is not None:
                setattr(live_manager, "_coordinator", self)

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

    async def _send_winner_announcement(
        self,
        chat_id: int,
        game: Game,
    ) -> Optional[Message]:
        """Send end-game results message with inline buttons."""

        if self._bot is None:
            logger.warning("Bot instance unavailable for winner announcement")
            return None

        winner, hand_rank_key = determine_winner_with_rank(game)

        if winner is None:
            return await self._bot.send_message(
                chat_id=chat_id,
                text="🏆 Game ended (no winner determined)",
            )

        language_code = "en"
        if self._kv is not None:
            try:
                language = self._kv.get_chat_language(chat_id)
                if language:
                    language_code = language
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to fetch chat language", exc_info=True)

        hand_rank_localized = translation_manager.t(
            f"poker.hand.{hand_rank_key}",
            lang=language_code,
        )

        cards = getattr(winner, "cards", []) or []
        cards_display = " - ".join(self._format_card(card) for card in cards) or "—"
        cards_ltr = f"\u202A{cards_display}\u202C"

        player_name = self._get_player_display_name(winner)
        pot_amount = getattr(game, "pot", 0)

        message_text = translation_manager.t(
            "game.results.winner",
            lang=language_code,
            player_name=player_name,
            amount=pot_amount,
            hand_rank=hand_rank_localized,
            cards=cards_ltr,
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    translation_manager.t(
                        "game.results.btn.play_again",
                        lang=language_code,
                    ),
                    callback_data="ready",
                ),
                InlineKeyboardButton(
                    translation_manager.t(
                        "game.results.btn.leave",
                        lang=language_code,
                    ),
                    callback_data="leave_game",
                ),
            ]
        ]

        return await self._bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _track_game_message(
        self,
        game_id: Optional[str],
        message_id: Optional[int],
    ) -> None:
        """Persist message identifiers for later cleanup."""

        if not game_id or message_id is None or self._kv is None:
            return

        key = f"game:{game_id}:message_ids"
        try:
            raw = self._kv.get(key)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                message_ids = json.loads(raw)
            else:
                message_ids = []
        except Exception:
            message_ids = []

        if message_id not in message_ids:
            message_ids.append(message_id)
            try:
                self._kv.set(key, json.dumps(message_ids))
            except Exception:  # pragma: no cover - defensive
                logger.debug("Failed to store message IDs", exc_info=True)

    async def _cleanup_game_messages(
        self,
        chat_id: int,
        game_id: Optional[str],
        keep_message_id: Optional[int],
    ) -> None:
        """Delete tracked game messages except the final results message."""

        if self._kv is None or self._bot is None or not game_id:
            return

        key = f"game:{game_id}:message_ids"
        try:
            raw = self._kv.get(key)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                message_ids = json.loads(raw)
            else:
                message_ids = []
        except Exception:
            message_ids = []

        for stored_id in message_ids:
            try:
                stored_int = int(stored_id)
            except (TypeError, ValueError):
                continue

            if keep_message_id is not None and stored_int == keep_message_id:
                continue

            try:
                await self._bot.delete_message(
                    chat_id=chat_id,
                    message_id=stored_int,
                )
            except TelegramError as exc:  # pragma: no cover - network
                logger.warning(
                    "Failed to delete game message %s: %s",
                    stored_int,
                    exc,
                )

        try:
            self._kv.delete(key)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Failed to clear tracked message IDs", exc_info=True)

    def _format_card(self, card) -> str:
        """Format a card using rank and suit emoji."""

        suit_map = {
            "♥": "♥️",
            "♦": "♦️",
            "♣": "♣️",
            "♠": "♠️",
        }
        rank = getattr(card, "rank", str(card))
        suit = getattr(card, "suit", "♠")
        return f"{rank}{suit_map.get(suit, suit)}"

    def _get_player_display_name(self, player: Player) -> str:
        mention = getattr(player, "mention_markdown", "") or ""
        if mention.startswith("[") and "](" in mention:
            try:
                return mention.split("]", 1)[0][1:]
            except (IndexError, ValueError):  # pragma: no cover - defensive
                pass
        username = getattr(player, "username", None)
        if username:
            return username
        return str(getattr(player, "user_id", "Player"))

    async def finish_game_with_cleanup(
        self,
        chat_id: int,
        game: Game,
    ) -> None:
        """Finish game, send results, and clean up messages."""

        results_message = await self._send_winner_announcement(chat_id, game)

        game_id = getattr(game, "id", None)
        message_id = getattr(results_message, "message_id", None)
        await self._cleanup_game_messages(chat_id, game_id, message_id)

        game.state = GameState.FINISHED
        game.group_message_id = None

