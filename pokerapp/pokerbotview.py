#!/usr/bin/env python3

from telegram import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Bot,
    InputMediaPhoto,
)
from telegram.constants import ParseMode
from io import BytesIO

from pokerapp.desk import DeskImageGenerator
from pokerapp.cards import Cards
from pokerapp.entities import (
    Game,
    Player,
    PlayerAction,
    MessageId,
    ChatId,
    Mention,
    Money,
)


class PokerBotViewer:
    def __init__(self, bot: Bot):
        self._bot = bot
        self._desk_generator = DeskImageGenerator()

    async def send_message(
        self,
        chat_id: ChatId,
        text: str,
        reply_markup: ReplyKeyboardMarkup = None,
    ) -> None:
        await self._bot.send_message(
            chat_id=chat_id,
            parse_mode=ParseMode.MARKDOWN,
            text=text,
            reply_markup=reply_markup,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    async def send_photo(self, chat_id: ChatId) -> None:
        # TODO: photo to args.
        with open("./assets/poker_hand.jpg", 'rb') as photo:
            await self._bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=True,
            )

    async def send_dice_reply(
        self,
        chat_id: ChatId,
        message_id: MessageId,
        emoji='🎲',
    ) -> Message:
        return await self._bot.send_dice(
            reply_to_message_id=message_id,
            chat_id=chat_id,
            disable_notification=True,
            emoji=emoji,
        )

    async def send_message_reply(
        self,
        chat_id: ChatId,
        message_id: MessageId,
        text: str,
    ) -> None:
        await self._bot.send_message(
            reply_to_message_id=message_id,
            chat_id=chat_id,
            parse_mode=ParseMode.MARKDOWN,
            text=text,
            disable_notification=True,
        )

    async def send_desk_cards_img(
        self,
        chat_id: ChatId,
        cards: Cards,
        caption: str = "",
        disable_notification: bool = True,
    ) -> Message:
        im_cards = self._desk_generator.generate_desk(cards)
        bio = BytesIO()
        bio.name = 'desk.png'
        im_cards.save(bio, 'PNG')
        bio.seek(0)
        return await self._bot.send_media_group(
            chat_id=chat_id,
            media=[
                InputMediaPhoto(
                    media=bio,
                    caption=caption,
                ),
            ],
            disable_notification=disable_notification,
        )[0]

    @ staticmethod
    def _get_cards_markup(cards: Cards) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[cards],
            selective=True,
            resize_keyboard=True,
        )

    @ staticmethod
    def _get_turns_markup(
        check_call_action: PlayerAction
    ) -> InlineKeyboardMarkup:
        keyboard = [[
            InlineKeyboardButton(
                text=PlayerAction.FOLD.value,
                callback_data=PlayerAction.FOLD.value,
            ),
            InlineKeyboardButton(
                text=PlayerAction.ALL_IN.value,
                callback_data=PlayerAction.ALL_IN.value,
            ),
            InlineKeyboardButton(
                text=check_call_action.value,
                callback_data=check_call_action.value,
            ),
        ], [
            InlineKeyboardButton(
                text=str(PlayerAction.SMALL.value) + "$",
                callback_data=str(PlayerAction.SMALL.value)
            ),
            InlineKeyboardButton(
                text=str(PlayerAction.NORMAL.value) + "$",
                callback_data=str(PlayerAction.NORMAL.value)
            ),
            InlineKeyboardButton(
                text=str(PlayerAction.BIG.value) + "$",
                callback_data=str(PlayerAction.BIG.value)
            ),
        ]]

        return InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )

    async def send_cards(
            self,
            chat_id: ChatId,
            cards: Cards,
            mention_markdown: Mention,
            ready_message_id: str,
    ) -> None:
        markup = PokerBotViewer._get_cards_markup(cards)
        await self._bot.send_message(
            chat_id=chat_id,
            text="Showing cards to " + mention_markdown,
            reply_markup=markup,
            reply_to_message_id=ready_message_id,
            parse_mode=ParseMode.MARKDOWN,
            disable_notification=True,
        )

    @ staticmethod
    def define_check_call_action(
        game: Game,
        player: Player,
    ) -> PlayerAction:
        if player.round_rate == game.max_round_rate:
            return PlayerAction.CHECK
        return PlayerAction.CALL

    async def send_turn_actions(
            self,
            chat_id: ChatId,
            game: Game,
            player: Player,
            money: Money,
    ) -> None:
        if len(game.cards_table) == 0:
            cards_table = "no cards"
        else:
            cards_table = " ".join(game.cards_table)
        text = (
            "Turn of {}\n" +
            "{}\n" +
            "Money: *{}$*\n" +
            "Max round rate: *{}$*"
        ).format(
            player.mention_markdown,
            cards_table,
            money,
            game.max_round_rate,
        )
        check_call_action = PokerBotViewer.define_check_call_action(
            game, player
        )
        markup = PokerBotViewer._get_turns_markup(check_call_action)
        await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_notification=True,
        )

    async def remove_markup(
        self,
        chat_id: ChatId,
        message_id: MessageId,
    ) -> None:
        await self._bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
        )

    async def remove_message(
        self,
        chat_id: ChatId,
        message_id: MessageId,
    ) -> None:
        await self._bot.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )

    async def send_stake_selection(
        self,
        chat_id: int,
        user_id: int,
    ) -> None:
        """Send stake selection menu for private game creation."""

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    "💎 Micro (5/10) - 200 min",
                    callback_data="stake:micro",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎯 Low (10/20) - 400 min",
                    callback_data="stake:low",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎲 Medium (25/50) - 1K min",
                    callback_data="stake:medium",
                ),
            ],
            [
                InlineKeyboardButton(
                    "💰 High (50/100) - 2K min",
                    callback_data="stake:high",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👑 Premium (100/200) - 4K min",
                    callback_data="stake:premium",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="stake:cancel",
                ),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await self._bot.send_message(
            chat_id=user_id,
            text=(
                "🔒 CREATE PRIVATE GAME\n\n"
                "Choose your stake level:\n\n"
                "💎 Micro - Small stakes, great for practice\n"
                "🎯 Low - Casual games with friends\n"
                "🎲 Medium - Standard poker action\n"
                "💰 High - Serious players only\n"
                "👑 Premium - High rollers table\n\n"
                "⚠️ All players need minimum buy-in to join!"
            ),
            reply_markup=reply_markup,
        )

    async def send_player_invite(
        self,
        inviter_id: int,
        inviter_name: str,
        invitee_id: int,
        game_code: str,
        stake_name: str,
    ) -> None:
        """Send invitation to specific player."""

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Accept Invitation",
                    callback_data=f"invite_accept:{game_code}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Decline",
                    callback_data=f"invite_decline:{game_code}",
                ),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await self._bot.send_message(
            chat_id=invitee_id,
            text=(
                "🎰 PRIVATE GAME INVITATION\n\n"
                f"{inviter_name} invited you to a private poker game!\n\n"
                f"🎲 Stakes: {stake_name}\n"
                f"🔑 Game Code: {game_code}\n\n"
                "Will you join?"
            ),
            reply_markup=reply_markup,
        )

    async def send_private_game_status(
        self,
        chat_id: int,
        host_name: str,
        stake_name: str,
        game_code: str,
        current_players: int,
        max_players: int,
        min_players: int,
        player_names: list,
        can_start: bool,
    ) -> None:
        """Send current status of private game lobby."""

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        player_list = "\n".join([f" • {name}" for name in player_names])

        keyboard = []
        if can_start:
            keyboard.append([
                InlineKeyboardButton(
                    "🎰 START GAME",
                    callback_data=f"private_start:{game_code}",
                ),
            ])

        keyboard.append([
            InlineKeyboardButton(
                "📨 Invite Player",
                callback_data=f"private_invite:{game_code}",
            ),
        ])
        keyboard.append([
            InlineKeyboardButton(
                "🚪 Leave Lobby",
                callback_data=f"private_leave:{game_code}",
            ),
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        status_emoji = "✅" if can_start else "⏳"
        min_indicator = f"(min {min_players})" if current_players < min_players else ""

        await self._bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 PRIVATE GAME LOBBY\n\n"
                f"🎯 Host: {host_name}\n"
                f"🎲 Stakes: {stake_name}\n"
                f"🔑 Code: {game_code}\n\n"
                f"{status_emoji} Players: {current_players}/{max_players} {min_indicator}\n\n"
                f"{player_list}\n\n"
                f"{'✅ Ready to start!' if can_start else '⏳ Waiting for more players…'}"
            ),
            reply_markup=reply_markup,
        )

    async def send_insufficient_balance_error(
        self,
        chat_id: int,
        user_id: int,
        required: int,
        current: int,
    ) -> None:
        """Notify user they don't have enough chips."""

        await self._bot.send_message(
            chat_id=user_id,
            text=(
                "❌ INSUFFICIENT BALANCE\n\n"
                f"Required: {required} chips\n"
                f"Your balance: {current} chips\n"
                f"Needed: {required - current} more\n\n"
                "💰 Get free chips with /money command!"
            ),
        )
