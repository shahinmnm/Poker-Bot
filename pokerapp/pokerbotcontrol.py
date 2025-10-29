#!/usr/bin/env python3

import inspect
import logging

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

from pokerapp.entities import PlayerAction
from pokerapp.notify_utils import LoggerHelper, NotificationManager
from pokerapp.pokerbotmodel import PokerBotModel, PlayerActionValidation

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

        # Acknowledge the callback immediately
        await self._respond_to_query(query)

        callback_data = query.data

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

    async def _handle_action_button(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle new action button format from inline keyboards.

        Format: action:TYPE:GAME_ID or action:raise:AMOUNT:GAME_ID
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
            parts = query.data.split(":")

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

            action_type = parts[1]  # fold, call, check, raise, all_in

            # For raise, format is action:raise:AMOUNT:GAME_ID
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
                message_version = None
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
                # For other actions: action:TYPE:GAME_ID
                raise_amount = None
                message_version = None
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
                validation: PlayerActionValidation = (
                    await self._model.prepare_player_action(
                        user_id=user_id,
                        chat_id=chat_id,
                        action_type=action_type,
                        raise_amount=raise_amount,
                        message_version=message_version,
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

                await NotificationManager.popup(
                    query,
                    text=None,
                    show_alert=False,
                    event="ActionAck",
                )

                success = await self._model.execute_player_action(
                    validation.prepared_action
                )

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

                await self._respond_to_query(query)
                return

            if "action_type" in signature.parameters:
                # Preferred fallback when validation helpers are unavailable
                success = await handle_action(
                    user_id=user_id,
                    chat_id=chat_id,
                    action_type=action_type,
                    raise_amount=raise_amount,
                )
            else:
                # Legacy fallback using PlayerAction enum based API
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

                success = await handle_action(
                    user_id=str(user_id),
                    chat_id=str(chat_id),
                    game_id=game_id,
                    action=player_action,
                    amount=legacy_amount,
                )

            if success:
                await self._respond_to_query(query)
            else:
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
