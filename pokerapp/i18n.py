"""
Internationalization (i18n) support for poker bot.

Provides language detection, translation management, and locale-aware
formatting for multi-language user experiences.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from pathlib import Path
from enum import Enum


logger = logging.getLogger(__name__)


class SupportedLanguage(Enum):
    """Supported language codes (ISO 639-1)."""
    ENGLISH = "en"
    SPANISH = "es"
    FRENCH = "fr"
    GERMAN = "de"
    PORTUGUESE = "pt"
    RUSSIAN = "ru"
    CHINESE = "zh"
    JAPANESE = "ja"
    KOREAN = "ko"
    ARABIC = "ar"
    HINDI = "hi"
    ITALIAN = "it"
    DUTCH = "nl"
    POLISH = "pl"
    TURKISH = "tr"
    VIETNAMESE = "vi"
    THAI = "th"
    INDONESIAN = "id"
    PERSIAN = "fa"
    HEBREW = "he"


@dataclass(frozen=True)
class LanguageContext:
    """Resolved language metadata for rendering."""

    code: str
    direction: str
    font: str


class TranslationManager:
    """
    Manages translations and locale-specific formatting.

    Features:
    - Auto-detects user language from Telegram
    - Loads translations from JSON files
    - Provides fallback to English
    - Supports RTL languages
    - Locale-aware number formatting
    """

    # RTL (right-to-left) languages
    RTL_LANGUAGES = {"ar", "he", "fa"}

    # Default fallback language
    DEFAULT_LANGUAGE = "en"

    # Default font fallbacks for layout metadata
    _DEFAULT_FONT_LTR = "system"
    _DEFAULT_FONT_RTL = "Noto Naskh Arabic"

    # Per-language font overrides to improve RTL rendering
    _LANGUAGE_FONT_MAP = {
        "ar": "Noto Naskh Arabic",
        "fa": "Vazirmatn",
        "he": "Rubik",
    }

    def __init__(self, translations_dir: str = "translations"):
        """
        Initialize translation manager.

        Args:
            translations_dir: Directory containing translation JSON files
        """
        self.translations_dir = Path(translations_dir)
        self.translations: Dict[str, Dict[str, str]] = {}
        self._kvstore: Optional[Any] = None
        self._load_translations()

    # ------------------------------------------------------------------
    # Integration helpers
    # ------------------------------------------------------------------
    def attach_kvstore(self, kvstore: Any) -> None:
        """Attach key-value store used for language lookups."""

        self._kvstore = kvstore

    # ------------------------------------------------------------------
    # Translation lookups
    # ------------------------------------------------------------------
    def resolve_language(
        self,
        *,
        user_id: Optional[int] = None,
        lang: Optional[str] = None,
    ) -> str:
        """Resolve an appropriate language code for a request."""

        if lang:
            candidate = lang.lower()
            if candidate in self.translations:
                return candidate

        if user_id is not None and self._kvstore is not None:
            try:
                stored = self._kvstore.get_user_language(user_id)
            except AttributeError:
                stored = None
            if stored and stored in self.translations:
                return stored

        return self.DEFAULT_LANGUAGE

    def get_language_context(self, language: Optional[str] = None) -> LanguageContext:
        """Return rendering metadata for *language*."""

        code = self.resolve_language(lang=language)
        direction = "rtl" if self.is_rtl(code) else "ltr"
        font = self._LANGUAGE_FONT_MAP.get(code)
        if font is None:
            font = self._DEFAULT_FONT_RTL if direction == "rtl" else self._DEFAULT_FONT_LTR

        return LanguageContext(code=code, direction=direction, font=font)

    def t(
        self,
        key: str,
        *,
        user_id: Optional[int] = None,
        lang: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Translate *key* using the most relevant language context."""

        language = self.resolve_language(user_id=user_id, lang=lang)
        return self.translate(key, language=language, **kwargs)

    def _load_translations(self) -> None:
        """Load all translation files from disk."""

        if not self.translations_dir.exists():
            logger.warning(
                "Translations directory not found: %s. Creating with default English.",
                self.translations_dir,
            )
            self.translations_dir.mkdir(parents=True, exist_ok=True)
            self._create_default_english()
            return

        # Load each language file
        for lang_file in self.translations_dir.glob("*.json"):
            lang_code = lang_file.stem
            try:
                with open(lang_file, "r", encoding="utf-8") as f:
                    self.translations[lang_code] = json.load(f)
                logger.info("✅ Loaded translations for: %s", lang_code)
            except Exception as exc:
                logger.error(
                    "Failed to load translations for %s: %s",
                    lang_code,
                    exc,
                )

        # Ensure English exists as fallback
        if "en" not in self.translations:
            self._create_default_english()

    def _create_default_english(self) -> None:
        """Create default English translation file."""

        default_translations = {
            # === GAME STATES ===
            "game.state.initial": "Waiting for players",
            "game.state.pre_flop": "Pre-flop",
            "game.state.flop": "Flop",
            "game.state.turn": "Turn",
            "game.state.river": "River",
            "game.state.finished": "Showdown",

            # === ACTIONS ===
            "action.check": "Check",
            "action.call": "Call",
            "action.fold": "Fold",
            "action.raise": "Raise",
            "action.all_in": "All-In",

            # === BUTTON LABELS ===
            "button.check": "✅ Check",
            "button.call": "💵 Call ${amount}",
            "button.fold": "❌ Fold",
            "button.raise": "⬆️ Raise",
            "button.all_in": "🔥 All-In",
            "button.ready": "✋ Ready",
            "button.start": "🎮 Start Game",
            "button.join": "➕ Join",
            "button.leave": "➖ Leave",

            # === MESSAGES ===
            "msg.welcome": "👋 Welcome to Texas Hold'em Poker!",
            "msg.game_started": "🎮 Game started! Good luck!",
            "msg.your_turn": "🎯 It's your turn!",
            "msg.player_folded": "❌ {player} folded",
            "msg.player_called": "💵 {player} called ${amount}",
            "msg.player_raised": "⬆️ {player} raised to ${amount}",
            "msg.player_checked": "✅ {player} checked",
            "msg.player_all_in": "🔥 {player} went all-in with ${amount}",
            "msg.winner": "🏆 {player} wins ${amount}!",
            "msg.pot": "💰 Pot: ${amount}",
            "msg.current_bet": "🎯 Current bet: ${amount}",

            # === ERRORS ===
            "error.not_your_turn": "❌ Not your turn!",
            "error.invalid_action": "❌ Invalid action",
            "error.insufficient_funds": "❌ Insufficient funds",
            "error.no_game": "❌ No active game",
            "error.game_in_progress": "❌ Game already in progress",
            "error.not_enough_players": "❌ Need at least 2 players to start",
            "error.max_players": "❌ Maximum {max} players allowed",

            # === HELP TEXT ===
            "help.title": "🎴 How to Play Poker",
            "help.commands": "📋 Commands",
            "help.ready": "/ready - Join the game",
            "help.start": "/start - Begin playing",
            "help.status": "/status - Check game state",
            "help.help": "/help - Show this message",
            "help.language": "/language - Change language",

            # === LOBBY ===
            "lobby.title": "🎮 Game Lobby",
            "lobby.players": "👥 Players ({count}/{max})",
            "lobby.waiting": "⏳ Waiting for host to start...",
            "lobby.host": "👑 Host",
            "lobby.joined": "✅ {player} joined!",
            "lobby.left": "👋 {player} left",

            # === CARDS ===
            "card.rank.A": "Ace",
            "card.rank.K": "King",
            "card.rank.Q": "Queen",
            "card.rank.J": "Jack",
            "card.rank.10": "Ten",
            "card.rank.9": "Nine",
            "card.rank.8": "Eight",
            "card.rank.7": "Seven",
            "card.rank.6": "Six",
            "card.rank.5": "Five",
            "card.rank.4": "Four",
            "card.rank.3": "Three",
            "card.rank.2": "Two",
            "card.suit.spades": "Spades",
            "card.suit.hearts": "Hearts",
            "card.suit.diamonds": "Diamonds",
            "card.suit.clubs": "Clubs",

            # === HAND RANKINGS ===
            "hand.royal_flush": "Royal Flush",
            "hand.straight_flush": "Straight Flush",
            "hand.four_of_kind": "Four of a Kind",
            "hand.full_house": "Full House",
            "hand.flush": "Flush",
            "hand.straight": "Straight",
            "hand.three_of_kind": "Three of a Kind",
            "hand.two_pair": "Two Pair",
            "hand.pair": "Pair",
            "hand.high_card": "High Card",
        }

        # Save to file
        en_file = self.translations_dir / "en.json"
        with open(en_file, "w", encoding="utf-8") as f:
            json.dump(default_translations, f, indent=2, ensure_ascii=False)

        self.translations["en"] = default_translations
        logger.info("✅ Created default English translations")

    def detect_language(self, telegram_language_code: Optional[str]) -> str:
        """
        Detect user's preferred language from Telegram settings.

        Args:
            telegram_language_code: Language code from Telegram user object

        Returns:
            Detected language code (defaults to 'en')

        Example:
            >>> detect_language("es-ES")
            "es"
        """
        if not telegram_language_code:
            return self.DEFAULT_LANGUAGE

        # Extract primary language code (e.g., "es-ES" → "es")
        primary_code = telegram_language_code.split("-")[0].lower()

        # Check if we support this language
        if primary_code in self.translations:
            return primary_code

        # Fallback to English
        logger.debug(
            "Unsupported language code: %s, falling back to English",
            telegram_language_code,
        )
        return self.DEFAULT_LANGUAGE

    def translate(
        self,
        key: str,
        language: str = "en",
        **kwargs: Any,
    ) -> str:
        """
        Get translated string for a given key.

        Args:
            key: Translation key (e.g., "msg.welcome")
            language: Target language code
            **kwargs: Variables for string formatting

        Returns:
            Translated and formatted string

        Example:
            >>> translate("msg.player_called", language="es", player="Juan", amount=50)
            "💵 Juan apostó $50"
        """
        # Get language translations (with English fallback)
        lang_dict = self.translations.get(
            language,
            self.translations.get(self.DEFAULT_LANGUAGE, {}),
        )

        # Get translation string
        translation = lang_dict.get(key)

        # Fallback to English if not found
        if translation is None:
            translation = self.translations.get(self.DEFAULT_LANGUAGE, {}).get(key)

        # Ultimate fallback: return key itself
        if translation is None:
            logger.warning(
                "Missing translation for key '%s' in language '%s'",
                key,
                language,
            )
            return f"[{key}]"

        # Format with provided variables
        try:
            return translation.format(**kwargs)
        except KeyError as exc:
            logger.error(
                "Missing variable in translation: %s (key=%s, lang=%s)",
                exc,
                key,
                language,
            )
            return translation

    def is_rtl(self, language: str) -> bool:
        """
        Check if language uses right-to-left text direction.

        Args:
            language: Language code

        Returns:
            True if RTL language
        """
        return language in self.RTL_LANGUAGES

    def format_currency(
        self,
        amount: int,
        language: str = "en",
        currency_symbol: str = "$",
    ) -> str:
        """
        Format currency amount with locale-aware separators.

        Args:
            amount: Dollar amount
            language: Language code for formatting rules
            currency_symbol: Currency symbol to use

        Returns:
            Formatted currency string

        Example:
            >>> format_currency(1500, "en")
            "$1,500"
            >>> format_currency(1500, "de")
            "1.500$"
        """
        # Language-specific formatting rules
        formatting_rules = {
            "en": lambda a, s: f"{s}{a:,}",              # $1,500
            "es": lambda a, s: f"{s}{a:,}".replace(",", "."),  # $1.500
            "fr": lambda a, s: f"{a:,} {s}".replace(",", " "),  # 1 500 $
            "de": lambda a, s: f"{a:,} {s}".replace(",", "."),  # 1.500 $
            "ru": lambda a, s: f"{a:,} {s}".replace(",", " "),  # 1 500 $
            "zh": lambda a, s: f"{s}{a:,}",              # $1,500
            "ja": lambda a, s: f"{s}{a:,}",              # $1,500
            "ar": lambda a, s: f"{s}{a:,}",              # $1,500 (RTL handled separately)
        }

        formatter = formatting_rules.get(language, formatting_rules["en"])
        return formatter(amount, currency_symbol)

    def get_supported_languages(self) -> List[Dict[str, str]]:
        """
        Get list of supported languages with native names.

        Returns:
            List of dicts with 'code' and 'name' keys
        """
        language_names = {
            "en": "English",
            "es": "Español",
            "fr": "Français",
            "de": "Deutsch",
            "pt": "Português",
            "ru": "Русский",
            "zh": "中文",
            "ja": "日本語",
            "ko": "한국어",
            "ar": "العربية",
            "hi": "हिन्दी",
            "it": "Italiano",
            "nl": "Nederlands",
            "pl": "Polski",
            "tr": "Türkçe",
            "vi": "Tiếng Việt",
            "th": "ไทย",
            "id": "Bahasa Indonesia",
            "fa": "فارسی",
            "he": "עברית",
        }

        return [
            {"code": code, "name": language_names.get(code, code.upper())}
            for code in sorted(self.translations.keys())
        ]


# Singleton instance
translation_manager = TranslationManager()
