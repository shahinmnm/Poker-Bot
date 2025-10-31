"""Tests for the resilient key-value store wrapper."""

import redis

from pokerapp.kvstore import ResilientKV


class _FailingSetNXBackend:
    """Backend that simulates a network failure on ``setnx``."""

    def __init__(self) -> None:
        self.calls = 0

    # pragma: no cover - exercised via ResilientKV
    def setnx(self, key, value):  # pragma: no cover
        self.calls += 1
        raise redis.exceptions.ConnectionError("network down")


class _FailingSetBackend:
    """Backend that simulates failures on ``set`` and subsequent calls."""

    # pragma: no cover - exercised via ResilientKV
    def set(self, key, value, **kwargs):  # pragma: no cover
        raise redis.exceptions.TimeoutError("timed out")

    def get(self, key):  # pragma: no cover
        raise AssertionError("backend should not be used after failure")


def test_setnx_network_error_falls_back_to_memory_store():
    backend = _FailingSetNXBackend()
    store = ResilientKV(backend)

    result = store.setnx("foo", "bar")

    assert result is True
    assert backend.calls == 1
    assert store._backend is None
    assert store.get("foo") == b"bar"


def test_set_get_network_error_uses_fallback_memory_store():
    store = ResilientKV(_FailingSetBackend())

    assert store.set("foo", "bar") is True
    assert store._backend is None
    assert store.get("foo") == b"bar"


def test_chat_language_round_trip():
    store = ResilientKV(None)

    store.set_chat_language(12345, "es")

    assert store.get_chat_language(12345) == "es"


def test_user_language_round_trip():
    store = ResilientKV(None)

    store.set_user_language(42, "de")

    assert store.get_user_language(42) == "de"
