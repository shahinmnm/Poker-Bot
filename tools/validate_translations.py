#!/usr/bin/env python3
"""Validate translation files for structure and completeness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, List

import sys

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ensure_repo_root_on_path() -> None:
    """Add the repository root to ``sys.path`` when running as a script."""

    repo_root = str(REPO_ROOT)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def _load_translation_dependencies():
    """Import translation modules lazily to satisfy linting constraints."""

    _ensure_repo_root_on_path()
    from pokerapp.i18n import SupportedLanguage, TranslationManager

    return SupportedLanguage, TranslationManager


REQUIRED_SECTIONS = {"ui", "msg", "help", "game", "popup"}
RTL_LANGS = {"ar", "fa", "he"}


def _has_letters(value: str) -> bool:
    """Return True if *value* includes any alphabetic character."""

    return any(char.isalpha() for char in value)


def _validate_directory(translations_dir: Path) -> List[str]:
    SupportedLanguage, TranslationManager = _load_translation_dependencies()
    try:
        manager = TranslationManager(translations_dir=str(translations_dir))
    except Exception as exc:  # pragma: no cover - defensive guard
        return [f"Failed to load translations: {exc}"]

    base_path = translations_dir / "en.json"
    if not base_path.exists():
        return ["Missing base translation file: en.json"]

    with base_path.open("r", encoding="utf-8") as fh:
        english_payload = json.load(fh)

    try:
        english_strings, _ = manager._normalize_translation_payload(english_payload, "en")
    except Exception as exc:  # pragma: no cover - defensive guard
        return [f"Failed to normalize English translations: {exc}"]

    english_keys = set(english_strings.keys())
    errors: List[str] = []

    for language in SupportedLanguage:
        code = language.value
        path = translations_dir / f"{code}.json"
        if not path.exists():
            errors.append(f"{code}: translation file missing")
            continue

        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            errors.append(f"{code}: invalid JSON ({exc})")
            continue

        missing_sections = REQUIRED_SECTIONS.difference(payload)
        if missing_sections:
            errors.append(
                f"{code}: missing sections {', '.join(sorted(missing_sections))}"
            )

        try:
            strings, meta = manager._normalize_translation_payload(payload, code)
        except Exception as exc:
            errors.append(f"{code}: {exc}")
            continue

        missing_keys = english_keys.difference(strings)
        extra_keys = set(strings).difference(english_keys)

        if missing_keys:
            errors.append(
                f"{code}: missing keys {', '.join(sorted(missing_keys))}"
            )

        if extra_keys:
            errors.append(
                f"{code}: unexpected keys {', '.join(sorted(extra_keys))}"
            )

        rtl_flag = bool(meta.get("rtl"))
        if code in RTL_LANGS:
            if not rtl_flag:
                errors.append(f"{code}: rtl flag must be true")
            font = meta.get("font")
            if not isinstance(font, str) or font.strip() == "" or font == "system":
                errors.append(f"{code}: rtl languages require explicit font metadata")

        if code != "en":
            identical = [
                key
                for key, value in strings.items()
                if english_strings.get(key) == value and _has_letters(value)
            ]

            ratio = len(identical) / len(english_strings) if english_strings else 0.0
            if ratio > 0.2 or len(identical) > 8:
                errors.append(
                    f"{code}: potential untranslated strings ({len(identical)} match English)"
                )

    return errors


def _format_errors(errors: Iterable[str]) -> str:
    return "\n".join(f"- {error}" for error in errors)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate translation JSON files.")
    parser.add_argument(
        "--dir",
        default="translations",
        type=Path,
        help="Path to the translations directory (default: translations)",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    translations_dir: Path = args.dir

    if not translations_dir.exists():
        print(f"Translation directory not found: {translations_dir}")
        return 1

    errors = _validate_directory(translations_dir)
    if errors:
        print("Translation validation failed:")
        print(_format_errors(errors))
        return 1

    print("All translation files are valid.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
