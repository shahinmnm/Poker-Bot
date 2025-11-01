"""Structural validation for translation resources."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pokerapp.i18n import SupportedLanguage, TranslationManager


TRANSLATIONS_DIR = Path(__file__).resolve().parent.parent / "translations"
REQUIRED_SECTIONS = {"ui", "msg", "help", "game", "popup"}
RTL_LANGS = {"ar", "fa", "he"}


def _has_letters(value: str) -> bool:
    """Return True if *value* contains any alphabetic character."""

    return any(char.isalpha() for char in value)


def _load_payload(manager: TranslationManager, code: str) -> tuple[dict, dict[str, str], dict[str, object]]:
    path = TRANSLATIONS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    strings, meta = manager._normalize_translation_payload(data, code)
    return data, strings, meta


class _MemoryKVStore:
    """Minimal in-memory KV store for language preference tests."""

    def __init__(self) -> None:
        self._user_languages: dict[int, str] = {}

    def get_user_language(self, user_id: int) -> Optional[str]:
        return self._user_languages.get(user_id)

    def set_user_language(self, user_id: int, language_code: str) -> None:
        self._user_languages[user_id] = language_code


@lru_cache(maxsize=1)
def _translation_manager_instance() -> TranslationManager:
    """Return a cached translation manager bound to repository resources."""

    return TranslationManager(translations_dir=str(TRANSLATIONS_DIR))


def test_translation_files_have_required_sections() -> None:
    """Every language file must exist and contain the expected sections."""

    manager = _translation_manager_instance()
    data_en, strings_en, meta_en = _load_payload(manager, "en")

    assert REQUIRED_SECTIONS.issubset(data_en), "English translation must provide full structure"

    english_keys = set(strings_en.keys())

    assert meta_en["rtl"] is False
    assert isinstance(meta_en.get("font"), str)

    for lang in SupportedLanguage:
        code = lang.value
        path = TRANSLATIONS_DIR / f"{code}.json"
        assert path.exists(), f"Missing translation file for language '{code}'"

        data, strings, meta = _load_payload(manager, code)

        missing_sections = REQUIRED_SECTIONS.difference(data)
        assert not missing_sections, f"{code}: missing sections {sorted(missing_sections)}"

        assert set(strings.keys()) == english_keys, f"{code}: translation keys drift from English baseline"

        assert isinstance(meta.get("rtl"), bool), f"{code}: meta.rtl must be boolean"
        assert isinstance(meta.get("font"), str), f"{code}: meta.font must be string"

        if code in RTL_LANGS:
            assert meta["rtl"] is True, f"{code}: rtl languages must set meta.rtl true"
            assert meta.get("font"), f"{code}: rtl languages require explicit font"
            assert meta["font"] != "system", f"{code}: rtl languages must not rely on default system font"
        else:
            assert meta["rtl"] is False, f"{code}: non-RTL languages should set meta.rtl false"

        # Ensure runtime metadata mirrors file contents
        runtime_meta = manager.metadata.get(code)
        assert runtime_meta is not None, f"{code}: metadata missing after load"
        assert runtime_meta["rtl"] == meta["rtl"]
        assert runtime_meta.get("font") == meta.get("font")


def test_translations_are_localized() -> None:
    """Non-English translations should not be direct copies of English."""

    manager = _translation_manager_instance()
    english_strings = manager.translations["en"]
    total_keys = len(english_strings)

    for code, mapping in manager.translations.items():
        if code == "en":
            continue

        identical = [
            key
            for key, value in mapping.items()
            if value == english_strings.get(key) and _has_letters(value)
        ]

        overlap_ratio = len(identical) / total_keys if total_keys else 0

        assert (
            overlap_ratio <= 0.2
        ), f"{code}: {len(identical)} keys still match English ({overlap_ratio:.1%})"

        assert len(identical) <= 8, f"{code}: too many untranslated strings: {sorted(identical)}"


def test_language_context_uses_metadata() -> None:
    """Language context should reflect rtl and font metadata from translation files."""

    manager = _translation_manager_instance()

    rtl_context = manager.get_language_context("ar")
    assert rtl_context.direction == "rtl"
    assert rtl_context.font == "Noto Naskh Arabic"

    ltr_context = manager.get_language_context("en")
    assert ltr_context.direction == "ltr"
    assert ltr_context.font == "system"

    persian_context = manager.get_language_context("fa")
    assert persian_context.direction == "rtl"
    assert persian_context.font == "Vazirmatn"

    spanish_context = manager.get_language_context("es")
    assert spanish_context.direction == "ltr"
    assert spanish_context.font == "system"


def test_translation_keys_remain_in_sync() -> None:
    """Ensure every language file exposes the same translation keys as English."""

    manager = _translation_manager_instance()
    english_keys = set(manager.translations["en"].keys())

    for code, mapping in manager.translations.items():
        language_keys = set(mapping.keys())
        missing = english_keys - language_keys
        extra = language_keys - english_keys

        assert not missing, f"{code}: missing translation keys {sorted(missing)}"
        assert not extra, f"{code}: unexpected translation keys {sorted(extra)}"


def test_translate_missing_language_falls_back_to_english() -> None:
    """Unknown language codes should fall back to English strings."""

    manager = TranslationManager(translations_dir=str(TRANSLATIONS_DIR))
    key = "msg.welcome"

    english_value = manager.translate(key, language="en")
    fallback_value = manager.translate(key, language="zz")

    assert fallback_value == english_value


def test_translate_missing_key_uses_english_baseline() -> None:
    """Missing keys in a locale should use the English value as a fallback."""

    manager = TranslationManager(translations_dir=str(TRANSLATIONS_DIR))
    key = next(iter(manager.translations["en"].keys()))

    # Simulate a missing translation key in Spanish.
    spanish_map = manager.translations["es"].copy()
    original = spanish_map.pop(key, None)
    manager.translations["es"] = spanish_map

    try:
        translated = manager.translate(key, language="es")
        assert translated == manager.translations["en"][key]
    finally:
        # Restore original mapping so subsequent tests see the real data.
        if original is not None:
            manager.translations["es"][key] = original


def test_detected_language_is_stored_when_missing_preference() -> None:
    """Telegram language detection should seed the kv store when empty."""

    manager = TranslationManager(translations_dir=str(TRANSLATIONS_DIR))
    kv = _MemoryKVStore()
    manager.attach_kvstore(kv)

    resolved = manager.get_user_language_or_detect(
        42,
        telegram_language_code="es-ES",
    )

    assert resolved == "es"
    assert kv.get_user_language(42) == "es"


def test_stored_language_preference_is_not_overwritten_by_detection() -> None:
    """Manual language choices must win over Telegram's reported locale."""

    manager = TranslationManager(translations_dir=str(TRANSLATIONS_DIR))
    kv = _MemoryKVStore()
    manager.attach_kvstore(kv)

    kv.set_user_language(7, "fa")

    resolved = manager.get_user_language_or_detect(
        7,
        telegram_language_code="en",
    )

    assert resolved == "fa"
    assert kv.get_user_language(7) == "fa"
