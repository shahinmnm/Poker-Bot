"""Utilities for compact text formatting in live poker updates."""

from __future__ import annotations

from typing import List, Optional

from pokerapp.cards import Card
from pokerapp.entities import Player, PlayerState


class CompactFormatter:
    """Helpers focused on compressing live update text payloads.

    The formatter favors short, emoji-heavy strings so mobile users on
    constrained connections can still follow the game state without
    downloading large payloads."""

    _SUIT_MAP = {
        "S": "♠",
        "H": "♥",
        "D": "♦",
        "C": "♣",
        "♠": "♠",
        "♥": "♥",
        "♦": "♦",
        "♣": "♣",
    }

    _RANK_MAP = {
        "ACE": "A",
        "A": "A",
        "KING": "K",
        "K": "K",
        "QUEEN": "Q",
        "Q": "Q",
        "JACK": "J",
        "J": "J",
        "TEN": "T",
        "T": "T",
        "10": "T",
        "NINE": "9",
        "9": "9",
        "EIGHT": "8",
        "8": "8",
        "SEVEN": "7",
        "7": "7",
        "SIX": "6",
        "6": "6",
        "FIVE": "5",
        "5": "5",
        "FOUR": "4",
        "4": "4",
        "THREE": "3",
        "3": "3",
        "TWO": "2",
        "2": "2",
    }

    @staticmethod
    def _extract_components(card: Card) -> tuple[str, str]:
        card_text = str(card)
        if not card_text:
            return "?", "?"

        if ":" in card_text and card_text[-1] not in CompactFormatter._SUIT_MAP:
            rank, suit = card_text.split(":", maxsplit=1)
        else:
            rank = card_text[:-1] or card_text
            suit = card_text[-1]

        rank_key = rank.upper()
        suit_key = suit.upper()
        return rank_key, suit_key

    @staticmethod
    def _player_initial(player: Player) -> str:
        name = getattr(player, "mention_markdown", "")
        if name.startswith("[") and "](" in name:
            try:
                display = name.split("]")[0][1:]
            except (IndexError, AttributeError):
                display = ""
        else:
            display = getattr(player, "name", "") or ""

        display = display.strip()
        if display:
            return display[0].upper()

        user_id = getattr(player, "user_id", "?")
        return str(user_id)[0].upper()

    @staticmethod
    def _player_icon(player: Player) -> str:
        state = getattr(player, "state", None)
        if getattr(player, "did_win", False) or getattr(player, "is_winner", False):
            return "✅"
        outcome = getattr(player, "last_result", "").lower()
        if outcome in {"win", "won"}:
            return "✅"
        if outcome in {"loss", "lost"}:
            return "❌"

        if state == PlayerState.FOLD:
            return "⏸"
        if getattr(player, "eliminated", False):
            return "❌"
        if state in (PlayerState.ACTIVE, PlayerState.ALL_IN):
            return "▶️"

        return "▶️"

    @staticmethod
    def format_card(card: Card) -> str:
        """Return a short "rank+suit" rendering such as "A♠" or "K♥"."""

        rank_key, suit_key = CompactFormatter._extract_components(card)
        rank = CompactFormatter._RANK_MAP.get(rank_key, rank_key[:1])
        suit = CompactFormatter._SUIT_MAP.get(suit_key, suit_key)
        return f"{rank}{suit}"

    @staticmethod
    def format_cards(cards: List[Card]) -> str:
        """Join multiple cards with spaces, returning "—" when empty."""

        if not cards:
            return "—"
        return " ".join(CompactFormatter.format_card(card) for card in cards)

    @staticmethod
    def format_player_compact(player: Player, show_cards: bool = False) -> str:
        """Return a single-line summary of player state optimized for SMS size."""

        icon = CompactFormatter._player_icon(player)
        initial = CompactFormatter._player_initial(player)
        stack = max(getattr(player.wallet, "value", lambda: 0)(), 0)
        bet = max(getattr(player, "round_rate", 0), 0)
        line = f"{icon} {initial}:{stack} ({bet})"

        if show_cards:
            cards = CompactFormatter.format_cards(getattr(player, "cards", []) or [])
            line = f"{line} {cards}"

        return line

    @staticmethod
    def format_action_compact(player_name: str, action: str, amount: int = 0) -> str:
        """Return a compressed action line using emoji shorthand."""

        initial = (player_name or "?").strip()[:1].upper() or "?"
        action_key = action.lower()
        emoji = "➖"
        if "fold" in action_key:
            emoji = "❌"
        elif "raise" in action_key:
            emoji = "📈"
        elif "bet" in action_key:
            emoji = "💸"
        elif "call" in action_key:
            emoji = "📞"
        elif "check" in action_key:
            emoji = "👐"
        elif "all" in action_key and "in" in action_key:
            emoji = "🔥"

        amount_text = f"{amount}" if amount else ""
        return f"{initial}:{emoji}{amount_text}"

    @staticmethod
    def format_pot_compact(
        pot: int, side_pots: Optional[List[int]] = None
    ) -> str:
        """Return condensed pot text, collapsing side pots when present."""

        entries: List[str] = [str(max(pot, 0))]
        if side_pots:
            entries.extend(str(max(value, 0)) for value in side_pots if value)
        joined = "+".join(entries)
        return f"💰{joined}"
