"""Utilities for accessing key-value stores during tests and runtime."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Optional

import redis


logger = logging.getLogger(__name__)


def _to_bytes(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return str(value).encode("utf-8")


class InMemoryKV:
    """Minimal Redis-like key value store used when Redis is unavailable."""

    def __init__(self) -> None:
        self._values: Dict[str, Any] = {}
        self._lists: DefaultDict[str, List[Any]] = defaultdict(list)

    def get(self, key: str):  # pragma: no cover - trivial wrapper
        return _to_bytes(self._values.get(key))

    # pragma: no cover - trivial wrapper
    def set(self, key: str, value: Any, **kwargs: Any):
        self._values[key] = value
        return True

    # pragma: no cover - trivial wrapper
    def setnx(self, key: str, value: Any):
        if key in self._values:
            return False
        self._values[key] = value
        return True

    def exists(self, key: str):  # pragma: no cover - trivial wrapper
        return int(key in self._values or key in self._lists)

    def incrby(self, key: str, amount: int):
        current = int(self._values.get(key, 0))
        current += amount
        self._values[key] = current
        return current

    # pragma: no cover - trivial wrapper
    def delete(
        self,
        key: str,
    ):
        removed = 0
        if key in self._values:
            del self._values[key]
            removed += 1
        if key in self._lists:
            del self._lists[key]
            removed += 1
        return removed

    # pragma: no cover - trivial wrapper
    def rpush(
        self,
        key: str,
        value: Any,
    ):
        self._lists[key].append(value)
        return len(self._lists[key])

    # pragma: no cover - trivial wrapper
    def rpop(
        self,
        key: str,
    ):
        if key not in self._lists or not self._lists[key]:
            return None
        value = self._lists[key].pop()
        return _to_bytes(value)

    # High-level helpers -------------------------------------------------

    def set_user_language(self, user_id: int, language_code: str) -> None:
        """Store the preferred language for ``user_id``."""

        key = f"user:{user_id}:language"
        self._values[key] = language_code

    def get_user_language(self, user_id: int) -> Optional[str]:
        """Return the preferred language for ``user_id`` if present."""

        key = f"user:{user_id}:language"
        value = self._values.get(key)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def set_chat_language(self, chat_id: int, language_code: str) -> None:
        """Persist the preferred language for a chat lobby."""

        key = f"chat:{chat_id}:language"
        self._values[key] = language_code

    def get_chat_language(self, chat_id: int) -> Optional[str]:
        """Retrieve a stored language preference for a chat lobby."""

        key = f"chat:{chat_id}:language"
        value = self._values.get(key)
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value


class RedisKVStore:
    """Fallback Redis wrapper for environments without a Redis server."""

    def __init__(self, backend: Optional[redis.Redis] = None) -> None:
        self._backend = backend
        self._fallback = InMemoryKV()

    def _call(self, method: str, *args: Any, **kwargs: Any):
        if self._backend is not None:
            func = getattr(self._backend, method, None)
            if func is not None:
                try:
                    return func(*args, **kwargs)
                except redis.exceptions.RedisError:
                    self._backend = None
        fallback_func = getattr(self._fallback, method)
        return fallback_func(*args, **kwargs)

    def get(
        self,
        key: str,
    ):  # pragma: no cover - trivial wrapper
        return self._call("get", key)

    # pragma: no cover - trivial wrapper
    def set(
        self,
        key: str,
        value: Any,
        **kwargs: Any,
    ):
        return self._call("set", key, value, **kwargs)

    # pragma: no cover - trivial wrapper
    def setnx(
        self,
        key: str,
        value: Any,
    ):
        return self._call("setnx", key, value)

    def exists(
        self,
        key: str,
    ):
        return self._call("exists", key)

    def incrby(self, key: str, amount: int):
        return self._call("incrby", key, amount)

    # pragma: no cover - trivial wrapper
    def delete(
        self,
        key: str,
    ):
        return self._call("delete", key)

    # pragma: no cover - trivial wrapper
    def rpush(
        self,
        key: str,
        value: Any,
    ):
        return self._call("rpush", key, value)

    # pragma: no cover - trivial wrapper
    def rpop(
        self,
        key: str,
    ):
        return self._call("rpop", key)

    # ------------------------------------------------------------------
    # Language preference helpers
    # ------------------------------------------------------------------

    def set_user_language(self, user_id: int, language_code: str) -> None:
        """Store user's preferred language."""

        key = f"user:{user_id}:language"
        try:
            self.set(key, language_code, ex=None)  # No expiration
            logger.debug(
                "Stored language preference: user=%s, lang=%s",
                user_id,
                language_code,
            )
        except Exception as exc:  # pragma: no cover - logging side effect
            logger.error(
                "Failed to store language preference for user %s: %s",
                user_id,
                exc,
            )

    def get_user_language(self, user_id: int) -> Optional[str]:
        """Retrieve user's preferred language."""

        key = f"user:{user_id}:language"
        try:
            language = self.get(key)
            if isinstance(language, bytes):
                language = language.decode("utf-8")
            return language
        except Exception as exc:  # pragma: no cover - logging side effect
            logger.error(
                "Failed to retrieve language for user %s: %s",
                user_id,
                exc,
            )
            return None

    def set_chat_language(self, chat_id: int, language_code: str) -> None:
        """Persist language preference for a group or private lobby."""

        key = f"chat:{chat_id}:language"
        try:
            self.set(key, language_code, ex=None)
            logger.debug(
                "Stored chat language preference: chat=%s, lang=%s",
                chat_id,
                language_code,
            )
        except Exception as exc:  # pragma: no cover - logging side effect
            logger.error(
                "Failed to store language preference for chat %s: %s",
                chat_id,
                exc,
            )

    def get_chat_language(self, chat_id: int) -> Optional[str]:
        """Return stored language preference for chat if available."""

        key = f"chat:{chat_id}:language"
        try:
            language = self.get(key)
            if isinstance(language, bytes):
                language = language.decode("utf-8")
            return language
        except Exception as exc:  # pragma: no cover - logging side effect
            logger.error(
                "Failed to retrieve language for chat %s: %s",
                chat_id,
                exc,
            )
            return None

    def get_user_language_or_detect(
        self,
        user_id: int,
        telegram_language_code: Optional[str] = None,
    ) -> str:
        """Get user's stored language or detect from Telegram."""

        from pokerapp.i18n import translation_manager

        return translation_manager.get_user_language_or_detect(
            user_id,
            telegram_language_code=telegram_language_code,
        )


# Backwards compatibility alias for legacy imports
ResilientKV = RedisKVStore


_ADAPTER_ATTRIBUTE = "_pokerbot_resilient"
_ADAPTERS: Dict[int, RedisKVStore] = {}


def ensure_kv(kv: Optional[Any]) -> RedisKVStore:
    if isinstance(kv, RedisKVStore):
        return kv
    if kv is None:
        return RedisKVStore()

    adapter = getattr(kv, _ADAPTER_ATTRIBUTE, None)
    if isinstance(adapter, RedisKVStore):
        return adapter

    key = id(kv)
    if key in _ADAPTERS:
        return _ADAPTERS[key]

    adapter = RedisKVStore(kv)
    try:
        setattr(kv, _ADAPTER_ATTRIBUTE, adapter)
    except Exception:  # pragma: no cover - attribute assignment might fail
        _ADAPTERS[key] = adapter
    return adapter
