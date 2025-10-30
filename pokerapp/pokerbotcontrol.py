#!/usr/bin/env python3

import inspect
import logging
from typing import List, Optional, Tuple, TYPE_CHECKING

from telegram import (
    BotCommand,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
)

from pokerapp.entities import Game, Player, PlayerAction
from pokerapp.notify_utils import LoggerHelper, NotificationManager
from pokerapp.pokerbotmodel import (
    PokerBotModel,
    PlayerActionValidation,
    PreparedPlayerAction,
)
from pokerapp.request_cache import RequestCache

if TYPE_CHECKING:
    from pokerapp.live_message import LiveMessageManager
    from pokerapp.pokerbotview import PokerBotViewer

logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


class PokerBotController:
    """Controller for handling Telegram updates and routing to model."""

    _STALE_QUERY_MESSAGE = (
        "Query is too old and response timeout expired or query id is invalid"
    )

    def __init__(
        self,
        model: PokerBotModel,
        application: Application,
    ) -> None:
        """
        Initialize controller with handlers.

        Args:
            model: PokerBotModel instance
            application: PTB Application instance
        """
        self._model = model
        self._application = application
        self._view: "PokerBotViewer" = model._view
        self._pending_fold_confirmations: dict[int, PreparedPlayerAction] = {}

        application.add_handler(CommandHandler("ready", self._handle_ready))
        application.add_handler(CommandHandler("start", self._handle_start))
        application.add_handler(CommandHandler("stop", self._handle_stop))
        application.add_handler(CommandHandler("money", self._handle_money))
        application.add_handler(CommandHandler("ban", self._handle_ban))
        application.add_handler(CommandHandler("cards", self._handle_cards))
        application.add_handler(CommandHandler("help", self._handle_help))
        application.add_handler(
            CommandHandler("private", self._handle_private)
        )
        application.add_handler(
            CommandHandler("join", self._handle_join_private)
        )
        application.add_handler(
            CommandHandler("invite", self._handle_invite)
        )
        application.add_handler(
            CommandHandler("accept", self._handle_accept_invite)
        )
        application.add_handler(
            CommandHandler("decline", self._handle_decline_invite)
        )
        application.add_handler(
            CommandHandler("leave", self._handle_leave_private)
        )
        # Register callback query handlers before the fallback handler
        application.add_handler(
            CallbackQueryHandler(
                self._handle_stake_selection,
                pattern=r"^stake:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_invite_accept_callback,
                pattern=r"^invite_accept:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_invite_decline_callback,
                pattern=r"^invite_decline:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_private_start_callback,
                pattern=r"^private_start:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_private_leave_callback,
                pattern=r"^private_leave:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_action_button,
                pattern=r"^action:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_lobby_sit,
                pattern=r"^lobby_sit$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_lobby_leave,
                pattern=r"^lobby_leave$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_lobby_start,
                pattern=r"^lobby_start$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._handle_callback_query)
        )

        application.post_init = self._post_init

        log_helper.info("ControllerInit", "Handlers registered")

    def _get_live_manager(self) -> Optional["LiveMessageManager"]:
        """Safely retrieve :class:`LiveMessageManager` from the view.

        Returns:
            LiveMessageManager if available, otherwise ``None``.

        Notes:
            The controller defensively guards access to the view to prevent
            attribute errors if initialization order changes or the view has
            not finished constructing its live message helpers.
        """

        try:
            view = getattr(self, "_view", None)
            if view is None:
                log_helper.warn(
                    "ControllerViewMissing",
                    "Controller has no view reference; cannot access LiveMessageManager",
                )
                return None

            live_manager = getattr(view, "_live_manager", None)
            if live_manager is None:
                log_helper.debug(
                    "LiveManagerMissing",
                    "View exists but LiveMessageManager not initialized",
                )

            return live_manager

        except Exception as exc:  # pragma: no cover - defensive logging
            log_helper.error(
                "LiveManagerAccessError",
                f"Failed to retrieve LiveMessageManager: {exc}",
                exc_info=True,
            )
            return None

    @staticmethod
    def _find_player(game: Game, user_id: int) -> Optional[Player]:
        """Return the player in *game* that matches *user_id*, if any."""

        for player in getattr(game, "players", []):
            if str(getattr(player, "user_id", "")) == str(user_id):
                return player

        return None

    @staticmethod
    def _should_confirm_fold(game: Game, player: Player) -> bool:
        """Return ``True`` when folding should prompt for confirmation."""

        pot_size = getattr(game, "pot", 0)
        current_bet = getattr(game, "max_round_rate", 0)
        player_invested = getattr(player, "round_rate", 0)

        is_big_pot = pot_size > (5 * current_bet)
        has_stake = player_invested > (0.1 * pot_size)

        return is_big_pot and has_stake

    async def handle_fold(
        self,
        user_id: int,
        game: Game,
        confirmed: bool = False,
        *,
        prepared_action: Optional[PreparedPlayerAction] = None,
        query=None,
    ) -> Optional[bool]:
        """Process a fold request, optionally prompting for confirmation.

        Args:
            user_id: Telegram identifier of the acting user.
            game: Active game context for the fold.
            confirmed: ``True`` when the user has already confirmed.
            prepared_action: Pre-validated action metadata, if available.
            query: Callback query instance used for popups/toasts.

        Returns:
            ``True`` when the fold action executed successfully,
            ``False`` on failure, and ``None`` if awaiting confirmation.
        """

        player = (
            prepared_action.current_player
            if prepared_action is not None
            else self._find_player(game, user_id)
        )

        if player is None:
            log_helper.warn(
                "FoldPlayerMissing",
                "Unable to locate player for fold action",
                user_id=user_id,
                game_id=getattr(game, "id", None),
            )
            return False

        if not confirmed:
            if self._should_confirm_fold(game, player):
                if prepared_action is not None:
                    self._pending_fold_confirmations[user_id] = prepared_action
                await self._view.show_fold_confirmation(
                    chat_id=player.user_id,
                    pot_size=getattr(game, "pot", 0),
                    player_invested=player.round_rate,
                )
                if query is not None:
                    await NotificationManager.toast(
                        query,
                        text="⚠️ Confirm fold",
                        event="FoldConfirmPrompt",
                    )
                return None

            self._pending_fold_confirmations.pop(user_id, None)

        action_to_execute = prepared_action
        if action_to_execute is None:
            action_to_execute = self._pending_fold_confirmations.get(user_id)

        self._pending_fold_confirmations.pop(user_id, None)

        if action_to_execute is None:
            log_helper.warn(
                "FoldActionMissing",
                "No prepared fold action available",
                user_id=user_id,
                game_id=getattr(game, "id", None),
            )
            return False

        success = await self._model.execute_player_action(action_to_execute)
        return success

    @classmethod
    def _is_stale_callback_query_error(cls, error: BadRequest) -> bool:
        """Return True when Telegram reports an expired callback query."""

        return cls._STALE_QUERY_MESSAGE in str(error)

    async def _respond_to_query(
        self,
        query,
        text: str | None = None,
        *,
        show_alert: bool = False,
        event: str = "ControllerPopup",
        context: CallbackContext | None = None,
        fallback_chat_id: int | None = None,
    ) -> bool:
        """Centralized callback responder with optional fallback messaging."""

        if (
            text is None
            or not show_alert
            or not (context and fallback_chat_id)
        ):
            return await NotificationManager.popup(
                query,
                text=text,
                show_alert=show_alert,
                event=event,
            )

        return await NotificationManager.popup_with_fallback(
            query,
            text=text,
            bot=context.bot if context else None,
            fallback_chat_id=fallback_chat_id,
            show_alert=show_alert,
            event=event,
        )

    async def _post_init(self, application: Application) -> None:
        """Set up bot command descriptions in Telegram UI."""
        commands = [
            BotCommand("start", "🎰 Start a new poker game"),
            BotCommand("ready", "✋ Join lobby / Sit at table"),
            BotCommand(
                "private",
                "🔒 Create private game (Coming Soon)",
            ),
            BotCommand(
                "join",
                "🚪 Join private game by code (Coming Soon)",
            ),
            BotCommand(
                "invite",
                "📨 Invite user to private game (Coming Soon)",
            ),
            BotCommand(
                "accept",
                "✅ Accept private game invitation (Coming Soon)",
            ),
            BotCommand(
                "decline",
                "❌ Decline private game invitation (Coming Soon)",
            ),
            BotCommand(
                "leave",
                "🚶 Leave private game (Coming Soon)",
            ),
            BotCommand("money", "💰 Claim daily bonus (dice roll)"),
            BotCommand("cards", "🃏 Show your cards again"),
            BotCommand("ban", "⛔ Force AFK player to fold (2min+)"),
            BotCommand("stop", "🛑 Leave current game"),
            BotCommand("help", "❓ Show game rules and commands"),
        ]

        try:
            await application.bot.set_my_commands(commands)
            log_helper.info(
                "CommandSetup",
                "Bot commands registered in Telegram UI",
            )
        except Exception as exc:  # pragma: no cover - Telegram API
            log_helper.error(
                "CommandSetup",
                "Failed to register commands",
                error=str(exc),
            )

    async def _handle_ready(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /ready command."""
        await self._model.ready(update, context)

    async def _handle_start(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /start command."""
        await self._model.start(update, context)

    async def _handle_stop(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /stop command."""
        await self._model.stop(
            user_id=update.effective_message.from_user.id
        )

    async def _handle_cards(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /cards command."""
        await self._model.send_cards_to_user(update, context)

    async def _handle_ban(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /ban command."""
        await self._model.ban_player(update, context)

    async def _handle_money(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /money command."""
        await self._model.bonus(update, context)

    async def _handle_help(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /help command with game rules."""
        help_text = """
🎰 TEXAS HOLD’EM POKER BOT 🎰

🎮 GAME MODES:

🏛️ Group Games - Play in group chats with friends
🔒 Private Games - Exclusive invite-only tables (Coming Soon!)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏛️ GROUP GAME COMMANDS:

✋ /ready - Join lobby (sit at table)
🎰 /start - Start game (or use lobby button)
🛑 /stop - Leave current game

🎲 LOBBY SYSTEM:

Use /ready to join the lobby
Interactive buttons to sit/leave/start
Lobby shows all seated players
Minimum 2 players to start game

🔒 PRIVATE GAME COMMANDS (Coming Soon):

🔒 /private - Create private game lobby
🚪 /join <code> - Join game by secret code
📨 /invite @username - Invite specific user
✅ /accept - Accept invitation
❌ /decline - Decline invitation
🚶 /leave - Leave private game

💎 GENERAL COMMANDS:

💰 /money - Get daily bonus chips
🃏 /cards - Show your cards again
⛔ /ban - Force AFK player out (admin only)
❓ /help - Show this help message

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 HOW TO PLAY:

🏛️ Group Mode:

1️⃣ Add bot to your group chat
2️⃣ Everyone sends ✋ /ready
3️⃣ Host sends 🎰 /start when ready
4️⃣ Game begins automatically!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🃏 POKER BASICS:

🎴 Each player gets 2 private cards
🃏 5 community cards revealed in stages
💰 Best 5-card hand wins the pot!

🎲 BETTING ROUNDS:

🌅 Pre-flop - Only your 2 cards
🌄 Flop - 3 community cards revealed
🌇 Turn - 4th community card
🌃 River - Final 5th card

🎯 ACTIONS:

✅ Check - Pass (no bet required)
💵 Call - Match current bet
📈 Raise - Increase the bet
🚀 All-in - Bet everything!
❌ Fold - Give up this hand

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎁 DAILY BONUS:

Send 💰 /money once per day for free chips!

🎲 Bonus amounts:

⚀ = 5 chips   ⚁ = 20 chips   ⚂ = 40 chips
⚃ = 80 chips  ⚄ = 160 chips  ⚅ = 320 chips

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🍀 Good luck at the tables! 🍀

"""
        await update.effective_message.reply_text(help_text)

    async def _handle_button_clicked(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle inline button clicks for player actions."""
        query_data = update.callback_query.data

        if query_data == PlayerAction.CHECK.value:
            await self._model.call_or_check(update, context)
        elif query_data == PlayerAction.CALL.value:
            await self._model.call_or_check(update, context)
        elif query_data == PlayerAction.FOLD.value:
            await self._model.fold(update, context)
        elif query_data == str(PlayerAction.SMALL.value):
            await self._model.raise_rate_bet(
                update, context, PlayerAction.SMALL
            )
        elif query_data == str(PlayerAction.NORMAL.value):
            await self._model.raise_rate_bet(
                update, context, PlayerAction.NORMAL
            )
        elif query_data == str(PlayerAction.BIG.value):
            await self._model.raise_rate_bet(
                update, context, PlayerAction.BIG
            )
        elif query_data == PlayerAction.ALL_IN.value:
            await self._model.all_in(update, context)

    async def _handle_lobby_sit(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Handle lobby "Sit at Table" button."""

        query = update.callback_query
        if query is None:
            return

        await self._respond_to_query(query)
        await self._model.ready(update, context)

    async def _handle_lobby_leave(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Handle lobby "Leave Table" button."""

        query = update.callback_query
        if query is None:
            return

        chat = update.effective_chat
        user = query.from_user

        if chat is None or user is None:
            await self._respond_to_query(query)
            return

        await self._model.remove_lobby_player(
            context=context,
            chat_id=chat.id,
            user_id=user.id,
        )
        await self._respond_to_query(
            query,
            text="🚶 You left the table. Use /ready to rejoin!",
        )

    async def _handle_lobby_start(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Handle lobby "Start Game" button."""

        query = update.callback_query
        if query is None:
            return

        await self._respond_to_query(query)
        await self._model.start(update, context)

    async def _handle_private(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /private command to create private game."""

        await self._model.create_private_game(update, context)

    async def _handle_join_private(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /join command to join private game by code."""

        await self._model.join_private_game(update, context)

    async def _handle_invite(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /invite command to invite player to private game."""

        await self._model.invite_player(update, context)

    async def _handle_accept_invite(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /accept command (manual acceptance without button)."""

        await update.effective_message.reply_text(
            (
                "ℹ️ To accept an invitation, use the buttons in the "
                "invitation message.\n\n"
                "If you lost the message, ask the host to re-invite you!"
            )
        )

    async def _handle_decline_invite(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /decline command (manual decline without button)."""

        await update.effective_message.reply_text(
            (
                "ℹ️ To decline an invitation, use the buttons in the "
                "invitation message.\n\n"
                "If you lost the message, you can ignore it (invitations "
                "expire in 1 hour)."
            )
        )

    async def _handle_leave_private(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /leave command to leave private game lobby."""

        await self._model.leave_private_game(update, context)

    async def _handle_invite_accept_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle invitation acceptance via inline button."""

        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)
        await self._model.accept_invitation(update, context)

    async def _handle_invite_decline_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle invitation decline via inline button."""

        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)
        await self._model.decline_invitation(update, context)

    async def _handle_private_start_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle private game start button callback."""

        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)
        await self._model.start_private_game(update, context)

    async def _handle_private_leave_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle private game leave button callback."""

        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)
        await self._model.leave_private_game(update, context)

    async def _handle_callback_query(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle legacy callback data fallback."""
        query = update.callback_query

        if not query or not query.data:
            return

        callback_data = query.data
        user_id = getattr(getattr(query, "from_user", None), "id", None)

        if callback_data == "confirm_fold":
            if user_id is None:
                await self._respond_to_query(
                    query,
                    "♻️ Fold action expired. Refresh buttons.",
                    event="FoldConfirm",
                )
                return

            pending = self._pending_fold_confirmations.get(user_id)

            if pending is None:
                await self._respond_to_query(
                    query,
                    "♻️ Fold action expired. Refresh buttons.",
                    event="FoldConfirm",
                )
                return

            result = await self.handle_fold(
                user_id=user_id,
                game=pending.game,
                confirmed=True,
                prepared_action=pending,
            )

            message = query.message
            if message is not None:
                try:
                    await self._view.remove_markup(
                        chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    log_helper.warn(
                        "FoldConfirmCleanupFailed",
                        "Failed to clear confirmation keyboard",
                        error=str(exc),
                    )

            if result:
                await NotificationManager.toast(
                    query,
                    text="🚪 Folded",
                    event="ActionToast",
                )
            else:
                await self._respond_to_query(
                    query,
                    "❌ Unable to process fold. Please try again.",
                    event="FoldConfirm",
                )
            return

        if callback_data == "cancel_fold":
            if user_id is not None:
                self._pending_fold_confirmations.pop(user_id, None)

            message = query.message
            if message is not None:
                try:
                    await self._view.remove_markup(
                        chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    log_helper.warn(
                        "FoldCancelCleanupFailed",
                        "Failed to clear confirmation keyboard",
                        error=str(exc),
                    )

            query_id = getattr(query, "id", None)
            if query_id is not None:
                await self._view.answer_callback_query(
                    query_id,
                    "Fold cancelled",
                )
            else:
                await self._respond_to_query(
                    query,
                    "Fold cancelled",
                    event="FoldConfirmCancel",
                )
            return

        # Acknowledge the callback immediately for legacy handlers
        await self._respond_to_query(query)

        # Legacy fallback for old button format
        player_action_callbacks = {
            PlayerAction.CHECK.value,
            PlayerAction.CALL.value,
            PlayerAction.FOLD.value,
            PlayerAction.ALL_IN.value,
            str(PlayerAction.SMALL.value),
            str(PlayerAction.NORMAL.value),
            str(PlayerAction.BIG.value),
        }

        if callback_data in player_action_callbacks:
            await self._model.middleware_user_turn(
                self._handle_button_clicked
            )(update, context)
        else:
            # Unknown callback - ignore silently
            log_helper.warn(
                "CallbackUnknown",
                callback_data=callback_data,
            )

    def _build_action_toast(
        self,
        action_type: str,
        validation: PlayerActionValidation,
    ) -> str:
        """Return a short toast message describing the applied action."""

        prepared = validation.prepared_action
        if prepared is None:
            return "✅ Action submitted"

        game = prepared.game
        player = prepared.current_player

        if action_type == "check":
            return "✅ Check"

        if action_type == "call":
            call_amount = max(game.max_round_rate - player.round_rate, 0)
            if call_amount > 0:
                return f"✅ Call ${call_amount}"
            return "✅ Check"

        if action_type == "fold":
            return "✅ Folded"

        if action_type == "raise":
            if prepared.raise_amount:
                return f"✅ Raise to ${prepared.raise_amount}"
            return "✅ Raise submitted"

        if action_type == "all_in":
            return "🔥 All-in submitted"

        return "✅ Action submitted"

    async def _handle_raise_callback(
        self,
        query,
        context: CallbackContext,
    ) -> dict:
        """Handle raise_* callbacks that manage the amount selector."""

        data = getattr(query, "data", "") or ""
        result: dict = {"handled": False}

        if not data.startswith("raise_"):
            return result

        message = query.message
        if message is None:
            await NotificationManager.popup(
                query,
                text="❌ Cannot resolve message context",
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        chat_id = message.chat_id
        user_id = getattr(query.from_user, "id", None)
        if user_id is None:
            await NotificationManager.popup(
                query,
                text="❌ Cannot resolve user",
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        game = self._model._game(chat_id)
        if game is None:
            await NotificationManager.popup(
                query,
                text="❌ No active game",
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        live_manager = self._get_live_manager()
        if live_manager is None:
            log_helper.warn(
                "RaiseLiveUpdateFailed",
                "Cannot update live message for raise flow (LiveManager unavailable)",
                user_id=user_id,
                chat_id=chat_id,
            )
            await NotificationManager.popup(
                query,
                text="❌ Raise picker unavailable",
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        current_player = None
        index = getattr(game, "current_player_index", -1)
        if 0 <= index < len(game.players):
            current_player = game.players[index]

        parts = data.split(":")
        if len(parts) < 2:
            await NotificationManager.popup(
                query,
                text="❌ Invalid raise action",
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        action = parts[0]

        def _parse_version_and_game(raw_parts: List[str], start_index: int) -> Tuple[Optional[int], Optional[str]]:
            version_val: Optional[int] = None
            game_val: Optional[str] = None
            idx = start_index
            if idx < len(raw_parts):
                try:
                    version_val = int(raw_parts[idx])
                    idx += 1
                except ValueError:
                    version_val = None
            if idx < len(raw_parts):
                game_val = raw_parts[idx]
            return version_val, game_val

        if action == "raise_amt":
            if len(parts) < 4:
                await NotificationManager.popup(
                    query,
                    text="❌ Invalid raise selection",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            selection_key = parts[1]
            message_version, game_id = _parse_version_and_game(parts, 2)
            if game_id is None or game_id != getattr(game, "id", None):
                await NotificationManager.popup(
                    query,
                    text="♻️ Action expired. Refresh buttons.",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            if (
                message_version is not None
                and message_version != game.get_live_message_version()
            ):
                await NotificationManager.popup(
                    query,
                    text="♻️ Action expired. Refresh buttons.",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            success = await live_manager.present_raise_selector(
                chat_id=chat_id,
                game=game,
                current_player=current_player,
                user_id=user_id,
                message_id=message.message_id,
                message_version=message_version,
                selection_key=selection_key,
            )

            if not success:
                await NotificationManager.popup(
                    query,
                    text="❌ Raise picker unavailable",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            await NotificationManager.popup(
                query,
                text=None,
                show_alert=False,
                event="RaiseSelectAck",
            )

            result["handled"] = True
            return result

        if action == "raise_back":
            message_version, game_id = _parse_version_and_game(parts, 1)
            if game_id is None or game_id != getattr(game, "id", None):
                await NotificationManager.popup(
                    query,
                    text="♻️ Action expired. Refresh buttons.",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            await live_manager.restore_action_keyboard(
                chat_id=chat_id,
                game=game,
                current_player=current_player,
                message_id=message.message_id,
            )
            live_manager.clear_raise_selection(chat_id, user_id)

            await NotificationManager.popup(
                query,
                text=None,
                show_alert=False,
                event="RaiseSelectAck",
            )

            result["handled"] = True
            return result

        if action == "raise_confirm":
            message_version, game_id = _parse_version_and_game(parts, 1)
            if game_id is None or game_id != getattr(game, "id", None):
                await NotificationManager.popup(
                    query,
                    text="♻️ Action expired. Refresh buttons.",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            if (
                message_version is not None
                and message_version != game.get_live_message_version()
            ):
                await NotificationManager.popup(
                    query,
                    text="♻️ Action expired. Refresh buttons.",
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            selection_key, option = live_manager.get_raise_selection(
                chat_id,
                user_id,
            )
            if selection_key is None or option is None:
                await NotificationManager.popup(
                    query,
                    text="Choose an amount first",
                    show_alert=False,
                    event="RaiseSelectAck",
                )
                result["handled"] = True
                return result

            action_type = "raise"
            raise_amount = option.amount
            if option.kind == "all_in":
                action_type = "all_in"
                raise_amount = None

            await live_manager.restore_action_keyboard(
                chat_id=chat_id,
                game=game,
                current_player=current_player,
                message_id=message.message_id,
            )
            live_manager.clear_raise_selection(chat_id, user_id)

            result.update(
                {
                    "handled": False,
                    "action": {
                        "action_type": action_type,
                        "raise_amount": raise_amount,
                        "message_version": message_version,
                        "game_id": game_id,
                    },
                }
            )
            return result

        await NotificationManager.popup(
            query,
            text="❌ Unknown raise action",
            show_alert=False,
            event="RaiseSelectError",
        )
        result["handled"] = True
        return result

    async def _start_raise_selection(
        self,
        query,
        context: CallbackContext,
        *,
        game_id: Optional[str],
        message_version: Optional[int],
    ) -> None:
        """Handle the initial action:raise:start callback."""

        message = query.message
        if message is None:
            await NotificationManager.popup(
                query,
                text="❌ Cannot resolve message context",
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        chat_id = message.chat_id
        user_id = getattr(query.from_user, "id", None)
        if user_id is None:
            await NotificationManager.popup(
                query,
                text="❌ Cannot resolve user",
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        game = self._model._game(chat_id)
        if game is None or game_id != getattr(game, "id", None):
            await NotificationManager.popup(
                query,
                text="♻️ Action expired. Refresh buttons.",
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        if (
            message_version is not None
            and message_version != game.get_live_message_version()
        ):
            await NotificationManager.popup(
                query,
                text="♻️ Action expired. Refresh buttons.",
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        live_manager = self._get_live_manager()
        if live_manager is None:
            log_helper.warn(
                "RaiseLiveUpdateFailed",
                "Cannot update live message for raise flow (LiveManager unavailable)",
                user_id=user_id,
                chat_id=chat_id,
            )
            await NotificationManager.popup(
                query,
                text="❌ Raise picker unavailable",
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        current_player = None
        index = getattr(game, "current_player_index", -1)
        if 0 <= index < len(game.players):
            current_player = game.players[index]

        success = await live_manager.present_raise_selector(
            chat_id=chat_id,
            game=game,
            current_player=current_player,
            user_id=user_id,
            message_id=message.message_id,
            message_version=message_version,
            selection_key=None,
        )

        if not success:
            await NotificationManager.popup(
                query,
                text="❌ Raise picker unavailable",
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        await NotificationManager.popup(
            query,
            text="Pick a raise amount",
            show_alert=False,
            event="RaiseSelectAck",
        )

    async def _handle_action_button(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle new action button format from inline keyboards.

        Supported formats include versioned actions and the dynamic raise flow.
        """

        query = update.callback_query

        if not query or not query.data:
            return

        async def show_popup(
            message: str,
            is_alert: bool = True,
            *,
            fallback_chat_id: int | None = None,
        ) -> bool:
            """Show popups and fall back to chat messaging when needed."""

            return await self._respond_to_query(
                query,
                message,
                show_alert=is_alert,
                event="ActionPopup",
                context=context if fallback_chat_id else None,
                fallback_chat_id=fallback_chat_id,
            )

        try:
            data = query.data

            raise_result = await self._handle_raise_callback(query, context)
            if raise_result.get("handled"):
                return

            override = raise_result.get("action") if raise_result else None
            raise_amount: Optional[int] = None
            message_version: Optional[int] = None
            game_id: Optional[str] = None

            if override:
                action_type = override.get("action_type")
                raise_amount = override.get("raise_amount")
                message_version = override.get("message_version")
                game_id = override.get("game_id")
            else:
                parts = data.split(":")

                if len(parts) < 3:
                    log_helper.warn(
                        "ActionDataInvalid",
                        query_data=query.data,
                        reason="too_few_parts",
                    )
                    await show_popup(
                        "❌ Invalid action format",
                        is_alert=False,
                    )
                    return

                action_type = parts[1]

                if action_type == "raise" and len(parts) >= 3 and parts[2] == "start":
                    idx = 3
                    msg_version: Optional[int] = None
                    if idx < len(parts) - 1:
                        try:
                            msg_version = int(parts[idx])
                            idx += 1
                        except ValueError:
                            msg_version = None

                    if idx >= len(parts):
                        log_helper.warn(
                            "ActionDataInvalid",
                            query_data=query.data,
                            reason="missing_game_id",
                        )
                        await show_popup(
                            "❌ Invalid action format",
                            is_alert=False,
                        )
                        return

                    await self._start_raise_selection(
                        query,
                        context,
                        game_id=parts[idx],
                        message_version=msg_version,
                    )
                    return

                if action_type == "raise":
                    if len(parts) < 4:
                        log_helper.warn(
                            "ActionDataInvalid",
                            query_data=query.data,
                            reason="missing_raise_params",
                        )
                        await self._respond_to_query(
                            query,
                            "❌ Invalid raise format",
                            event="ActionPopup",
                        )
                        return

                    try:
                        raise_amount = int(parts[2])
                    except (ValueError, IndexError):
                        log_helper.warn(
                            "ActionDataInvalid",
                            query_data=query.data,
                            reason="invalid_raise_amount",
                        )
                        await self._respond_to_query(
                            query,
                            "❌ Invalid raise amount",
                            event="ActionPopup",
                        )
                        return

                    if len(parts) == 4:
                        game_id = parts[3]
                    else:
                        try:
                            message_version = int(parts[3])
                        except (ValueError, IndexError):
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="invalid_version",
                            )
                            await self._respond_to_query(
                                query,
                                "❌ Invalid action version",
                                event="ActionPopup",
                            )
                            return
                        try:
                            game_id = parts[4]
                        except IndexError:
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="missing_game_id",
                            )
                            await self._respond_to_query(
                                query,
                                "❌ Invalid action format",
                                event="ActionPopup",
                            )
                            return
                else:
                    if len(parts) == 3:
                        game_id = parts[2]
                    else:
                        try:
                            message_version = int(parts[2])
                        except (ValueError, IndexError):
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="invalid_version",
                            )
                            await self._respond_to_query(
                                query,
                                "❌ Invalid action version",
                                event="ActionPopup",
                            )
                            return
                        try:
                            game_id = parts[3]
                        except IndexError:
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="missing_game_id",
                            )
                            await self._respond_to_query(
                                query,
                                "❌ Invalid action format",
                                event="ActionPopup",
                            )
                            return

            if not game_id:
                await show_popup(
                    "❌ Invalid action format",
                    is_alert=False,
                )
                return

            user_id = query.from_user.id
            chat_id = query.message.chat_id if query.message else None

            if not chat_id:
                await self._respond_to_query(
                    query,
                    "❌ Cannot determine chat context",
                    event="ActionPopup",
                )
                return

            handle_action = getattr(self._model, "handle_player_action", None)

            if handle_action is None:
                log_helper.error(
                    "ActionDispatch",
                    "Model missing handle_player_action method",
                )
                await self._respond_to_query(
                    query,
                    "❌ Action handler not available",
                    event="ActionPopup",
                )
                return

            signature = inspect.signature(handle_action)

            if "action_type" in signature.parameters and hasattr(
                self._model, "prepare_player_action"
            ) and hasattr(self._model, "execute_player_action"):
                cache = RequestCache()

                try:
                    validation: PlayerActionValidation = (
                        await self._model.prepare_player_action(
                            user_id=user_id,
                            chat_id=chat_id,
                            action_type=action_type,
                            raise_amount=raise_amount,
                            message_version=message_version,
                            cache=cache,
                        )
                    )

                    if (
                        not validation.success
                        or validation.prepared_action is None
                    ):
                        error_message = (
                            validation.message
                            or "❌ Action failed - not your turn or invalid action"
                        )
                        await show_popup(
                            error_message,
                            is_alert=True,
                            fallback_chat_id=chat_id,
                        )
                        return

                    prepared_action = validation.prepared_action

                    toast_message = self._build_action_toast(
                        action_type,
                        validation,
                    )

                    if action_type == "fold" and prepared_action is not None:
                        fold_result = await self.handle_fold(
                            user_id=user_id,
                            game=prepared_action.game,
                            prepared_action=prepared_action,
                            query=query,
                        )

                        if fold_result is None:
                            return

                        success = fold_result
                    else:
                        success = await self._model.execute_player_action(
                            prepared_action,
                            cache=cache,
                        )
                finally:
                    cache.log_stats("ActionDispatch")

                if not success:
                    log_helper.warn(
                        "ActionExecution",
                        "Execution of player action failed after validation",
                        action_type=action_type,
                    )
                    await show_popup(
                        "❌ Action failed. Please check the game state.",
                        is_alert=True,
                        fallback_chat_id=chat_id,
                    )
                    return

                # Show instant feedback using toast system
                await NotificationManager.toast(
                    query,
                    text=toast_message,
                    event="ActionToast",
                )
                return

            if "action_type" in signature.parameters:
                # ✅ Toast feedback: instant confirmation for user
                action_toasts = {
                    "fold": "🚪 Folded",
                    "check": "✅ Checked",
                    "call": f"💵 Called ${raise_amount or 0}",
                    "raise": f"📈 Raised ${raise_amount or 0}",
                    "bet": f"💰 Bet ${raise_amount or 0}",
                    "all_in": "🚀 All-in!",
                }

                # Get toast text with safe fallback
                toast_text = action_toasts.get(
                    action_type,
                    "✅ Action confirmed"
                )

                # Send toast (non-blocking, clears button spinner)
                await NotificationManager.toast(
                    query,
                    text=toast_text,
                    event="ActionToast",
                )

                success = await handle_action(
                    user_id=user_id,
                    chat_id=chat_id,
                    action_type=action_type,
                    raise_amount=raise_amount,
                )
            else:
                legacy_map = {
                    "check": PlayerAction.CHECK,
                    "call": PlayerAction.CALL,
                    "fold": PlayerAction.FOLD,
                    "raise": PlayerAction.RAISE_RATE,
                    "all_in": PlayerAction.ALL_IN,
                }

                player_action = legacy_map.get(action_type)

                if player_action is None:
                    await self._respond_to_query(
                        query,
                        "❌ Unknown action type",
                    )
                    return

                legacy_amount = raise_amount if raise_amount is not None else 0

                # ✅ Toast feedback: instant confirmation for user
                action_toasts = {
                    "fold": "🚪 Folded",
                    "check": "✅ Checked",
                    "call": f"💵 Called ${legacy_amount}",
                    "raise": f"📈 Raised ${legacy_amount}",
                    "bet": f"💰 Bet ${legacy_amount}",
                    "all_in": "🚀 All-in!",
                }

                # Get toast text with safe fallback
                toast_text = action_toasts.get(
                    action_type,
                    "✅ Action confirmed"
                )

                # Send toast (non-blocking, clears button spinner)
                await NotificationManager.toast(
                    query,
                    text=toast_text,
                    event="ActionToast",
                )

                success = await handle_action(
                    user_id=str(user_id),
                    chat_id=str(chat_id),
                    game_id=game_id,
                    action=player_action,
                    amount=legacy_amount,
                )

            if not success:
                await self._respond_to_query(
                    query,
                    "❌ Action failed - not your turn or invalid action",
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_helper.error(
                "ActionHandler",
                "Error handling action button",
                error=str(exc),
                exc_info=True,
            )
            await show_popup(
                "❌ An error occurred. Please try again.",
                is_alert=True,
                fallback_chat_id=(
                    query.message.chat_id if query and query.message else None
                ),
            )
        finally:
            view = getattr(self, "_view", None)
            if view and hasattr(view, "get_render_cache_stats"):
                stats = view.get_render_cache_stats()
                total = stats.get("total", 0)
                if total:
                    logger.info(
                        "Render cache stats: %d hits / %d total (%.1f%% hit rate)",
                        stats.get("hits", 0),
                        total,
                        stats.get("hit_rate", 0.0),
                    )

    async def _handle_stake_selection(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle stake level selection from inline keyboard."""
        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)

        # Parse stake level from callback data (e.g., "stake:low" → "low")
        stake_level = query.data.split(":", 1)[1]

        # Call model to create game with selected stake
        await self._model.create_private_game_with_stake(
            update, context, stake_level
        )
