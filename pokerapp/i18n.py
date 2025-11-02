"""
Internationalization (i18n) support for poker bot.

Provides language detection, translation management, and locale-aware
formatting for multi-language user experiences.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path
from enum import Enum


logger = logging.getLogger(__name__)


class SupportedLanguage(Enum):
    """Supported language codes (ISO 639-1)."""

    ENGLISH = "en"
    SPANISH = "es"
    RUSSIAN = "ru"
    ARABIC = "ar"
    PERSIAN = "fa"
    HEBREW = "he"


@dataclass(frozen=True)
class LanguageContext:
    """Resolved language metadata for rendering."""

    code: str
    direction: str
    font: str


class _SafeFormatDict(dict):
    """Dictionary that leaves unknown placeholders intact during formatting."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - formatting guard
        return "{" + key + "}"


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
        self.metadata: Dict[str, Dict[str, Any]] = {}
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

        if user_id is not None:
            return self.get_user_language_or_detect(user_id)

        return self.DEFAULT_LANGUAGE

    def get_language_context(self, language: Optional[str] = None) -> LanguageContext:
        """Return rendering metadata for *language*."""

        code = self.resolve_language(lang=language)
        direction = "rtl" if self.is_rtl(code) else "ltr"
        font: Optional[str] = None
        if code in self.metadata:
            font = self.metadata[code].get("font")
        if not font:
            font = self._LANGUAGE_FONT_MAP.get(code)
        if font is None:
            font = self._DEFAULT_FONT_RTL if direction == "rtl" else self._DEFAULT_FONT_LTR

        return LanguageContext(code=code, direction=direction, font=font)

    def get_translator(self, language: Optional[str] = None) -> Callable[[str], str]:
        """Return a callable that translates keys for *language*."""

        resolved = self.resolve_language(lang=language)

        def _translator(key: str, **kwargs: Any) -> str:
            return self.translate(key, language=resolved, **kwargs)

        return _translator

    def get_user_language_or_detect(
        self,
        user_id: int,
        *,
        telegram_language_code: Optional[str] = None,
    ) -> str:
        """Return the preferred language for *user_id* with sensible fallbacks."""

        stored_language: Optional[str] = None

        if self._kvstore is not None:
            try:
                stored_language = self._kvstore.get_user_language(user_id)
            except AttributeError:
                stored_language = None

        normalized_stored: Optional[str] = None
        if stored_language:
            candidate = stored_language.lower()
            if candidate in self.translations:
                normalized_stored = candidate

        detected_language: Optional[str] = None
        if telegram_language_code:
            detected_language = self.detect_language(telegram_language_code)

        if normalized_stored:
            return normalized_stored

        if detected_language:
            if self._kvstore is not None:
                try:
                    self._kvstore.set_user_language(user_id, detected_language)
                except AttributeError:  # pragma: no cover - defensive
                    pass

            return detected_language

        return self.DEFAULT_LANGUAGE

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
                    payload = json.load(f)
                strings, meta = self._normalize_translation_payload(payload, lang_code)
                self.translations[lang_code] = strings
                self.metadata[lang_code] = meta
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

    def _normalize_translation_payload(
        self, payload: Dict[str, Any], lang_code: str
    ) -> tuple[Dict[str, str], Dict[str, Any]]:
        """Validate payload sections and flatten into lookup dictionary."""

        required_sections = {"ui", "msg", "help", "game", "popup"}
        missing = required_sections.difference(payload)
        if missing:
            raise ValueError(
                f"Translation file '{lang_code}.json' missing sections: {sorted(missing)}"
            )

        meta = payload.get("meta", {})
        if not isinstance(meta, dict):
            raise ValueError(
                f"Translation file '{lang_code}.json' meta section must be an object"
            )

        rtl = meta.get("rtl", False)
        if not isinstance(rtl, bool):
            raise ValueError(
                f"Translation file '{lang_code}.json' meta.rtl must be boolean"
            )

        font = meta.get("font")
        if font is not None and not isinstance(font, str):
            raise ValueError(
                f"Translation file '{lang_code}.json' meta.font must be a string when provided"
            )

        flattened: Dict[str, str] = {}

        def _flatten(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    child_prefix = f"{prefix}.{child_key}" if prefix else child_key
                    _flatten(child_prefix, child_value)
            else:
                if not isinstance(value, str):
                    raise ValueError(
                        f"Translation value for '{prefix}' in '{lang_code}.json' must be a string"
                    )
                flattened[prefix] = value

        # Flatten UI without prefix, keep nested namespaces
        _flatten("", payload["ui"])
        # Flatten other sections with their namespace prefixes
        for section in ("game", "help", "msg"):
            _flatten(section, payload[section])

        # Popup namespace
        _flatten("popup", payload["popup"])

        # Viewer overlays (legacy top-level namespace in translation files)
        if "viewer" in payload:
            _flatten("viewer", payload["viewer"])

        return flattened, {"rtl": rtl, "font": font}

    @staticmethod
    def _build_structured_payload(flat: Dict[str, str]) -> Dict[str, Any]:
        """Convert flat translation keys into sectioned payload."""

        structured: Dict[str, Any] = {
            "meta": {},
            "ui": {},
            "msg": {},
            "help": {},
            "game": {},
            "popup": {},
        }

        prefix_section_map: Dict[str, tuple[str, bool]] = {
            "game": ("game", True),
            "action": ("ui", False),
            "button": ("ui", False),
            "msg": ("msg", True),
            "error": ("msg", False),
            "help": ("help", True),
            "lobby": ("ui", False),
            "model": ("ui", False),
            "controller": ("ui", False),
            "viewer": ("ui", False),
            "card": ("game", False),
            "hand": ("game", False),
            "settings": ("ui", False),
        }

        def insert(target: Dict[str, Any], parts: List[str], value: str) -> None:
            key = parts[0]
            if len(parts) == 1:
                target[key] = value
                return
            child = target.setdefault(key, {})
            if not isinstance(child, dict):
                raise ValueError(
                    f"Cannot insert into non-dict node for key: {'.'.join(parts)}"
                )
            insert(child, parts[1:], value)

        for full_key, value in flat.items():
            parts = full_key.split(".")
            prefix = parts[0]

            if prefix == "viewer" and len(parts) > 1 and parts[1] == "fold_confirmation":
                insert(structured["popup"], parts[1:], value)
                continue

            if prefix == "controller" and len(parts) > 1 and parts[1] == "toast":
                insert(structured["popup"], parts[1:], value)
                continue

            mapping = prefix_section_map.get(prefix)
            if not mapping:
                raise KeyError(f"No section mapping for translation key: {full_key}")

            section, drop_prefix = mapping
            target_parts = parts[1:] if drop_prefix else parts
            insert(structured[section], target_parts, value)

        return structured

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

        payload = self._build_structured_payload(default_translations)
        payload["meta"] = {"rtl": False, "font": self._DEFAULT_FONT_LTR}

        # Save to file
        en_file = self.translations_dir / "en.json"
        with open(en_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)

        strings, meta = self._normalize_translation_payload(payload, "en")
        self.translations["en"] = strings
        self.metadata["en"] = meta
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
            safe_kwargs = _SafeFormatDict(**kwargs)
        except TypeError:
            safe_kwargs = _SafeFormatDict()
            safe_kwargs.update({str(k): v for k, v in kwargs.items()})

        try:
            return translation.format_map(safe_kwargs)
        except Exception as exc:  # pragma: no cover - defensive formatting guard
            logger.error(
                "Failed to format translation: %s (key=%s, lang=%s)",
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
        if language in self.metadata:
            rtl = self.metadata[language].get("rtl")
            if isinstance(rtl, bool):
                return rtl
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
        """Return metadata for supported languages.

        Each entry includes the ISO code, a native display name, and an
        associated flag emoji for richer language picker UIs.
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

        language_flags = {
            "en": "🇺🇸",
            "es": "🇪🇸",
            "fr": "🇫🇷",
            "de": "🇩🇪",
            "pt": "🇵🇹",
            "ru": "🇷🇺",
            "zh": "🇨🇳",
            "ja": "🇯🇵",
            "ko": "🇰🇷",
            "ar": "🇸🇦",
            "hi": "🇮🇳",
            "it": "🇮🇹",
            "nl": "🇳🇱",
            "pl": "🇵🇱",
            "tr": "🇹🇷",
            "vi": "🇻🇳",
            "th": "🇹🇭",
            "id": "🇮🇩",
            "fa": "🇮🇷",
            "he": "🇮🇱",
        }

        supported = []
        for code in sorted(self.translations.keys()):
            supported.append(
                {
                    "code": code,
                    "name": language_names.get(code, code.upper()),
                    "flag": language_flags.get(code, "🏳️"),
                }
            )

        return supported


# Singleton instance
translation_manager = TranslationManager()
