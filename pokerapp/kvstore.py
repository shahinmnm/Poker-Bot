"""Utilities for accessing key-value stores during tests and runtime."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Optional

import redis


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

    def set(self, key: str, value: Any):  # pragma: no cover - trivial wrapper
        self._values[key] = value
        return True

    def incrby(self, key: str, amount: int):
        current = int(self._values.get(key, 0))
        current += amount
        self._values[key] = current
        return current

    def delete(
        self,
        key: str,
    ):  # pragma: no cover - trivial wrapper
        removed = 0
        if key in self._values:
            del self._values[key]
            removed += 1
        if key in self._lists:
            del self._lists[key]
            removed += 1
        return removed

    def rpush(
        self,
        key: str,
        value: Any,
    ):  # pragma: no cover - trivial wrapper
        self._lists[key].append(value)
        return len(self._lists[key])

    def rpop(
        self,
        key: str,
    ):  # pragma: no cover - trivial wrapper
        if key not in self._lists or not self._lists[key]:
            return None
        value = self._lists[key].pop()
        return _to_bytes(value)


class ResilientKV:
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

    def set(
        self,
        key: str,
        value: Any,
    ):  # pragma: no cover - trivial wrapper
        return self._call("set", key, value)

    def incrby(self, key: str, amount: int):
        return self._call("incrby", key, amount)

    def delete(
        self,
        key: str,
    ):  # pragma: no cover - trivial wrapper
        return self._call("delete", key)

    def rpush(
        self,
        key: str,
        value: Any,
    ):  # pragma: no cover - trivial wrapper
        return self._call("rpush", key, value)

    def rpop(
        self,
        key: str,
    ):  # pragma: no cover - trivial wrapper
        return self._call("rpop", key)


_ADAPTER_ATTRIBUTE = "_pokerbot_resilient"
_ADAPTERS: Dict[int, ResilientKV] = {}


def ensure_kv(kv: Optional[Any]) -> ResilientKV:
    if isinstance(kv, ResilientKV):
        return kv
    if kv is None:
        return ResilientKV()

    adapter = getattr(kv, _ADAPTER_ATTRIBUTE, None)
    if isinstance(adapter, ResilientKV):
        return adapter

    key = id(kv)
    if key in _ADAPTERS:
        return _ADAPTERS[key]

    adapter = ResilientKV(kv)
    try:
        setattr(kv, _ADAPTER_ATTRIBUTE, adapter)
    except Exception:  # pragma: no cover - attribute assignment might fail
        _ADAPTERS[key] = adapter
    return adapter
