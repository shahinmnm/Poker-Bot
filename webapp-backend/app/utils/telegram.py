"""Telegram WebApp authentication utilities."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

import redis


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
_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """Get or create Redis client for session storage."""

    global _redis_client

    if _redis_client is None:
        host = os.getenv("REDIS_HOST", "redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        _redis_client = redis.Redis(host=host, port=port, decode_responses=True)

    return _redis_client


def generate_session_token(
    user_id: int,
    username: Optional[str] = None,
    ttl_seconds: int = 86_400,
) -> str:
    """Generate a secure session token for an authenticated user.

    The TTL is capped at one hour to match the Redis session persistence policy.
    """

    token = secrets.token_urlsafe(32)

    # Sessions are persisted for one hour in Redis regardless of the provided TTL
    ttl_seconds = min(ttl_seconds, 3600)

    session_data = {
        "user_id": user_id,
        "username": username,
        "created_at": int(time.time()),
    }

    client = get_redis_client()
    client.setex(f"session:{token}", ttl_seconds, json.dumps(session_data))

    return token


def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a session token and return associated user details if valid."""

    if not token:
        return None

    client = get_redis_client()

    try:
        raw_session = client.get(f"session:{token}")
    except redis.RedisError:
        return None

    if not raw_session:
        return None

    try:
        session = json.loads(raw_session)
    except json.JSONDecodeError:
        return None

    return {
        "user_id": session["user_id"],
        "username": session.get("username"),
    }
