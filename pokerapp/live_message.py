#!/usr/bin/env python3
"""Live message helper utilities for the in-chat game view."""

from __future__ import annotations

import html
from typing import List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from pokerapp.entities import Game, GameState, Player, PlayerState


class LiveMessageManager:
    """Manage the single live game message shown in group chats."""

    def __init__(self, bot, logger):
        self._bot = bot
        self._logger = logger

    async def send_or_update_game_state(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Send a new live message or update the existing one."""

        message_text = self._build_game_state_text(game, current_player)
        reply_markup = self._build_action_inline_keyboard(game, current_player)

        try:
            if game.has_group_message():
                try:
                    message = await self._bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=game.group_message_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                        disable_web_page_preview=True,
                    )
                    message_id = getattr(message, "message_id", game.group_message_id)
                except TelegramError as exc:  # pragma: no cover - network interaction
                    self._logger.warning(
                        "Failed to edit live game message %s: %s",
                        game.group_message_id,
                        exc,
                    )
                    message = await self._bot.send_message(
                        chat_id=chat_id,
                        text=message_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                        disable_notification=True,
                        disable_web_page_preview=True,
                    )
                    message_id = getattr(message, "message_id", None)
            else:
                message = await self._bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
                message_id = getattr(message, "message_id", None)
        except TelegramError as exc:  # pragma: no cover - network interaction
            self._logger.error("Unable to send or update live message: %s", exc)
            return None

        if message_id is not None:
            game.set_group_message(message_id)

        return message_id

    def _build_game_state_text(self, game: Game, current_player: Player) -> str:
        """Construct the full text body shown in the live game message."""

        round_labels = {
            GameState.INITIAL: "Waiting",
            GameState.ROUND_PRE_FLOP: "Pre-Flop",
            GameState.ROUND_FLOP: "Flop",
            GameState.ROUND_TURN: "Turn",
            GameState.ROUND_RIVER: "River",
            GameState.FINISHED: "Showdown",
        }

        lines: List[str] = ["<b>🃏 Texas Hold'em Poker</b>"]

        lines.append(f"💰 Pot: <b>${game.pot}</b>")
        round_label = round_labels.get(game.state, "Unknown")
        lines.append(f"🎲 Round: {round_label}")

        to_call = max(game.max_round_rate - current_player.round_rate, 0)
        lines.append(f"💵 To Call: ${to_call}")

        if game.cards_table:
            community_cards = " ".join(html.escape(str(card)) for card in game.cards_table)
            lines.append(f"🃏 Community: {community_cards}")
        else:
            lines.append("🃏 Community: Waiting for cards…")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<b>Players</b>")

        for player in game.players:
            icon = self._status_icon(player, current_player)
            name = self._extract_display_name(player.mention_markdown)
            balance = player.wallet.value()

            status_bits: List[str] = []
            if player.state == PlayerState.ALL_IN:
                status_bits.append("All-In")
            elif player.state == PlayerState.FOLD:
                status_bits.append("Folded")

            if player.round_rate > 0:
                status_bits.append(f"In for ${player.round_rate}")

            status_suffix = f" ({', '.join(status_bits)})" if status_bits else ""
            lines.append(f"{icon} <b>{name}</b> — ${balance}{status_suffix}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📢 <b>Recent Actions</b>")

        recent_lines = game.get_recent_actions_text().splitlines()
        if not recent_lines:
            recent_lines = ["No actions yet."]

        lines.extend(html.escape(line) for line in recent_lines)

        return "\n".join(lines)

    def _build_action_inline_keyboard(
        self,
        game: Game,
        player: Player,
    ) -> InlineKeyboardMarkup:
        """Build the inline keyboard for the player's available actions."""

        buttons: List[List[InlineKeyboardButton]] = []

        current_bet = game.max_round_rate
        player_bet = player.round_rate
        player_balance = player.wallet.value()
        call_amount = max(current_bet - player_bet, 0)
        game_id = str(game.id)

        first_row: List[InlineKeyboardButton] = []
        if call_amount == 0:
            first_row.append(
                InlineKeyboardButton(
                    "✅ Check",
                    callback_data=self._format_action_callback("check", game_id),
                )
            )
        elif call_amount < player_balance:
            first_row.append(
                InlineKeyboardButton(
                    f"💵 Call ${call_amount}",
                    callback_data=self._format_action_callback("call", game_id),
                )
            )

        first_row.append(
            InlineKeyboardButton(
                "🚪 Fold",
                callback_data=self._format_action_callback("fold", game_id),
            )
        )
        buttons.append(first_row)

        big_blind = game.table_stake * 2 if game.table_stake else 0
        min_raise = max(current_bet * 2, big_blind)
        can_raise = player_balance > call_amount and player_balance >= min_raise

        second_row: List[InlineKeyboardButton] = []

        if can_raise:
            second_row.append(
                InlineKeyboardButton(
                    f"📈 Raise (min ${min_raise})",
                    callback_data=self._format_action_callback(
                        "raise", game_id, str(min_raise)
                    ),
                )
            )

        if player_balance > 0:
            second_row.append(
                InlineKeyboardButton(
                    f"💥 All-In (${player_balance})",
                    callback_data=self._format_action_callback("all_in", game_id),
                )
            )

        if second_row:
            buttons.append(second_row)

        return InlineKeyboardMarkup(buttons)

    @staticmethod
    def _format_action_callback(action: str, game_id: str, *extra: str) -> str:
        """Return callback payload in the expected controller format."""

        parts = ["action", action, *extra, game_id]
        return ":".join(str(part) for part in parts)

    @staticmethod
    def _extract_display_name(mention_markdown: str) -> str:
        """Convert markdown mention into a safe plain-text name."""

        if not mention_markdown:
            return "Unknown"

        mention = mention_markdown.replace("`", "").strip()
        if mention.startswith("[") and "]" in mention:
            mention = mention[1 : mention.index("]")]

        return html.escape(mention)

    @staticmethod
    def _status_icon(player: Player, current_player: Player) -> str:
        """Return the correct status indicator for a player."""

        if player.state == PlayerState.FOLD:
            return "⚫"
        if player.state == PlayerState.ALL_IN:
            return "🔴"
        if player.user_id == current_player.user_id:
            return "🟢"
        return "🔵"
