#!/usr/bin/env python3
"""Live message helper utilities for the in-chat game view."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from pokerapp.entities import Game, Player, PlayerState


class LiveMessageManager:
    """Manage the single live game message shown in group chats."""

    # Stage emojis for card reveals
    STAGE_EMOJIS = {
        0: "🔒",  # Pre-flop
        3: "🌄",  # Flop
        4: "🌇",  # Turn
        5: "🌃",  # River
    }

    # Stage names
    STAGE_NAMES = {
        0: "Pre-flop",
        3: "Flop",
        4: "Turn",
        5: "River",
    }

    PARSE_MODE = "HTML"
    # Minimum spacing between consecutive updates per chat (seconds)
    DEBOUNCE_WINDOW = 0.35

    def __init__(self, bot, logger):
        self._bot = bot
        self._logger = logger
        self._chat_locks: Dict[str, asyncio.Lock] = {}
        self._last_update_at: Dict[str, float] = {}

    async def send_or_update_live_message(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Public wrapper maintaining backwards compatibility."""
        game_identifier = getattr(game, "game_id", getattr(game, "id", "?"))
        self._logger.info(
            "🔍 LiveMessageManager.send_or_update_live_message called - "
            "chat_id=%s, game_id=%s",
            chat_id,
            game_identifier,
        )
        return await self.send_or_update_game_state(
            chat_id=chat_id,
            game=game,
            current_player=current_player,
        )

    async def send_or_update_game_state(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Send a new live message or update the existing one."""

        chat_key = str(chat_id)
        lock = self._chat_locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_key] = lock

        async with lock:
            await self._apply_debounce(chat_key)
            loop = asyncio.get_running_loop()
            try:
                return await self._send_or_update_locked(
                    chat_id=chat_id,
                    game=game,
                    current_player=current_player,
                )
            finally:
                self._last_update_at[chat_key] = loop.time()

    async def _apply_debounce(self, chat_key: str) -> None:
        """Sleep briefly if the last update happened too recently."""

        window = self.DEBOUNCE_WINDOW
        if window <= 0:
            return

        last_update = self._last_update_at.get(chat_key)
        if last_update is None:
            return

        loop = asyncio.get_running_loop()
        elapsed = loop.time() - last_update
        if elapsed < window:
            await asyncio.sleep(window - elapsed)

    async def _send_or_update_locked(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Internal helper executing the actual message update."""

        next_version = None
        if current_player is not None:
            next_version = game.next_live_message_version()

        message_text = self._build_game_state_text(game, current_player)
        reply_markup = None
        if current_player is not None:
            reply_markup = self._build_action_inline_keyboard(
                game,
                current_player,
                next_version,
            )

        self._logger.info(
            "🔍 Sending update - has_buttons=%s, button_count=%s",
            reply_markup is not None,
            len(reply_markup.inline_keyboard)
            if getattr(reply_markup, "inline_keyboard", None)
            else 0,
        )

        message_id = None

        # Try to edit existing message first
        if game.has_group_message():
            try:
                self._logger.debug(
                    "Attempting to edit message %s in chat %s",
                    game.group_message_id,
                    chat_id,
                )
                message = await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game.group_message_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode=self.PARSE_MODE,
                    disable_web_page_preview=True,
                )
                message_id = getattr(
                    message,
                    "message_id",
                    game.group_message_id,
                )
                if next_version is not None:
                    game.mark_live_message_version(next_version)
                self._logger.debug(
                    "✅ Successfully edited message %s", message_id
                )
                return message_id

            except TelegramError as exc:
                error_msg = str(exc).lower()

                if (
                    "not modified" in error_msg
                    or "message is not modified" in error_msg
                ):
                    self._logger.debug(
                        "Message %s content unchanged, skipping update",
                        game.group_message_id,
                    )
                    return game.group_message_id

                if (
                    "message to edit not found" in error_msg
                    or "message can't be edited" in error_msg
                    or "message_id_invalid" in error_msg
                ):
                    self._logger.warning(
                        "Message %s no longer exists, will send new message",
                        game.group_message_id,
                    )
                    game.group_message_id = None
                else:
                    self._logger.error(
                        "Failed to edit message %s: %s, will send new message",
                        game.group_message_id,
                        exc,
                    )
                    game.group_message_id = None

        # Send new message if no existing one or edit failed critically
        try:
            self._logger.debug("Sending new live message to chat %s", chat_id)
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode=self.PARSE_MODE,
                disable_notification=True,
                disable_web_page_preview=True,
            )
            message_id = getattr(message, "message_id", None)

            if message_id is not None:
                self._logger.info(
                    "✅ Created new live message %s in chat %s",
                    message_id,
                    chat_id,
                )
                game.set_group_message(message_id)
                if next_version is not None:
                    game.mark_live_message_version(next_version)

            return message_id

        except TelegramError as exc:
            self._logger.error(
                "❌ Unable to send new live message to chat %s: %s",
                chat_id,
                exc,
            )
            return None

    def _build_game_state_text(
        self, game: Game, _current_player: Player
    ) -> str:
        """Construct the full text body shown in the live game message."""

        return self._format_game_state(game)

    def _get_player_name(self, player: Player) -> str:
        """Extract display name from player for UI display."""
        mention = getattr(player, "mention_markdown", None)

        if mention and mention.startswith("[") and "](" in mention:
            try:
                name = mention.split("]")[0][1:]
                if name:
                    return name
            except (IndexError, AttributeError):
                pass

        return f"User {player.user_id}"

    def _format_game_state(self, game: Game) -> str:
        """Format the live message using the Phase 8+ layout."""

        lines: List[str] = []

        # === HEADER ===
        lines.append("🎴 <b>TEXAS HOLD'EM</b> 🎴")
        lines.append("")

        # === COMMUNITY CARDS ===
        lines.append("🃏 <b>COMMUNITY CARDS</b>")

        num_cards = len(game.cards_table) if game.cards_table else 0
        stage_emoji = self.STAGE_EMOJIS.get(num_cards, "🔒")
        stage_name = self.STAGE_NAMES.get(num_cards, "Pre-flop")

        if num_cards == 0:
            lines.append(
                f"{stage_emoji} Cards will be revealed during betting rounds"
            )
        else:
            from pokerapp.pokerbotview import PokerBotViewer

            cards_display = PokerBotViewer._format_cards_line(game.cards_table)
            lines.append(f"{stage_emoji} {stage_name}: {cards_display}")

        lines.append("")

        # === POT & BETS ===
        lines.append("💰 <b>POT & BETS</b>")
        lines.append(f"💵 Total Pot: ${game.pot}")

        if game.max_round_rate > 0:
            lines.append(f"🎯 Current Bet: ${game.max_round_rate}")

        lines.append("")

        # === PLAYERS ===
        active_count = sum(
            1 for p in game.players if p.state != PlayerState.FOLD
        )
        lines.append(f"👥 <b>PLAYERS ({active_count} active)</b>")

        for player in game.players:
            name = self._get_player_name(player)
            balance = player.wallet.value()

            if player.state == PlayerState.FOLD:
                icon = "❌"
                status = " (folded)"
            elif player.state == PlayerState.ALL_IN:
                icon = "🔥"
                status = f" (ALL-IN: ${player.round_rate})"
            elif player.round_rate > 0:
                icon = "✅"
                status = f" (bet: ${player.round_rate})"
            else:
                icon = "✅"
                status = ""

            lines.append(f"{icon} {name} - ${balance}{status}")

        lines.append("")

        # === RECENT ACTIVITY ===
        if game.recent_actions:
            lines.append("📋 <b>RECENT ACTIVITY</b>")
            for action in game.recent_actions[-3:]:
                lines.append(f"{action}")
            lines.append("")

        return "\n".join(lines)

    def _build_action_inline_keyboard(
        self,
        game: Game,
        player: Player,
        version: Optional[int],
    ) -> Optional[InlineKeyboardMarkup]:
        """Build the inline keyboard for the player's available actions."""

        if player is None:
            return None

        buttons: List[List[InlineKeyboardButton]] = []

        current_bet = game.max_round_rate
        player_bet = player.round_rate
        player_balance = player.wallet.value()
        call_amount = max(current_bet - player_bet, 0)
        game_id = str(game.id)
        version_segment = [str(version)] if version is not None else []

        first_row: List[InlineKeyboardButton] = []
        show_primary_all_in = False

        if call_amount <= 0:
            first_row.append(
                InlineKeyboardButton(
                    "✅ Check",
                    callback_data=":".join(
                        ["action", "check", *version_segment, game_id]
                    ),
                )
            )
        elif call_amount < player_balance:
            first_row.append(
                InlineKeyboardButton(
                    f"💵 Call ${call_amount}",
                    callback_data=":".join(
                        ["action", "call", *version_segment, game_id]
                    ),
                )
            )
        else:
            show_primary_all_in = player_balance > 0
            first_row.append(
                InlineKeyboardButton(
                    f"🔥 All-In (${player_balance})",
                    callback_data=":".join(
                        ["action", "all_in", *version_segment, game_id]
                    ),
                )
            )

        first_row.append(
            InlineKeyboardButton(
                "🚪 Fold",
                callback_data=":".join(
                    ["action", "fold", *version_segment, game_id]
                ),
            )
        )
        buttons.append(first_row)

        big_blind = (game.table_stake or 0) * 2
        min_raise = max(current_bet * 2, big_blind)
        can_raise = (
            player_balance > call_amount and player_balance >= min_raise
        )

        raise_amounts: List[int] = []
        if can_raise:
            pot_raise = game.pot
            close_threshold = min_raise * 0.1

            primary_raise = min_raise
            merged_pot_raise = False

            if pot_raise > 0 and player_balance >= pot_raise:
                if abs(pot_raise - min_raise) <= close_threshold:
                    merged_pot_raise = True
                    if pot_raise > min_raise:
                        primary_raise = pot_raise
            raise_amounts.append(primary_raise)

            if (
                pot_raise > min_raise
                and player_balance >= pot_raise
                and not merged_pot_raise
            ):
                raise_amounts.append(pot_raise)

        if player_balance > 0 and (can_raise or not show_primary_all_in):
            second_row: List[InlineKeyboardButton] = []

            if can_raise and raise_amounts:
                primary_raise_amount = raise_amounts[0]
                second_row.append(
                    InlineKeyboardButton(
                        f"📈 Raise ${primary_raise_amount}",
                        callback_data=":".join(
                            [
                                "action",
                                "raise",
                                str(primary_raise_amount),
                                *version_segment,
                                game_id,
                            ]
                        ),
                    )
                )

            if not show_primary_all_in:
                second_row.append(
                    InlineKeyboardButton(
                        f"💥 All-In (${player_balance})",
                        callback_data=":".join(
                            ["action", "all_in", *version_segment, game_id]
                        ),
                    )
                )

            if second_row:
                buttons.append(second_row)

        extra_amounts = sorted(set(raise_amounts[1:]))
        if extra_amounts:
            for i in range(0, len(extra_amounts), 2):
                row: List[InlineKeyboardButton] = []
                for amount in extra_amounts[i:i + 2]:
                    row.append(
                        InlineKeyboardButton(
                            f"${amount}",
                            callback_data=":".join(
                                [
                                    "action",
                                    "raise",
                                    str(amount),
                                    *version_segment,
                                    game_id,
                                ]
                            ),
                        )
                    )
                buttons.append(row)

        if not buttons:
            return None

        return InlineKeyboardMarkup(buttons)
