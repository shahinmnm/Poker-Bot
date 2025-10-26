#!/usr/bin/env python3
"""Live message helper utilities for the in-chat game view."""

from __future__ import annotations

from typing import List, Optional

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
                        reply_markup=reply_markup,
                        disable_web_page_preview=True,
                    )
                    message_id = getattr(
                        message,
                        "message_id",
                        game.group_message_id,
                    )
                except TelegramError as exc:  # pragma: no cover
                    self._logger.warning(
                        "Failed to edit live game message %s: %s",
                        game.group_message_id,
                        exc,
                    )
                    message = await self._bot.send_message(
                        chat_id=chat_id,
                        text=message_text,
                        reply_markup=reply_markup,
                        disable_notification=True,
                        disable_web_page_preview=True,
                    )
                    message_id = getattr(message, "message_id", None)
            else:
                message = await self._bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    disable_notification=True,
                    disable_web_page_preview=True,
                )
                message_id = getattr(message, "message_id", None)
        except TelegramError as exc:  # pragma: no cover
            self._logger.error(
                "Unable to send or update live message: %s", exc
            )
            return None

        if message_id is not None:
            game.set_group_message(message_id)

        return message_id

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

    def _build_header(self) -> str:
        """Build simple header with title."""
        return "🎴 TEXAS HOLD'EM 🎴"

    def _build_cards_section(self, game: Game) -> str:
        """Build community cards section with stage indicator."""
        num_cards = len(game.cards_table)
        emoji = self.STAGE_EMOJIS.get(num_cards, "🎴")
        stage_name = self.STAGE_NAMES.get(num_cards, "Unknown")

        if num_cards == 0:
            cards_text = "🔒 Cards will be revealed during betting rounds"
        else:
            cards = " ".join(game.cards_table)
            cards_text = f"{emoji} {stage_name}: {cards}"

        return (
            f"\n\n🃏 COMMUNITY CARDS\n"
            f"{cards_text}"
        )

    def _build_pot_section(self, game: Game) -> str:
        """Build pot and betting information section."""
        pot_indicator = "💰"
        if game.pot >= 1000:
            pot_indicator = "💰💰💰"
        elif game.pot >= 500:
            pot_indicator = "💰💰"

        return (
            f"\n\n{pot_indicator} POT & BETS\n"
            f"💵 Total Pot: ${game.pot}\n"
            f"🎯 Current Bet: ${game.max_round_rate}"
        )

    def _build_players_section(self, game: Game) -> str:
        """Build player list with status indicators."""
        active_count = sum(
            1 for p in game.players
            if p.state == PlayerState.ACTIVE
        )

        lines = [f"\n\n👥 PLAYERS ({active_count} active)"]

        current_player_id = game.players[game.current_player_index].user_id

        for player in game.players:
            player_name = self._get_player_name(player)
            chips = player.wallet.value()
            bet = player.round_rate

            if player.state == PlayerState.FOLD:
                line = f"❌ {player_name} - Folded"
            elif player.state == PlayerState.ALL_IN:
                line = f"🚀 {player_name} - ${chips} ALL-IN!"
            else:
                is_current = (player.user_id == current_player_id)
                icon = "▶️" if is_current else "✅"
                line = f"{icon} {player_name} - ${chips}"
                if bet > 0:
                    line += f" (bet: ${bet})"

            lines.append(line)

        return "\n".join(lines)

    def _build_activity_section(self, game: Game) -> str:
        """Build recent activity feed from game.recent_actions."""
        if game.recent_actions:
            recent = list(reversed(game.recent_actions[-5:]))
            activity_lines = [
                f"{i}. {action}"
                for i, action in enumerate(recent, 1)
            ]
            activity_text = "\n".join(activity_lines)
        else:
            activity_text = "No recent activity yet"

        return (
            f"\n\n📋 RECENT ACTIVITY\n"
            f"{activity_text}"
        )

    def _build_turn_indicator(self, game: Game) -> str:
        """Build current turn indicator."""
        current_player = game.players[game.current_player_index]
        player_name = self._get_player_name(current_player)
        chips = current_player.wallet.value()

        return (
            f"\n\n⏱️ CURRENT TURN\n"
            f"{player_name}'s Turn - ${chips} available\n"
            f"\n⬇️ Choose your action below ⬇️"
        )

    def _format_game_state(self, game: Game) -> str:
        """
        Format game state as plain text for live message display.
        Uses emojis and smart spacing for readability.
        Persian font compatible (no HTML/Markdown).

        Args:
            game: Current game state

        Returns:
            Plain text formatted string for Telegram message
        """
        sections = [
            self._build_header(),
            self._build_cards_section(game),
            self._build_pot_section(game),
            self._build_players_section(game),
            self._build_activity_section(game),
            self._build_turn_indicator(game),
        ]

        return "".join(sections)

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
        player_id = str(player.user_id)

        first_row: List[InlineKeyboardButton] = []
        if call_amount == 0:
            first_row.append(
                InlineKeyboardButton(
                    "✅ Check",
                    callback_data=f"action_check_{player_id}",
                )
            )
        elif call_amount < player_balance:
            first_row.append(
                InlineKeyboardButton(
                    f"💵 Call ${call_amount}",
                    callback_data=f"action_call_{player_id}",
                )
            )
        else:
            first_row.append(
                InlineKeyboardButton(
                    f"🔥 All-In (${player_balance})",
                    callback_data=f"action_allin_{player_id}",
                )
            )

        first_row.append(
            InlineKeyboardButton(
                "🚪 Fold",
                callback_data=f"action_fold_{player_id}",
            )
        )
        buttons.append(first_row)

        big_blind = game.table_stake * 2 if game.table_stake else 0
        min_raise = max(current_bet * 2, big_blind)
        can_raise = player_balance > max(call_amount, min_raise)

        if can_raise:
            all_in_callback = f"action_allin_{player_id}"
            raise_callback = f"action_raise_{player_id}"
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"📈 Raise (min ${min_raise})",
                        **{"callback_data": raise_callback},
                    ),
                    InlineKeyboardButton(
                        f"💥 All-In (${player_balance})",
                        **{"callback_data": all_in_callback},
                    ),
                ]
            )

        return InlineKeyboardMarkup(buttons)
