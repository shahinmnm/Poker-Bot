#!/usr/bin/env python3

import logging

from typing import Any, Dict, List, Optional, Set, Tuple

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
    PlayerAction,
    MessageId,
    ChatId,
    Mention,
)
from pokerapp.device_detector import DeviceProfile, DeviceType
from pokerapp.i18n import translation_manager
from pokerapp.kvstore import RedisKVStore, ensure_kv
from pokerapp.live_message import LiveMessageManager
from pokerapp.render_cache import RenderCache


logger = logging.getLogger(__name__)


class PokerBotViewer:
    def __init__(
        self,
        bot: Bot,
        logger: logging.Logger = logger,
        kv: Optional[RedisKVStore] = None,
        user_language: str = "en",
    ) -> None:
        self._bot = bot
        if logger is None:
            logger = logging.getLogger(__name__)
        self._logger = logger
        self._kv = ensure_kv(kv)
        self._user_language = user_language
        self._render_cache = RenderCache(self._kv, self._logger)
        self._live_manager = LiveMessageManager(
            bot=bot,
            logger=self._logger,
            kv=self._kv,
            render_cache=self._render_cache,
        )
        self._logger.info("🔍 PokerBotViewer initialized with LiveMessageManager")

    def _t(self, key: str, **kwargs: Any) -> str:
        """Translate message key for the active user language."""

        return translation_manager.translate(
            key,
            language=self._user_language,
            **kwargs,
        )

    def _format_currency(self, amount: int, *, include_symbol: bool = True) -> str:
        """Format currency according to the active language."""

        symbol = "$" if include_symbol else ""
        return translation_manager.format_currency(
            amount,
            language=self._user_language,
            currency_symbol=symbol,
        )

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

    @staticmethod
    def _format_mobile_button_text(
        emoji: str,
        text: str,
        *,
        emoji_scale: float = 1.5,
    ) -> str:
        """Format button text with scaled emoji for mobile readability.

        Args:
            emoji: Button emoji (e.g., "✅", "💰")
            text: Button text (e.g., "CHECK", "CALL $50")
            emoji_scale: Scale multiplier for emoji size

        Returns:
            Formatted button text with spacing
        """

        if emoji_scale > 1.0:
            return f"{emoji}\u200A {text}"

        return f"{emoji} {text}"

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

    def format_game_state(
        self,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = ""
    ) -> str:
        """Delegate formatting to the LiveMessageManager implementation."""

        return self._live_manager._format_game_state(game)

    def build_action_buttons(
        self,
        game: Game,
        current_player: Player,
        version: Optional[int] = None,
        *,
        use_cache: bool = True,
        device_profile: Optional[DeviceProfile] = None,
    ) -> InlineKeyboardMarkup:
        """
        Build inline keyboard with available actions for current player.

        Args:
            game: Current game instance
            current_player: Player whose turn it is

        Returns:
            InlineKeyboardMarkup with action buttons
        """

        if device_profile is None:
            from pokerapp.device_detector import DeviceDetector

            detector = DeviceDetector()
            chat_type = "private" if getattr(game, "chat_id", 0) > 0 else "group"
            device_profile = detector.detect_device(chat_type=chat_type)

        is_mobile = device_profile.device_type == DeviceType.MOBILE
        emoji_scale = getattr(device_profile, "emoji_size_multiplier", 1.0)
        cache_variant = getattr(device_profile.device_type, "value", "default")

        cache_enabled = use_cache and self._render_cache is not None and not is_mobile
        if cache_enabled:
            cached = self._render_cache.get_cached_render(
                game,
                current_player,
                variant=cache_variant,
            )
            if cached and cached.keyboard_layout:
                keyboard = [
                    [InlineKeyboardButton(**btn) for btn in row]
                    for row in cached.keyboard_layout
                ]
                return InlineKeyboardMarkup(keyboard)

        current_bet = max(game.max_round_rate, 0)
        player_bet = max(current_player.round_rate, 0)
        player_balance = max(current_player.wallet.value(), 0)
        call_amount = max(current_bet - player_bet, 0)

        game_id_str = str(game.id)
        version_segment = [str(version)] if version is not None else []

        stake_config = getattr(game, "stake_config", None)
        config_big_blind = getattr(stake_config, "big_blind", 0) if stake_config else 0
        table_big_blind = (getattr(game, "table_stake", 0) or 0) * 2
        baseline_big_blind = max(config_big_blind, table_big_blind, 20)
        min_raise = max(current_bet * 2, baseline_big_blind)

        can_raise = player_balance > call_amount and player_balance >= min_raise

        available_actions: Set[PlayerAction] = {PlayerAction.FOLD}
        if call_amount <= 0:
            available_actions.add(PlayerAction.CHECK)
        elif call_amount < player_balance:
            available_actions.add(PlayerAction.CALL)
        elif player_balance > 0:
            available_actions.add(PlayerAction.ALL_IN)

        if can_raise:
            available_actions.add(PlayerAction.RAISE_RATE)
        if player_balance > 0:
            available_actions.add(PlayerAction.ALL_IN)

        def _callback(action: str, *extra: str) -> str:
            return ":".join(["action", action, *extra, *version_segment, game_id_str])

        if is_mobile:
            def _build_mobile_buttons() -> List[List[InlineKeyboardButton]]:
                buttons: List[List[InlineKeyboardButton]] = []

                if PlayerAction.CHECK in available_actions:
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_text(
                                    "✅",
                                    self._t("action.check"),
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("check"),
                            )
                        ]
                    )
                elif PlayerAction.CALL in available_actions:
                    call_amount_display = self._format_currency(call_amount)
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_text(
                                    "💰",
                                    f"{self._t('action.call')} {call_amount_display}",
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("call"),
                            )
                        ]
                    )

                if PlayerAction.RAISE_RATE in available_actions and player_balance > 0:
                    max_raise = player_balance
                    presets: List[Tuple[str, str, int]] = []

                    if min_raise <= max_raise:
                        presets.append(
                            (
                                "📈",
                                f"{self._t('button.raise')} {self._format_currency(min_raise)}",
                                min_raise,
                            )
                        )

                    pot_amount = max(getattr(game, "pot", 0), 0)
                    two_pot = pot_amount * 2
                    if min_raise <= two_pot <= max_raise:
                        presets.append(
                            (
                                "📈",
                                f"{self._t('button.raise')} 2×{self._format_currency(two_pot)}",
                                two_pot,
                            )
                        )

                    half_stack = max_raise // 2
                    if (
                        half_stack >= min_raise
                        and half_stack <= max_raise
                        and all(option[2] != half_stack for option in presets)
                    ):
                        presets.append(
                            (
                                "💼",
                                f"{self._t('button.raise')} ½×{self._format_currency(half_stack)}",
                                half_stack,
                            )
                        )

                    for i in range(0, len(presets), 2):
                        chunk = presets[i: i + 2]
                        row: List[InlineKeyboardButton] = []
                        for emoji, label, amount in chunk:
                            row.append(
                                InlineKeyboardButton(
                                    self._format_mobile_button_text(
                                        emoji,
                                        label,
                                        emoji_scale=emoji_scale,
                                    ),
                                    callback_data=_callback("raise", str(amount)),
                                )
                            )
                        if row:
                            buttons.append(row)

                if PlayerAction.ALL_IN in available_actions and player_balance > 0:
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_text(
                                    "🔥",
                                    f"{self._t('button.all_in')} {self._format_currency(player_balance)}",
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("all_in"),
                            )
                        ]
                    )

                if PlayerAction.FOLD in available_actions:
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_text(
                                    "❌",
                                    self._t("action.fold"),
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("fold"),
                            )
                        ]
                    )

                return buttons

            mobile_buttons = _build_mobile_buttons()
            if mobile_buttons:
                return InlineKeyboardMarkup(mobile_buttons)

        buttons: List[List[InlineKeyboardButton]] = []

        row1: List[InlineKeyboardButton] = []
        if PlayerAction.CHECK in available_actions:
            row1.append(
                InlineKeyboardButton(
                    self._t("button.check"),
                    callback_data=_callback("check"),
                )
            )
        elif PlayerAction.CALL in available_actions:
            call_amount_display = self._format_currency(
                call_amount,
                include_symbol=False,
            )
            row1.append(
                InlineKeyboardButton(
                    self._t("button.call", amount=call_amount_display),
                    callback_data=_callback("call"),
                )
            )

        row1.append(
            InlineKeyboardButton(
                self._t("button.fold"),
                callback_data=_callback("fold"),
            )
        )
        buttons.append(row1)

        pot_amount = getattr(game, "pot", 0)

        def _format_raise_button(amount: int) -> str:
            formatted_amount = LiveMessageManager._format_chips(amount, width=4)
            return f"{self._t('button.raise')} {formatted_amount}"

        raise_amounts: List[int] = []
        if PlayerAction.RAISE_RATE in available_actions:
            raise_amounts.append(min_raise)
            if pot_amount > min_raise and player_balance >= pot_amount:
                raise_amounts.append(pot_amount)

        if player_balance > 0:
            row2: List[InlineKeyboardButton] = []

            if PlayerAction.RAISE_RATE in available_actions:
                row2.append(
                    InlineKeyboardButton(
                        _format_raise_button(min_raise),
                        callback_data=_callback("raise", str(min_raise)),
                    )
                )

            row2.append(
                InlineKeyboardButton(
                    f"{self._t('button.all_in')} {LiveMessageManager._format_chips(player_balance, width=4)}",
                    callback_data=_callback("all_in"),
                )
            )

            buttons.append(row2)

        extra_amounts = raise_amounts[1:]
        if extra_amounts:
            for i in range(0, len(extra_amounts), 2):
                row: List[InlineKeyboardButton] = []
                for amount in extra_amounts[i: i + 2]:
                    row.append(
                        InlineKeyboardButton(
                            _format_raise_button(amount),
                            callback_data=_callback("raise", str(amount)),
                        )
                    )
                buttons.append(row)

        markup = InlineKeyboardMarkup(buttons)

        if cache_enabled and buttons:
            layout = [
                [
                    {"text": btn.text, "callback_data": btn.callback_data}
                    for btn in row
                ]
                for row in buttons
            ]
            self._render_cache.cache_render_result(
                game,
                current_player,
                keyboard_layout=layout,
                variant=cache_variant,
            )

        return markup

    def get_render_cache_stats(self) -> Dict[str, Any]:
        """Expose render cache performance metrics."""

        return self._render_cache.get_stats()

    def invalidate_render_cache(self, game: Game) -> None:
        """Invalidate cached render results for the given game."""

        game_id = getattr(game, "id", "")
        self._render_cache.invalidate_game(game_id)
        if hasattr(self._live_manager, "invalidate_render_cache"):
            self._live_manager.invalidate_render_cache(game)

    async def show_fold_confirmation(
        self,
        chat_id: int,
        pot_size: int,
        player_invested: int,
    ) -> None:
        """Display a high-stakes fold confirmation dialog."""

        investment_pct = (
            (player_invested / pot_size) * 100 if pot_size > 0 else 0
        )
        message = (
            "⚠️ <b>FOLD CONFIRMATION</b>\n\n"
            f"💰 Pot: ${pot_size:,}\n"
            f"💸 Your Investment: ${player_invested:,} ({investment_pct:.1f}%)\n\n"
            "Are you sure you want to fold?"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Yes, Fold", callback_data="confirm_fold"
                    ),
                    InlineKeyboardButton(
                        "↩️ Cancel", callback_data="cancel_fold"
                    ),
                ]
            ]
        )

        await self._bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_notification=True,
            disable_web_page_preview=True,
        )

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
            next_version = None
            if current_player:
                next_version = game.next_live_message_version()
                reply_markup = self.build_action_buttons(
                    game,
                    current_player,
                    version=next_version,
                )

            message = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_notification=True,
                disable_web_page_preview=True,
            )

            if next_version is not None:
                game.mark_live_message_version(next_version)

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

        return await self._live_manager.send_or_update_live_message(
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
            next_version = None
            if current_player:
                next_version = game.next_live_message_version()
                reply_markup = self.build_action_buttons(
                    game,
                    current_player,
                    version=next_version,
                )

            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )

            if next_version is not None:
                game.mark_live_message_version(next_version)

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

    async def answer_callback_query(
        self,
        query_id: str,
        text: Optional[str] = None,
        *,
        show_alert: bool = False,
    ) -> None:
        """Acknowledge a callback query by its identifier."""

        await self._bot.answer_callback_query(
            callback_query_id=query_id,
            text=text,
            show_alert=show_alert,
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
        """Send localized insufficient balance error."""

        balance_display = self._format_currency(balance)
        required_display = self._format_currency(required)

        text = self._t(
            "error.insufficient_funds_detail",
            balance=balance_display,
            required=required_display,
        )

        await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )

    async def send_lobby_message(
        self,
        chat_id: int,
        player_count: int,
        max_players: int,
        players: List[str],
        is_host: bool = False,
    ) -> Message:
        """Send localized lobby status message."""

        title = self._t("lobby.title")
        player_text = self._t("lobby.players", count=player_count, max=max_players)

        text_parts = [title, "", player_text]

        for i, player_name in enumerate(players):
            if i == 0 and is_host:
                text_parts.append(f"{self._t('lobby.host')} {player_name}")
            else:
                text_parts.append(f"• {player_name}")

        if player_count < 2:
            text_parts.append("")
            text_parts.append(self._t("lobby.waiting"))
        elif player_count >= 2:
            text_parts.append("")
            text_parts.append(self._t("lobby.ready_to_start"))

        return await self._bot.send_message(
            chat_id=chat_id,
            text="\n".join(text_parts),
        )

    async def send_game_started_message(
        self,
        chat_id: int,
    ) -> None:
        """Send localized game started notification."""

        text = self._t("msg.game_started")

        await self._bot.send_message(
            chat_id=chat_id,
            text=text,
        )

    def format_player_action(
        self,
        player_name: str,
        action: PlayerAction,
        amount: int = 0,
    ) -> str:
        """Format localized player action description."""

        amount_display = self._format_currency(amount, include_symbol=False)

        action_messages = {
            PlayerAction.FOLD: self._t("msg.player_folded", player=player_name),
            PlayerAction.CHECK: self._t("msg.player_checked", player=player_name),
            PlayerAction.CALL: self._t(
                "msg.player_called",
                player=player_name,
                amount=amount_display,
            ),
            PlayerAction.RAISE_RATE: self._t(
                "msg.player_raised",
                player=player_name,
                amount=amount_display,
            ),
            PlayerAction.ALL_IN: self._t(
                "msg.player_all_in",
                player=player_name,
                amount=amount_display,
            ),
        }

        return action_messages.get(
            action,
            f"{player_name}: {action.value}",
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
