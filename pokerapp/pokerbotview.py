#!/usr/bin/env python3

import logging

from typing import List, Optional

from telegram import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Bot,
)
from telegram.constants import ParseMode
from pokerapp.cards import Card, Cards
from pokerapp.entities import (
    Game,
    Player,
    PlayerState,
    GameState,
    MessageId,
    ChatId,
    Mention,
)
from pokerapp.live_message import LiveMessageManager


logger = logging.getLogger(__name__)


class PokerBotViewer:
    def __init__(self, bot: Bot):
        self._bot = bot
        self._live_manager = LiveMessageManager(bot=bot, logger=logger)

    _SUIT_EMOJIS = {
        "spades": "♠️",
        "hearts": "♥️",
        "diamonds": "♦️",
        "clubs": "♣️",
        "♠": "♠️",
        "♥": "♥️",
        "♦": "♦️",
        "♣": "♣️",
        "S": "♠️",
        "H": "♥️",
        "D": "♦️",
        "C": "♣️",
    }

    _HAND_INDENT = "     "

    @classmethod
    def _extract_rank_and_suit(cls, card: Card) -> tuple[str, str]:
        card_text = str(card)
        if not card_text:
            return "?", "?"

        rank = card_text[:-1] or card_text
        suit = card_text[-1]

        # Handle cards defined with descriptive suit names.
        if suit not in cls._SUIT_EMOJIS and ":" in card_text:
            parts = card_text.split(":", maxsplit=1)
            rank = parts[0]
            suit = parts[1]

        return rank.upper(), suit

    @staticmethod
    def _format_card(card: Card) -> str:
        """
        Format a card with Unicode symbol and suit emoji.

        Args:
            card: Card object with rank and suit

        Returns:
            Formatted string like "A♠" or "K♥"
        """

        rank_str, suit_key = PokerBotViewer._extract_rank_and_suit(card)
        suit_emoji = PokerBotViewer._SUIT_EMOJIS.get(suit_key, "?")

        return f"{suit_emoji}{rank_str}"

    @classmethod
    def _format_cards_line(cls, cards: List[Card]) -> str:
        if not cards:
            return ""

        return "  ".join(cls._format_card(card) for card in cards)

    @staticmethod
    def _format_board_cards(cards: List[Card]) -> str:
        """
        Format multiple cards for board display.

        Args:
            cards: List of Card objects

        Returns:
            Formatted string like "A♠ K♥ J♣"
        """

        line = PokerBotViewer._format_cards_line(cards)
        return line if line else "Waiting for flop…"

    @classmethod
    def build_hand_panel(
        cls,
        hand_cards: Optional[List[Card]] = None,
        board_cards: Optional[List[Card]] = None,
        *,
        include_table: bool = True,
        pot: Optional[int] = None,
    ) -> str:
        """Construct the emoji panel used across private and group UIs."""

        lines: List[str] = []

        if hand_cards is not None:
            lines.append(f"{cls._HAND_INDENT}🃏 Your hand: ")
            hand_line = cls._format_cards_line(hand_cards) or "—"
            lines.append(f"{cls._HAND_INDENT}{hand_line}")

        if include_table:
            if lines:
                lines.append("")
            lines.append(f"{cls._HAND_INDENT}🧩 Table: ")
            board_line = cls._format_cards_line(board_cards or [])
            if not board_line:
                board_line = "Waiting for flop…"
            lines.append(f"{cls._HAND_INDENT}{board_line}")

        if pot is not None:
            lines.append("")
            lines.append(f"{cls._HAND_INDENT}💰 Pot: ${pot}")

        return "\n".join(lines)

    @classmethod
    def build_table_panel(cls, cards: List[Card], pot: Optional[int]) -> str:
        return cls.build_hand_panel(
            hand_cards=None,
            board_cards=cards,
            include_table=True,
            pot=pot,
        )

    def format_game_state(
        self,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = ""
    ) -> str:
        """
        Format complete game state for the living message.

        Args:
            game: Current game instance
            current_player: Player whose turn it is (None if game ended)
            action_prompt: Text prompting current player's action

        Returns:
            HTML-formatted message text
        """

        lines = []

        round_name = {
            GameState.ROUND_PRE_FLOP: "PRE-FLOP",
            GameState.ROUND_FLOP: "FLOP",
            GameState.ROUND_TURN: "TURN",
            GameState.ROUND_RIVER: "RIVER",
            GameState.FINISHED: "FINISHED",
        }.get(game.state, "STARTING")

        game_header = (
            f"🃏 <b>POKER GAME #{game.id[:8].upper()} - {round_name}</b>"
        )
        lines.append(game_header)

        big_blind = game.table_stake * 2

        lines.append(
            f"💰 Pot: <b>${game.pot}</b> | "
            f"Stakes: {game.table_stake}/{big_blind}"
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        if game.cards_table:
            lines.append("🎴 <b>Board Cards:</b>")
            lines.append(self._format_board_cards(game.cards_table))
            lines.append("")

        player_count = len(game.players)
        lines.append(f"👥 <b>Players ({player_count})</b>")

        for _, player in enumerate(game.players):
            if current_player and player.user_id == current_player.user_id:
                icon = "⏰"
            elif player.state == PlayerState.FOLD:
                icon = "❌"
            elif player.state == PlayerState.ALL_IN:
                icon = "🔥"
            else:
                icon = "✅"

            balance = player.wallet.value()

            bet_display = ""
            if player.round_rate > 0:
                if player.state == PlayerState.ALL_IN:
                    bet_display = f" [ALL-IN ${player.round_rate}]"
                else:
                    bet_display = f" [BET ${player.round_rate}]"
            elif player.state == PlayerState.FOLD:
                bet_display = " [FOLDED]"

            name = player.mention_markdown.strip('`').split(']')[0].strip('[')

            lines.append(f"{icon} {name} ${balance}{bet_display}")

        lines.append("")

        if game.recent_actions:
            lines.append("📢 <b>Recent Activity:</b>")
            for action in game.recent_actions:
                lines.append(f"• {action}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        if action_prompt:
            lines.append(f"👉 {action_prompt}")
            lines.append("")

        return "\n".join(lines)

    def build_action_buttons(
        self,
        game: Game,
        current_player: Player,
    ) -> InlineKeyboardMarkup:
        """
        Build inline keyboard with available actions for current player.

        Args:
            game: Current game instance
            current_player: Player whose turn it is

        Returns:
            InlineKeyboardMarkup with action buttons
        """

        buttons = []

        current_bet = game.max_round_rate
        player_bet = current_player.round_rate
        player_balance = current_player.wallet.value()
        call_amount = current_bet - player_bet

        game_id_str = str(game.id)

        row1 = []
        if call_amount == 0:
            row1.append(
                InlineKeyboardButton(
                    "✅ Check",
                    callback_data=":".join(["action", "check", game_id_str]),
                )
            )
        elif call_amount < player_balance:
            row1.append(
                InlineKeyboardButton(
                    f"💵 Call ${call_amount}",
                    callback_data=":".join(["action", "call", game_id_str]),
                )
            )

        row1.append(
            InlineKeyboardButton(
                "❌ Fold",
                callback_data=":".join(["action", "fold", game_id_str]),
            )
        )
        buttons.append(row1)

        if player_balance > call_amount:
            row2 = []
            big_blind = game.table_stake * 2
            min_raise = max(current_bet * 2, big_blind)

            if player_balance >= min_raise:
                row2.append(
                    InlineKeyboardButton(
                        f"📈 Raise ${min_raise}",
                        callback_data=":".join(
                            ["action", "raise", str(min_raise), game_id_str]
                        ),
                    )
                )

                pot_raise = game.pot
                if pot_raise > min_raise and player_balance >= pot_raise:
                    row2.append(
                        InlineKeyboardButton(
                            f"📊 Raise ${pot_raise}",
                            callback_data=":".join(
                                [
                                    "action",
                                    "raise",
                                    str(pot_raise),
                                    game_id_str,
                                ]
                            ),
                        )
                    )

            if player_balance > 0:
                row2.append(
                    InlineKeyboardButton(
                        f"🔥 All-In ${player_balance}",
                        callback_data=":".join(
                            ["action", "allin", game_id_str]
                        ),
                    )
                )

            buttons.append(row2)

        return InlineKeyboardMarkup(buttons)

    async def send_game_state(
        self,
        chat_id: ChatId,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = "",
    ) -> Optional[int]:
        """Send new game state message to group chat.

        Args:
            chat_id: Target chat ID
            game: Current game instance
            current_player: Player whose turn it is
            action_prompt: Text prompting action

        Returns:
            Message ID of sent message, or None on failure
        """

        try:
            text = self.format_game_state(game, current_player, action_prompt)

            # Build buttons if there's a current player
            reply_markup = None
            if current_player:
                reply_markup = self.build_action_buttons(game, current_player)

            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_notification=True,
                disable_web_page_preview=True,
            )

            return message.message_id
        except Exception as e:
            logger.error(f"Failed to send game state: {e}")
            return None

    async def send_or_update_live_message(
        self,
        chat_id: ChatId,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Bridge helper for LiveMessageManager updates."""

        if self._live_manager is None:
            return None

        return await self._live_manager.send_or_update_game_state(
            chat_id=chat_id,
            game=game,
            current_player=current_player,
        )

    async def update_game_state(
        self,
        chat_id: ChatId,
        message_id: int,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = "",
    ) -> bool:
        """Update existing game state message via edit_message_text.

        Args:
            chat_id: Target chat ID
            message_id: Message ID to edit
            game: Current game instance
            current_player: Player whose turn it is
            action_prompt: Text prompting action

        Returns:
            True if update succeeded, False otherwise
        """

        try:
            text = self.format_game_state(game, current_player, action_prompt)

            # Build buttons if there's a current player
            reply_markup = None
            if current_player:
                reply_markup = self.build_action_buttons(game, current_player)

            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )

            return True
        except Exception as e:
            logger.error(f"Failed to update game state: {e}")
            return False

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

    async def send_or_update_table_cards(
        self,
        chat_id: ChatId,
        cards: Cards,
        *,
        pot: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> Optional[int]:
        """Send or edit the emoji-based community card panel."""

        text = self.build_table_panel(list(cards), pot)

        try:
            if message_id is not None:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return message_id

            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=True,
            )
            return message.message_id
        except Exception as exc:  # pragma: no cover - Telegram failures
            logger.warning(
                "Failed to update table cards for chat %s: %s",
                chat_id,
                exc,
            )
            return message_id

    @staticmethod
    def _get_cards_markup(cards: Cards) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[cards],
            selective=True,
            resize_keyboard=True,
        )

    async def send_cards(
            self,
            chat_id: ChatId,
            cards: Cards,
            mention_markdown: Mention,
            ready_message_id: Optional[MessageId],
    ) -> None:
        markup = PokerBotViewer._get_cards_markup(cards)
        panel_text = self.build_hand_panel(
            hand_cards=list(cards),
            board_cards=[],
        )
        message_text = (
            f"{mention_markdown}\n\n{panel_text}"
            if mention_markdown else panel_text
        )

        send_kwargs = dict(
            chat_id=chat_id,
            text=message_text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_notification=True,
        )

        if ready_message_id is not None:
            send_kwargs["reply_to_message_id"] = ready_message_id

        await self._bot.send_message(**send_kwargs)

    async def send_or_update_private_hand(
        self,
        chat_id: ChatId,
        cards: Cards,
        *,
        table_cards: Optional[Cards] = None,
        mention_markdown: Optional[str] = None,
        message_id: Optional[int] = None,
        disable_notification: bool = True,
        footer: Optional[str] = None,
    ) -> Optional[int]:
        """Send or edit a player's private hand panel in direct chats."""

        panel_text = self.build_hand_panel(
            hand_cards=list(cards),
            board_cards=list(table_cards or []),
            include_table=True,
        )
        message_text = (
            f"{mention_markdown}\n\n{panel_text}"
            if mention_markdown else panel_text
        )

        if footer:
            message_text = f"{message_text}\n\n{footer}"

        reply_markup = PokerBotViewer._get_cards_markup(cards)

        try:
            if message_id is not None:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=message_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return message_id

            message = await self._bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
                disable_notification=disable_notification,
            )
            return message.message_id
        except Exception as exc:  # pragma: no cover - Telegram failures
            logger.warning(
                "Failed to deliver private hand to %s: %s",
                chat_id,
                exc,
            )
            return message_id

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
        user_name: str,
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
            chat_id=chat_id,
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
        chat_id: int,
        inviter_name: str,
        game_code: str,
        stake_name: str,
    ) -> None:
        """Send invitation notification in the originating chat."""

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
            chat_id=chat_id,
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
        min_indicator = (
            f"(min {min_players})" if current_players < min_players else ""
        )
        readiness = (
            "✅ Ready to start!" if can_start else "⏳ Waiting for more players…"
        )

        message = (
            "🔒 PRIVATE GAME LOBBY\n\n"
            f"🎯 Host: {host_name}\n"
            f"🎲 Stakes: {stake_name}\n"
            f"🔑 Code: {game_code}\n\n"
            f"{status_emoji} Players: {current_players}/{max_players} "
            f"{min_indicator}\n\n"
            f"{player_list}\n\n"
            f"{readiness}"
        )

        await self._bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=reply_markup,
        )

    async def send_insufficient_balance_error(
        self,
        chat_id: int,
        balance: int,
        required: int,
        reply_to_message_id: Optional[int] = None,
    ) -> None:
        """Notify user they don't have enough chips."""

        await self._bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ INSUFFICIENT BALANCE\n\n"
                f"Required: {required} chips\n"
                f"Your balance: {balance} chips\n"
                f"Needed: {required - balance} more\n\n"
                "💰 Get free chips with /money command!"
            ),
            reply_to_message_id=reply_to_message_id,
        )

    def build_invitation_message(
        self,
        host_name: str,
        game_code: str,
        stake_config: dict,
    ) -> tuple[str, InlineKeyboardMarkup]:
        """
        Build invitation message with accept/decline buttons.

        Returns:
            (message_text, keyboard)
        """

        small_blind = format(stake_config["small_blind"], ",")
        big_blind = format(stake_config["big_blind"], ",")
        min_buyin = format(stake_config["min_buyin"], ",")

        message = (
            f"🎴 Game Invitation\n\n"
            f"{host_name} invited you to join their private poker game!\n\n"
            f"🎯 Game Code: {game_code}\n\n"
            f"💰 Stakes: {stake_config['name']}\n"
            f" • Small Blind: {small_blind}\n"
            f" • Big Blind: {big_blind}\n"
            f"💵 Min Buy-in: {min_buyin} chips\n\n"
            f"Do you want to join?"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Accept",
                    callback_data="invite_accept:" + str(game_code)
                ),
                InlineKeyboardButton(
                    "❌ Decline",
                    callback_data="invite_decline:" + str(game_code)
                ),
            ]
        ])

        return message, keyboard
