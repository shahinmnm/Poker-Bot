"""Telegram-related utility helpers."""

from __future__ import annotations

import hashlib
import hmac
from typing import Mapping
from urllib.parse import parse_qsl


def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """Verify a Telegram WebApp ``initData`` signature.

    Parameters
    ----------
    init_data:
        The raw query string provided by Telegram via the WebApp interface.
    bot_token:
        The bot token used to calculate the signature secret.

    Returns
    -------
    bool
        ``True`` when the provided signature matches the calculated hash,
        ``False`` otherwise.
    """

    if not init_data or not bot_token:
        return False

    parsed: Mapping[str, str] = dict(parse_qsl(init_data))
    hash_value = parsed.pop("hash", None)
    if not hash_value:
        return False

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(calculated_hash, hash_value)
