"""Utility helpers for working with cached inline keyboard layouts.

These helpers strip the per-message version token from callback payloads
before a layout is cached and re-insert the latest version when the layout
is rehydrated.  Without this normalisation, cached keyboards can include a
stale ``message_version`` value which causes legitimate button presses to be
rejected as "expired" even though the UI was freshly rendered.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


_VERSION_AWARE_PREFIXES = {"action", "raise_amt"}


def _should_normalise(parts: List[str]) -> bool:
    """Return ``True`` if the callback payload follows the expected schema."""

    if not parts:
        return False

    return parts[0] in _VERSION_AWARE_PREFIXES and len(parts) >= 3


def strip_version_token(callback_data: Optional[str], version: Optional[int]) -> Optional[str]:
    """Remove the trailing version token from ``callback_data`` when present.

    Cached layouts should not persist a specific ``message_version`` because the
    next render will bump the counter.  We strip the token so cached layouts can
    be reused safely for subsequent renders.
    """

    if callback_data is None or version is None:
        return callback_data

    version_str = str(version)
    parts = callback_data.split(":")

    if not _should_normalise(parts):
        return callback_data

    if len(parts) >= 3 and parts[-2] == version_str:
        parts.pop(-2)
        return ":".join(parts)

    return callback_data


def apply_version_token(callback_data: Optional[str], version: Optional[int]) -> Optional[str]:
    """Inject the provided ``version`` into ``callback_data`` before caching.

    When a cached keyboard is reused we re-hydrate the callback payloads with
    the current ``message_version`` so Telegram button presses continue to be
    accepted by the backend.
    """

    if callback_data is None or version is None:
        return callback_data

    version_str = str(version)
    parts = callback_data.split(":")

    if not _should_normalise(parts):
        return callback_data

    if len(parts) >= 3 and parts[-2] == version_str:
        return callback_data

    if len(parts) >= 2:
        parts.insert(len(parts) - 1, version_str)
        return ":".join(parts)

    return callback_data


def serialise_keyboard_layout(
    inline_keyboard: Sequence[Sequence[InlineKeyboardButton]],
    *,
    version: Optional[int],
) -> List[List[Dict[str, str]]]:
    """Convert ``InlineKeyboardMarkup`` rows into a serialisable structure.

    The resulting layout is safe to store in the render cache because any
    version token embedded in the callback payload is stripped.
    """

    serialised: List[List[Dict[str, str]]] = []
    for row in inline_keyboard:
        row_payload: List[Dict[str, str]] = []
        for button in row:
            entry: Dict[str, str] = {"text": button.text}
            if getattr(button, "callback_data", None) is not None:
                entry["callback_data"] = strip_version_token(
                    button.callback_data, version
                )
            if getattr(button, "url", None):
                entry["url"] = button.url
            row_payload.append(entry)
        serialised.append(row_payload)

    return serialised


def rehydrate_keyboard_layout(
    layout: Iterable[Iterable[Dict[str, str]]],
    *,
    version: Optional[int],
) -> InlineKeyboardMarkup:
    """Build an ``InlineKeyboardMarkup`` from cached layout metadata."""

    rows: List[List[InlineKeyboardButton]] = []
    for row in layout:
        buttons: List[InlineKeyboardButton] = []
        for data in row:
            button_kwargs = dict(data)
            callback_data = button_kwargs.get("callback_data")
            button_kwargs["callback_data"] = apply_version_token(
                callback_data, version
            )
            buttons.append(InlineKeyboardButton(**button_kwargs))
        rows.append(buttons)

    return InlineKeyboardMarkup(rows)
