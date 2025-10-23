#!/usr/bin/env python3

import logging

from telegram import (
    BotCommand,
    CallbackQuery,
    Update,
)
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
)

from pokerapp.entities import PlayerAction
from pokerapp.pokerbotmodel import PokerBotModel

logger = logging.getLogger(__name__)


class PokerBotController:
    """Controller for handling Telegram updates and routing to model."""

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
        application.add_handler(
            CallbackQueryHandler(
                self.handle_action_button,
                pattern=r"^action:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._handle_callback_query)
        )

        application.post_init = self._post_init

        logger.info("Controller handlers registered")

    async def _post_init(self, application: Application) -> None:
        """Set up bot command descriptions in Telegram UI."""
        commands = [
            BotCommand("start", "🎰 Start a new poker game"),
            BotCommand("ready", "✋ Join the next round"),
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
            logger.info("Bot commands registered in Telegram UI")
        except Exception as exc:  # pragma: no cover - Telegram API
            logger.error("Failed to register commands: %s", exc)

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

🎰 /start - Start a new game
✋ /ready - Join the next round
🛑 /stop - Leave current game

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
            await self._model.call_check(update, context)
        elif query_data == PlayerAction.CALL.value:
            await self._model.call_check(update, context)
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

    async def _handle_callback_query(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Route callback queries from inline keyboard buttons."""
        query = update.callback_query

        if not query or not query.data:
            return

        # Acknowledge the callback immediately
        await query.answer()

        callback_data = query.data

        # Route to appropriate handler based on callback data prefix
        if callback_data.startswith("stake:"):
            await self._handle_stake_selection(update, context)
        elif callback_data.startswith("invite_accept:"):
            await self._model.accept_invitation(update, context)
        elif callback_data.startswith("invite_decline:"):
            await self._model.decline_invitation(update, context)
        elif callback_data.startswith("private_start:"):
            await self._model.start_private_game(update, context)
        elif callback_data.startswith("private_leave:"):
            await self._model.leave_private_game(update, context)
        else:
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
                logger.warning("Unknown callback data: %s", callback_data)

    async def _handle_stake_selection(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle stake level selection from inline keyboard."""
        query = update.callback_query

        if not query or not query.data:
            return

        # Parse stake level from callback data (e.g., "stake:low" → "low")
        stake_level = query.data.split(":", 1)[1]

        # Call model to create game with selected stake
        await self._model.create_private_game_with_stake(
            update, context, stake_level
        )

    async def handle_action_button(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """
        Handle inline button callbacks for player actions.

        Callback data format: "action:{action_type}:{game_id}"

        For raises: "action:raise:{amount}:{game_id}"
        """
        query: CallbackQuery = update.callback_query

        if not query or not query.data:
            return

        # Parse callback data
        parts = query.data.split(":")

        if len(parts) < 3 or parts[0] != "action":
            await query.answer("❌ Invalid action format")
            return

        action_type = parts[1]

        # Handle raise which has amount in data
        if action_type == "raise":
            if len(parts) != 4:
                await query.answer("❌ Invalid raise format")
                return

            try:
                amount = int(parts[2])
                game_id = parts[3]
            except ValueError:
                await query.answer("❌ Invalid raise amount")
                return
        else:
            game_id = parts[2]
            amount = 0

        try:
            # Map action types to PlayerAction enum
            action_map = {
                "check": PlayerAction.CHECK,
                "call": PlayerAction.CALL,
                "fold": PlayerAction.FOLD,
                "raise": PlayerAction.RAISE_RATE,
                "allin": PlayerAction.ALL_IN,
            }

            if action_type not in action_map:
                await query.answer("❌ Unknown action")
                return

            player_action = action_map[action_type]

            # Process action through model
            success = await self._model.handle_player_action(
                update=update,
                context=context,
                game_id=game_id,
                action=player_action,
                amount=amount,
            )

            if success:
                # Acknowledge the button press
                await query.answer()
            else:
                await query.answer(
                    "❌ Action failed. Not your turn or invalid move."
                )
        except Exception as e:  # pragma: no cover - defensive logging
            logger.error(f"Error handling action button: {e}")
            await query.answer("❌ An error occurred")
