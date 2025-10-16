#!/usr/bin/env python3

from telegram import Update
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    Application,
)

from pokerapp.entities import PlayerAction
from pokerapp.pokerbotmodel import PokerBotModel


class PokerBotCotroller:
    def __init__(self, model: PokerBotModel, application: Application):
        self._model = model

        application.add_handler(
            CommandHandler('ready', self._handle_ready)
        )
        application.add_handler(
            CommandHandler('start', self._handle_start)
        )
        application.add_handler(
            CommandHandler('help', self._handle_help)
        )
        application.add_handler(
            CommandHandler('stop', self._handle_stop)
        )
        application.add_handler(
            CommandHandler('money', self._handle_money)
        )
        application.add_handler(
            CommandHandler('ban', self._handle_ban)
        )
        application.add_handler(
            CommandHandler('cards', self._handle_cards)
        )
        application.add_handler(
            CallbackQueryHandler(
                self._model.middleware_user_turn(
                    self._handle_button_clicked,
                ),
            )
        )

    async def _handle_ready(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.ready(update, context)

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.start(update, context)

    async def _handle_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.show_help(update, context)

    async def _handle_stop(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.stop(user_id=update.effective_message.from_user.id)

    async def _handle_cards(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.send_cards_to_user(update, context)

    async def _handle_ban(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.ban_player(update, context)

    async def _handle_money(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._model.bonus(update, context)

    async def _handle_button_clicked(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
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
            await self._model.raise_rate_bet(update, context, PlayerAction.BIG)
        elif query_data == PlayerAction.ALL_IN.value:
            await self._model.all_in(update, context)
