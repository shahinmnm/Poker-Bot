#!/usr/bin/env python3

import logging

from telegram import BotCommand, Update
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

        application.add_handler(CommandHandler('ready', self._handle_ready))
        application.add_handler(CommandHandler('start', self._handle_start))
        application.add_handler(CommandHandler('stop', self._handle_stop))
        application.add_handler(CommandHandler('money', self._handle_money))
        application.add_handler(CommandHandler('ban', self._handle_ban))
        application.add_handler(CommandHandler('cards', self._handle_cards))
        application.add_handler(CommandHandler('help', self._handle_help))
        application.add_handler(
            CallbackQueryHandler(
                self._model.middleware_user_turn(
                    self._handle_button_clicked,
                ),
            )
        )

        application.post_init = self._post_init

        logger.info("Controller handlers registered")

    async def _post_init(self, application: Application) -> None:
        """Set up bot command descriptions in Telegram UI."""
        commands = [
            BotCommand("start", "Start a new poker game"),
            BotCommand("ready", "Join the next round"),
            BotCommand("money", "Claim daily bonus (dice roll)"),
            BotCommand("cards", "Show your cards again"),
            BotCommand("ban", "Force AFK player to fold (2min+)"),
            BotCommand("stop", "Leave current game"),
            BotCommand("help", "Show game rules and commands"),
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
🎰 **Texas Hold'em Poker Bot**

**Commands:**
/start - Start a new game
/ready - Join the next round
/money - Get daily bonus
/cards - Show your cards
/ban - Force AFK player out (admin)
/help - Show this message

**How to Play:**
1. Add bot to group chat
2. Everyone sends /ready
3. Use /start when enough players ready
4. Game starts with 2 private cards each
5. Bet, check, raise, or fold each round
6. Best 5-card hand wins the pot!

**Betting Rounds:**
• Pre-flop (2 cards each)
• Flop (3 community cards)
• Turn (4th community card)
• River (5th community card)

**Daily Bonus:** Send /money once per day for free chips!

Good luck! 🍀
"""
        await update.effective_message.reply_text(
            help_text,
            parse_mode='Markdown'
        )

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
