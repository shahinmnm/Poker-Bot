"""Structural validation for translation resources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pokerapp.i18n import SupportedLanguage, TranslationManager


TRANSLATIONS_DIR = Path(__file__).resolve().parent.parent / "translations"
REQUIRED_SECTIONS = {"ui", "msg", "help", "game", "popup"}
RTL_LANGS = {"ar", "fa", "he"}


def _load_payload(manager: TranslationManager, code: str) -> tuple[dict, dict[str, str], dict[str, object]]:
    path = TRANSLATIONS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    strings, meta = manager._normalize_translation_payload(data, code)
    return data, strings, meta


@pytest.fixture(scope="module")
def translation_manager_instance() -> TranslationManager:
    """Provide a translation manager bound to the repository resources."""

    return TranslationManager(translations_dir=str(TRANSLATIONS_DIR))


def test_translation_files_have_required_sections(translation_manager_instance: TranslationManager) -> None:
    """Every language file must exist and contain the expected sections."""

    manager = translation_manager_instance
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


def test_language_context_uses_metadata(translation_manager_instance: TranslationManager) -> None:
    """Language context should reflect rtl and font metadata from translation files."""

    manager = translation_manager_instance

    rtl_context = manager.get_language_context("ar")
    assert rtl_context.direction == "rtl"
    assert rtl_context.font == "Noto Naskh Arabic"

    ltr_context = manager.get_language_context("en")
    assert ltr_context.direction == "ltr"
    assert ltr_context.font == "system"
