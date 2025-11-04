"""Telegram WebApp authentication utilities."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
) -> Optional[Dict[str, Any]]:
    """Verify a Telegram WebApp ``initData`` signature and return user info."""

    if not init_data or not bot_token:
        return None

    parsed: Dict[str, str] = dict(parse_qsl(init_data, keep_blank_values=True))
    hash_value = parsed.pop("hash", None)
    if not hash_value:
        return None

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

    if not hmac.compare_digest(calculated_hash, hash_value):
        return None

    raw_user_data = parsed.get("user")
    if not raw_user_data:
        return None

    try:
        user_data = json.loads(raw_user_data)
    except json.JSONDecodeError:
        return None

    return user_data


# Session token storage (Redis-backed in production)
_session_store: Dict[str, Dict[str, Any]] = {}


def generate_session_token(
    user_id: int,
    username: Optional[str] = None,
    ttl_seconds: int = 86_400,
) -> str:
    """Generate a secure session token for an authenticated user."""

    token = secrets.token_urlsafe(32)
    now = time.time()

    _session_store[token] = {
        "user_id": user_id,
        "username": username,
        "created_at": now,
        "expires_at": now + ttl_seconds,
    }

    return token


def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a session token and return associated user details if valid."""

    if not token:
        return None

    session = _session_store.get(token)
    if not session:
        return None

    if time.time() > session["expires_at"]:
        del _session_store[token]
        return None

    return {
        "user_id": session["user_id"],
        "username": session.get("username"),
    }
