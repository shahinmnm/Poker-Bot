#!/usr/bin/env python3

import logging
from functools import lru_cache

from typing import Any, Dict, List, Optional, Set, Tuple

from telegram import (
    Message,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
    Bot,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from pokerapp.cards import Card, Cards
from pokerapp.entities import (
    Game,
    Player,
    PlayerAction,
    MessageId,
    ChatId,
    Mention,
    MenuContext,
)
from pokerapp.device_detector import DeviceProfile, DeviceType
from pokerapp.i18n import LanguageContext, translation_manager
from pokerapp.kvstore import RedisKVStore, ensure_kv
from pokerapp.live_message import (
    LiveMessageManager,
    UnicodeTextFormatter,
    normalize_numbers,
)
from pokerapp.keyboard_utils import (
    rehydrate_keyboard_layout,
    serialise_keyboard_layout,
)
from pokerapp.render_cache import RenderCache
from .menu_state import MenuLocation, get_breadcrumb_path, MENU_HIERARCHY


logger = logging.getLogger(__name__)


class ViewerTextKeys:
    HAND_HEADER = "viewer.hand.header"
    HAND_EMPTY = "viewer.hand.empty"
    TABLE_HEADER = "viewer.table.header"
    TABLE_WAITING = "viewer.table.waiting"
    POT = "viewer.hand.pot"
    FOLD_CONFIRM_BODY = "popup.fold_confirmation.body"
    FOLD_CONFIRM_CONFIRM_BUTTON = "popup.fold_confirmation.confirm_button"
    FOLD_CONFIRM_CANCEL_BUTTON = "popup.fold_confirmation.cancel_button"


class PokerBotViewer:
    def __init__(
        self,
        bot: Bot,
        logger: logging.Logger = logger,
        kv: Optional[RedisKVStore] = None,
        user_language: Optional[str] = None,
        language_context: Optional[LanguageContext] = None,
    ) -> None:
        self._bot = bot
        if logger is None:
            logger = logging.getLogger(__name__)
        self._logger = logger
        self._translation_manager = translation_manager
        self._kv = ensure_kv(kv)
        if user_language is None:
            user_language = translation_manager.DEFAULT_LANGUAGE
        if language_context is None:
            language_context = translation_manager.get_language_context(user_language)
        self._language_context: LanguageContext = language_context
        self._user_language = self._language_context.code
        self._render_cache = RenderCache(self._kv, self._logger)
        self._live_manager = LiveMessageManager(
            bot=bot,
            logger=self._logger,
            kv=self._kv,
            render_cache=self._render_cache,
        )
        self._live_manager.set_language_metadata(
            code=self._language_context.code,
            direction=self._language_context.direction,
            font=self._language_context.font,
        )
        self._logger.info("🔍 PokerBotViewer initialized with LiveMessageManager")

    def _get_language_context_for_user(
        self,
        *,
        user_id: Optional[int] = None,
        language: Optional[str] = None,
    ) -> LanguageContext:
        """Resolve a language context for a specific user without mutating state."""

        if user_id is not None:
            language = translation_manager.get_user_language_or_detect(user_id)

        if language is None:
            return self._language_context

        return translation_manager.get_language_context(language)

    def _t(
        self,
        key: str,
        *,
        context: Optional[LanguageContext] = None,
        **kwargs: Any,
    ) -> str:
        """Translate message key for the provided or active user language."""

        language_context = context or self._language_context
        return translation_manager.t(
            key,
            lang=language_context.code,
            **kwargs,
        )

    def _format_currency(
        self,
        amount: int,
        *,
        include_symbol: bool = True,
        context: Optional[LanguageContext] = None,
    ) -> str:
        """Format currency according to the provided or active language."""

        language_context = context or self._language_context
        symbol = "$" if include_symbol else ""
        return translation_manager.format_currency(
            amount,
            language=language_context.code,
            currency_symbol=symbol,
        )

    def set_language_context(
        self,
        language: Optional[str] = None,
        *,
        user_id: Optional[int] = None,
    ) -> None:
        """Update rendering metadata for subsequent responses."""

        resolved_code = translation_manager.resolve_language(
            user_id=user_id,
            lang=language,
        )
        self._language_context = translation_manager.get_language_context(resolved_code)
        self._user_language = self._language_context.code
        if self._live_manager is not None:
            self._live_manager.set_language_metadata(
                code=self._language_context.code,
                direction=self._language_context.direction,
                font=self._language_context.font,
            )

    @property
    def language_context(self) -> LanguageContext:
        """Expose active language metadata for consumers."""

        return self._language_context

    @property
    def i18n(self):
        """Provide access to the translation manager (backwards compatibility)."""

        return translation_manager

    def _apply_direction(
        self,
        text: str,
        *,
        context: Optional[LanguageContext] = None,
    ) -> str:
        if not text:
            return text

        language_context = context or self._language_context

        if language_context.direction != "rtl":
            return text
        if text.startswith("\u202B") and text.endswith("\u202C"):
            return text
        return f"\u202B{text}\u202C"

    def _localize_text(
        self,
        text: str,
        *,
        context: Optional[LanguageContext] = None,
    ) -> str:
        return self._apply_direction(text, context=context)

    async def _send_localized_message(
        self,
        *,
        chat_id: int,
        text: str,
        context: Optional[LanguageContext] = None,
        **kwargs: Any,
    ):
        """Send message with direction-aware wrapping."""

        plain_text = UnicodeTextFormatter.strip_all_html(text)
        normalized_text = normalize_numbers(plain_text)
        localized = self._localize_text(normalized_text, context=context)
        return await self._bot.send_message(chat_id=chat_id, text=localized, **kwargs)

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

    def build_hand_panel(
        self,
        hand_cards: Optional[List[Card]] = None,
        board_cards: Optional[List[Card]] = None,
        *,
        include_table: bool = True,
        pot: Optional[int] = None,
        context: Optional[LanguageContext] = None,
    ) -> str:
        """Construct the emoji panel used across private and group UIs."""

        language_context = context or self._language_context
        lines: List[str] = []

        if hand_cards is not None:
            lines.append(
                f"{self._HAND_INDENT}{self._t(ViewerTextKeys.HAND_HEADER, context=language_context)}"
            )
            hand_line = self._format_cards_line(hand_cards) or self._t(
                ViewerTextKeys.HAND_EMPTY,
                context=language_context,
            )
            lines.append(f"{self._HAND_INDENT}{hand_line}")

        if include_table:
            if lines:
                lines.append("")
            lines.append(
                f"{self._HAND_INDENT}{self._t(ViewerTextKeys.TABLE_HEADER, context=language_context)}"
            )
            board_line = self._format_cards_line(board_cards or [])
            if not board_line:
                board_line = self._t(
                    ViewerTextKeys.TABLE_WAITING,
                    context=language_context,
                )
            lines.append(f"{self._HAND_INDENT}{board_line}")

        if pot is not None:
            lines.append("")
            pot_display = self._format_currency(
                pot,
                context=language_context,
            )
            lines.append(
                f"{self._HAND_INDENT}{self._t(ViewerTextKeys.POT, amount=pot_display, context=language_context)}"
            )

        return "\n".join(lines)

    def format_game_state(
        self,
        game: Game,
        current_player: Optional[Player] = None,
        action_prompt: str = ""
    ) -> str:
        """Delegate formatting to the LiveMessageManager implementation."""

        return self._live_manager._format_game_state(
            game, current_player=current_player
        )

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
        cache_variant = f"{getattr(device_profile.device_type, 'value', 'default')}:{self._language_context.code}"

        if self._live_manager is not None:
            markup, _ = self._live_manager._build_action_inline_keyboard(
                game=game,
                player=current_player,
                version=version,
                use_cache=use_cache,
                device_profile=device_profile,
            )
            if markup is not None:
                return markup

        cache_enabled = use_cache and self._render_cache is not None and not is_mobile
        if cache_enabled:
            cached = self._render_cache.get_cached_render(
                game,
                current_player,
                variant=cache_variant,
            )
            if cached and cached.keyboard_layout:
                return rehydrate_keyboard_layout(
                    cached.keyboard_layout,
                    version=version,
                )

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
            self._render_cache.cache_render_result(
                game,
                current_player,
                keyboard_layout=serialise_keyboard_layout(
                    markup.inline_keyboard,
                    version=version,
                ),
                variant=cache_variant,
            )

        return markup

    def _build_raise_menu(
        self,
        game: Game,
        current_player: Player,
        *,
        version: Optional[int] = None,
        selected_key: Optional[str] = None,
    ) -> Optional[InlineKeyboardMarkup]:
        """Proxy helper that leverages the live manager's raise builder."""

        if self._live_manager is None:
            return None

        options = self._live_manager._compute_raise_options(game, current_player)
        return self._live_manager._build_raise_selection_keyboard(
            game=game,
            player=current_player,
            version=version,
            options=options,
            selected_key=selected_key,
        )

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
        *,
        confirmation_key: str,
        user_id: Optional[int] = None,
    ) -> None:
        """Display a high-stakes fold confirmation dialog.

        Args:
            chat_id: Destination chat identifier.
            pot_size: Current pot value for context.
            player_invested: Amount the player has contributed to the pot.
            confirmation_key: Unique identifier used to correlate callbacks.
        """

        investment_pct = (
            (player_invested / pot_size) * 100 if pot_size > 0 else 0
        )
        language_context = self._get_language_context_for_user(user_id=user_id)
        formatted_pot = translation_manager.format_currency(
            pot_size,
            language=language_context.code,
        )
        formatted_investment = translation_manager.format_currency(
            player_invested,
            language=language_context.code,
        )
        message = self._t(
            ViewerTextKeys.FOLD_CONFIRM_BODY,
            pot=formatted_pot,
            investment=formatted_investment,
            percentage=f"{investment_pct:.1f}",
            context=language_context,
        )

        confirm_callback = (
            f"confirm_fold:{confirmation_key}" if confirmation_key else "confirm_fold"
        )
        cancel_callback = (
            f"cancel_fold:{confirmation_key}" if confirmation_key else "cancel_fold"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        self._t(
                            ViewerTextKeys.FOLD_CONFIRM_CONFIRM_BUTTON,
                            context=language_context,
                        ),
                        callback_data=confirm_callback,
                    ),
                    InlineKeyboardButton(
                        self._t(
                            ViewerTextKeys.FOLD_CONFIRM_CANCEL_BUTTON,
                            context=language_context,
                        ),
                        callback_data=cancel_callback,
                    ),
                ]
            ]
        )

        plain_message = UnicodeTextFormatter.strip_all_html(message)
        await self._send_localized_message(
            chat_id=chat_id,
            text=plain_message,
            reply_markup=keyboard,
            disable_notification=True,
            disable_web_page_preview=True,
            context=language_context,
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

            clean_text = UnicodeTextFormatter.strip_all_html(text)
            message = await self._send_localized_message(
                chat_id=chat_id,
                text=clean_text,
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

            plain_text = UnicodeTextFormatter.strip_all_html(text)
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=self._localize_text(plain_text),
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
        plain_text = UnicodeTextFormatter.strip_all_html(text)
        await self._send_localized_message(
            chat_id=chat_id,
            text=plain_text,
            reply_markup=reply_markup,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    async def send_menu(
        self,
        chat_id: int,
        menu_context: MenuContext,
    ) -> None:
        """Public entry point for sending a context-aware menu."""

        await self._send_menu(chat_id, menu_context)

    async def show_main_menu(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Display the primary reply keyboard with WebApp entry point."""

        if update.effective_chat is None:
            self._logger.warning("No effective chat available to show main menu")
            return

        keyboard = [
            [
                KeyboardButton(
                    "🎮 باز کردن بازی",
                    web_app=WebAppInfo(url="https://poker.shahin8n.sbs"),
                )
            ],
            [KeyboardButton("/ready"), KeyboardButton("/status")],
            [KeyboardButton("/balance"), KeyboardButton("/help")],
        ]

        reply_markup = ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
        )

        message_text = (
            "🃏 Texas Poker Bot\n\n"
            "برای شروع بازی روی دکمه زیر کلیک کن:"
        )

        await self._send_localized_message(
            chat_id=update.effective_chat.id,
            text=message_text,
            reply_markup=reply_markup,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    async def _send_menu(
        self,
        chat_id: int,
        menu_context: MenuContext,
    ) -> None:
        """Send appropriate menu based on chat type and user state."""

        if menu_context.chat_type == "private":
            await self._send_private_menu(chat_id, menu_context)
        else:
            await self._send_group_menu(chat_id, menu_context)

    def _build_navigation_row(
        self,
        context: MenuContext,
        language_context: Any,
    ) -> List[InlineKeyboardButton]:
        """Build back/home navigation buttons based on menu state."""

        buttons: List[InlineKeyboardButton] = []

        location_enum: Optional[MenuLocation] = None
        parent_location: Optional[MenuLocation] = None
        if context.current_menu_location:
            try:
                location_enum = MenuLocation(context.current_menu_location)
                parent_location = MENU_HIERARCHY.get(location_enum)
            except ValueError:
                location_enum = None
                parent_location = None

        if parent_location is not None:
            back_label = self._t("nav.back", context=language_context)
            buttons.append(
                InlineKeyboardButton(
                    f"⬅️ {back_label}",
                    callback_data="nav_back",
                )
            )

        if location_enum and location_enum != MenuLocation.MAIN_MENU:
            home_label = self._t("nav.home", context=language_context)
            buttons.append(
                InlineKeyboardButton(
                    f"🏠 {home_label}",
                    callback_data="nav_home",
                )
            )

        return buttons

    @lru_cache(maxsize=128)
    def _get_location_label_cached(
        self,
        location_key: str,
        language_code: str,
    ) -> str:
        """Cache frequently accessed location labels."""

        translator = self._translation_manager.get_translator(language_code)
        return translator(f"menu.location.{location_key}")

    def _render_breadcrumb(
        self,
        context: MenuContext,
        language_context: Any,
    ) -> Optional[str]:
        """Render breadcrumb trail with caching optimization."""

        if not context.current_menu_location:
            return None

        try:
            current_location = MenuLocation(context.current_menu_location)
        except ValueError:
            return None

        path = get_breadcrumb_path(current_location)

        if not path or len(path) <= 1:
            return None

        labels: List[str] = []
        for location in path:
            try:
                label = self._get_location_label_cached(
                    location.value,
                    context.language_code,
                )
                labels.append(label)
            except KeyError:
                self._logger.warning(
                    "Missing translation for location: %s",
                    location.value,
                )
                labels.append(location.value.upper())

        separator = " → " if context.language_code != "fa" else " ← "
        breadcrumb = separator.join(labels)

        return f"📍 {breadcrumb}\n"

    def clear_location_cache(self):
        """Clear cached location labels (call when translations update)."""

        self._get_location_label_cached.cache_clear()

    def _build_private_menu_keyboard(
        self, context: MenuContext, language_context: LanguageContext
    ) -> List[List[InlineKeyboardButton]]:
        """Return the inline keyboard layout for the private menu."""

        keyboard: List[List[InlineKeyboardButton]] = []

        gameplay_row: List[InlineKeyboardButton] = []
        if context.active_private_game_code:
            gameplay_row.append(
                InlineKeyboardButton(
                    self._t("menu.private.view_game", context=language_context),
                    callback_data="private_view_game",
                )
            )

            if context.is_game_host:
                gameplay_row.append(
                    InlineKeyboardButton(
                        self._t(
                            "menu.private.manage_game",
                            context=language_context,
                        ),
                        callback_data="private_manage",
                    )
                )
        else:
            gameplay_row.append(
                InlineKeyboardButton(
                    self._t("menu.private.create_game", context=language_context),
                    callback_data="private_create",
                )
            )

        if gameplay_row:
            keyboard.append(gameplay_row)

        if context.has_pending_invite:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        self._t("menu.private.view_invites", context=language_context),
                        callback_data="view_invites",
                    )
                ]
            )

        support_row = [
            InlineKeyboardButton(
                self._t("menu.common.settings", context=language_context),
                callback_data="settings",
            ),
            InlineKeyboardButton(
                self._t("menu.common.help", context=language_context),
                callback_data="help",
            ),
        ]
        keyboard.append(support_row)

        nav_row = self._build_navigation_row(context, language_context)
        if nav_row:
            keyboard.append(nav_row)

        return keyboard

    async def _send_private_menu(
        self,
        chat_id: int,
        context: MenuContext,
    ) -> None:
        """Build and send menu for private (1-on-1) chats."""

        language_context = translation_manager.get_language_context(
            context.language_code
        )

        title = self._t("menu.private.main_title", context=language_context)

        section_lines: List[str] = []
        section_lines.append(
            self._t("menu.private.sections.gameplay", context=language_context)
        )

        if context.has_pending_invite:
            section_lines.append(
                self._t("menu.private.sections.invitations", context=language_context)
            )

        section_lines.append(
            self._t("menu.private.sections.support", context=language_context)
        )

        if section_lines:
            bullet_prefix = "• "
            sections_text = "\n".join(f"{bullet_prefix}{line}" for line in section_lines)
            title = f"{title}\n\n{sections_text}"

        breadcrumb = self._render_breadcrumb(context, language_context)
        if breadcrumb:
            title = f"{breadcrumb}\n\n{title}"

        keyboard = self._build_private_menu_keyboard(context, language_context)

        reply_markup = InlineKeyboardMarkup(keyboard)

        await self._send_localized_message(
            chat_id=chat_id,
            text=title,
            context=language_context,
            reply_markup=reply_markup,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    async def _send_group_menu(
        self,
        chat_id: int,
        context: MenuContext,
    ) -> None:
        """Build and send menu for group chats."""

        language_context = translation_manager.get_language_context(
            context.language_code
        )

        title = self._t("menu.group.main_title", context=language_context)

        keyboard: List[List[InlineKeyboardButton]] = []

        if context.group_has_active_game:
            if context.in_active_game:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            self._t(
                                "menu.group.view_game",
                                context=language_context,
                            ),
                            callback_data="group_view_game",
                        )
                    ]
                )
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            self._t(
                                "menu.group.leave_game",
                                context=language_context,
                            ),
                            callback_data="group_leave",
                        )
                    ]
                )
            else:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            self._t(
                                "menu.group.join_game",
                                context=language_context,
                            ),
                            callback_data="group_join",
                        )
                    ]
                )
        else:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        self._t(
                            "menu.group.start_game",
                            context=language_context,
                        ),
                        callback_data="group_start",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    self._t(
                        "menu.group.language",
                        context=language_context,
                    ),
                    callback_data="lang:open:group_menu",
                )
            ]
        )

        if context.user_is_group_admin:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        self._t(
                            "menu.group.admin_panel",
                            context=language_context,
                        ),
                        callback_data="group_admin",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    self._t("menu.common.help", context=language_context),
                    callback_data="help",
                )
            ]
        )

        breadcrumb = self._render_breadcrumb(context, language_context)
        if breadcrumb:
            title = f"{breadcrumb}\n\n{title}"

        nav_row = self._build_navigation_row(context, language_context)
        if nav_row:
            keyboard.append(nav_row)

        reply_markup = InlineKeyboardMarkup(keyboard)

        await self._send_localized_message(
            chat_id=chat_id,
            text=title,
            context=language_context,
            reply_markup=reply_markup,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    _STALE_CALLBACK_MESSAGE = (
        "Query is too old and response timeout expired or query id is invalid"
    )

    async def send_language_menu(
        self,
        *,
        chat_id: ChatId,
        language_code: str,
        message_id: Optional[MessageId] = None,
        reply_to_message_id: Optional[MessageId] = None,
        origin: Optional[str] = None,
    ) -> None:
        """Render a language picker with the active locale highlighted."""

        language_context = translation_manager.get_language_context(language_code)
        languages = translation_manager.get_supported_languages()

        rows: List[List[InlineKeyboardButton]] = []
        for index in range(0, len(languages), 2):
            row: List[InlineKeyboardButton] = []
            for lang_info in languages[index:index + 2]:
                code = lang_info["code"]
                name = lang_info.get("name", code.upper())
                flag = lang_info.get("flag", "🏳️")
                label = f"{flag} {name}"
                if code == language_code:
                    label = f"✅ {label}"
                data_parts = ["lang", "set", code]
                if origin:
                    data_parts.append(origin)
                row.append(
                    InlineKeyboardButton(
                        text=label,
                        callback_data=":".join(data_parts),
                    )
                )
            rows.append(row)

        header_key = "settings.choose_language"
        if origin in {"group_settings", "group_menu"}:
            header_key = "settings.choose_group_language"
        elif origin == "private_settings":
            header_key = "settings.choose_private_language"

        header = self._t(
            header_key,
            context=language_context,
        )

        if origin:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self._t(
                            "language.back",
                            context=language_context,
                        ),
                        callback_data=f"lang:back:{origin}",
                    )
                ]
            )

        markup = InlineKeyboardMarkup(rows)

        if message_id is not None:
            localized_header = self._localize_text(
                header,
                context=language_context,
            )

            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=localized_header,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
            except BadRequest as error:
                error_text = (getattr(error, "message", None) or str(error)).lower()
                if "message is not modified" in error_text:
                    logger.debug(
                        "LanguageMenuUnchanged",
                        extra={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "language_code": language_code,
                        },
                    )
                    return
                raise
            return

        await self._send_localized_message(
            chat_id=chat_id,
            text=header,
            context=language_context,
            reply_markup=markup,
            reply_to_message_id=reply_to_message_id,
            disable_notification=True,
            disable_web_page_preview=True,
        )

    async def send_settings_menu(
        self,
        *,
        chat_id: ChatId,
        context: MenuContext,
    ) -> None:
        """Render settings menu allowing language preferences."""

        language_context = translation_manager.get_language_context(
            context.language_code
        )

        title = self._t("menu.settings.title", context=language_context)

        bullet_points: List[str] = [
            self._t(
                "menu.settings.descriptions.private",
                context=language_context,
            )
        ]

        if context.is_group_chat():
            bullet_points.append(
                self._t(
                    "menu.settings.descriptions.group",
                    context=language_context,
                )
            )

        if bullet_points:
            title = "\n".join(
                [
                    title,
                    "",
                    *(
                        f"• {line}" for line in bullet_points
                    ),
                ]
            )

        keyboard: List[List[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(
                    self._t(
                        "menu.settings.buttons.private_language",
                        context=language_context,
                    ),
                    callback_data="lang:open:private_settings",
                )
            ]
        ]

        if context.is_group_chat():
            keyboard.append(
                [
                    InlineKeyboardButton(
                        self._t(
                            "menu.settings.buttons.group_language",
                            context=language_context,
                        ),
                        callback_data="lang:open:group_settings",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    self._t("menu.common.help", context=language_context),
                    callback_data="help",
                )
            ]
        )

        nav_row = self._build_navigation_row(context, language_context)
        if nav_row:
            keyboard.append(nav_row)

        markup = InlineKeyboardMarkup(keyboard)

        await self._send_localized_message(
            chat_id=chat_id,
            text=title,
            context=language_context,
            reply_markup=markup,
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

        if not query_id:
            logger.debug("AnswerCallbackSkipped", extra={"reason": "missing_query_id"})
            return

        try:
            await self._bot.answer_callback_query(
                callback_query_id=query_id,
                text=text,
                show_alert=show_alert,
            )
        except BadRequest as error:
            error_text_raw = getattr(error, "message", None) or str(error)
            error_text = error_text_raw.lower()

            if "query_id_invalid" in error_text or "query id is invalid" in error_text:
                logger.debug(
                    "AnswerCallbackIgnored",
                    extra={
                        "query_id": query_id,
                        "reason": "query_id_invalid",
                    },
                )
                return

            if self._STALE_CALLBACK_MESSAGE.lower() in error_text:
                logger.debug(
                    "AnswerCallbackStale",
                    extra={
                        "query_id": query_id,
                    },
                )
                return

            logger.warning(
                "AnswerCallbackFailed",
                extra={
                    "query_id": query_id,
                    "error": error_text_raw,
                    "show_alert": show_alert,
                },
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
        plain_text = UnicodeTextFormatter.strip_all_html(text)
        await self._send_localized_message(
            reply_to_message_id=message_id,
            chat_id=chat_id,
            text=plain_text,
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
        *,
        user_id: Optional[int] = None,
    ) -> None:
        markup = PokerBotViewer._get_cards_markup(cards)
        language_context = self._get_language_context_for_user(user_id=user_id)
        panel_text = self.build_hand_panel(
            hand_cards=list(cards),
            board_cards=[],
            context=language_context,
        )
        message_text = (
            f"{mention_markdown}\n\n{panel_text}"
            if mention_markdown else panel_text
        )

        plain_text = UnicodeTextFormatter.strip_all_html(message_text)
        localized_text = self._localize_text(
            plain_text,
            context=language_context,
        )

        send_kwargs = dict(
            chat_id=chat_id,
            text=localized_text,
            reply_markup=markup,
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
        user_id: Optional[int] = None,
    ) -> Optional[int]:
        """Send or edit a player's private hand panel in direct chats."""

        language_context = self._get_language_context_for_user(user_id=user_id)
        panel_text = self.build_hand_panel(
            hand_cards=list(cards),
            board_cards=list(table_cards or []),
            include_table=True,
            context=language_context,
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
                plain_text = UnicodeTextFormatter.strip_all_html(message_text)
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._localize_text(
                        plain_text,
                        context=language_context,
                    ),
                )
                return message_id

            plain_text = UnicodeTextFormatter.strip_all_html(message_text)
            message = await self._send_localized_message(
                chat_id=chat_id,
                text=plain_text,
                reply_markup=reply_markup,
                disable_notification=disable_notification,
                context=language_context,
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
        *,
        language_code: Optional[str] = None,
        message_id: Optional[int] = None,
    ) -> None:
        """Send stake selection menu for private game creation."""

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        language_context = self._get_language_context_for_user(language=language_code)
        option_keys = ["micro", "low", "medium", "high", "premium"]

        keyboard: List[List[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(
                    text=self._t(
                        f"private.stake_menu.button.{option}",
                        context=language_context,
                    ),
                    callback_data=f"stake:{option}",
                )
            ]
            for option in option_keys
        ]

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=self._t(
                        "private.stake_menu.button.language",
                        context=language_context,
                    ),
                    callback_data="stake:language",
                )
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=self._t(
                        "private.stake_menu.button.cancel",
                        context=language_context,
                    ),
                    callback_data="stake:cancel",
                )
            ]
        )

        options_block = "\n".join(
            self._t(
                f"msg.private.stake_menu.option_{option}",
                context=language_context,
            )
            for option in option_keys
        )

        text = self._t(
            "msg.private.stake_menu.body",
            context=language_context,
            title=self._t("msg.private.stake_menu.title", context=language_context),
            subtitle=self._t(
                "msg.private.stake_menu.subtitle", context=language_context
            ),
            options=options_block,
            footer=self._t("msg.private.stake_menu.footer", context=language_context),
        )
        plain_text = UnicodeTextFormatter.strip_all_html(text)
        localized_text = self._localize_text(plain_text, context=language_context)
        reply_markup = InlineKeyboardMarkup(keyboard)

        if message_id is not None:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=localized_text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return

        await self._send_localized_message(
            chat_id=chat_id,
            text=plain_text,
            context=language_context,
            reply_markup=reply_markup,
            disable_notification=True,
            disable_web_page_preview=True,
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

        language_context = self._language_context

        keyboard = [
            [
                InlineKeyboardButton(
                    self._t(
                        "private.invite.accept",
                        context=language_context,
                    ),
                    callback_data=f"invite_accept:{game_code}",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._t(
                        "private.invite.decline",
                        context=language_context,
                    ),
                    callback_data=f"invite_decline:{game_code}",
                ),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = self._t(
            "msg.private.invite.body",
            context=language_context,
            inviter=inviter_name,
            stake=stake_name,
            code=game_code,
        )

        await self._send_localized_message(
            chat_id=chat_id,
            text=message_text,
            context=language_context,
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

        language_context = self._language_context
        player_list = "\n".join([f" • {name}" for name in player_names])

        keyboard = []
        if can_start:
            keyboard.append([
                InlineKeyboardButton(
                    self._t(
                        "private.lobby.start",
                        context=language_context,
                    ),
                    callback_data=f"private_start:{game_code}",
                ),
            ])

        keyboard.append([
            InlineKeyboardButton(
                self._t(
                    "private.lobby.invite",
                    context=language_context,
                ),
                callback_data=f"private_invite:{game_code}",
            ),
        ])
        keyboard.append([
            InlineKeyboardButton(
                self._t(
                    "private.lobby.leave",
                    context=language_context,
                ),
                callback_data=f"private_leave:{game_code}",
            ),
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        min_indicator = ""
        if current_players < min_players:
            min_indicator = self._t(
                "msg.private.lobby.min_indicator",
                context=language_context,
                min_players=min_players,
            )

        readiness_key = (
            "msg.private.lobby.ready" if can_start else "msg.private.lobby.waiting"
        )
        readiness = self._t(
            readiness_key,
            context=language_context,
            min_players=min_players,
        )
        player_status = self._t(
            "msg.private.lobby.player_status",
            context=language_context,
            status_icon="✅" if can_start else "⏳",
            current=current_players,
            max=max_players,
            min_indicator=min_indicator,
        ).strip()

        message = self._t(
            "msg.private.lobby.body",
            context=language_context,
            title=self._t("msg.private.lobby.title", context=language_context),
            host=host_name,
            stake=stake_name,
            code=game_code,
            player_status=player_status,
            players=player_list,
            readiness=readiness,
        )

        await self._send_localized_message(
            chat_id=chat_id,
            text=message,
            context=language_context,
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
            "msg.error.insufficient_funds_detail",
            balance=balance_display,
            required=required_display,
        )

        await self._send_localized_message(
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
        player_text = self._t(
            "lobby.players",
            count=player_count,
            max=max_players,
        )

        player_entries: List[str] = []
        for index, player_name in enumerate(players):
            if index == 0 and is_host:
                player_entries.append(
                    self._t("lobby.host_entry", player=player_name)
                )
            else:
                player_entries.append(
                    self._t("lobby.player_entry", player=player_name)
                )

        player_list = "\n".join(player_entries)
        status_key = "lobby.ready_to_start" if player_count >= 2 else "lobby.waiting"
        status_text = self._t(status_key)

        segments = [title, "", player_text]
        if player_list:
            segments.extend(["", player_list])
        segments.extend(["", status_text])

        return await self._send_localized_message(
            chat_id=chat_id,
            text="\n".join(segments),
        )

    async def send_game_started_message(
        self,
        chat_id: int,
    ) -> None:
        """Send localized game started notification."""

        text = self._t("msg.game_started")

        await self._send_localized_message(
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

        small_blind = self._format_currency(
            stake_config["small_blind"],
            include_symbol=False,
        )
        big_blind = self._format_currency(
            stake_config["big_blind"],
            include_symbol=False,
        )
        min_buyin = self._format_currency(
            stake_config["min_buyin"],
            include_symbol=False,
        )

        message = self._t(
            "msg.private.invite.detailed_body",
            host=host_name,
            code=game_code,
            stake_name=stake_config["name"],
            small_blind=small_blind,
            big_blind=big_blind,
            min_buyin=min_buyin,
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    self._t("private.invite.accept"),
                    callback_data=f"invite_accept:{game_code}",
                ),
                InlineKeyboardButton(
                    self._t("private.invite.decline"),
                    callback_data=f"invite_decline:{game_code}",
                ),
            ]
        ])

        return message, keyboard
