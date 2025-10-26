"""Tests for the resilient key-value store wrapper."""

import redis

from pokerapp.kvstore import ResilientKV


class _FailingSetNXBackend:
    """Backend that simulates a network failure on ``setnx``."""

    def __init__(self) -> None:
        self.calls = 0

    def setnx(self, key, value):  # pragma: no cover - exercised via ResilientKV
        self.calls += 1
        raise redis.exceptions.ConnectionError("network down")


class _FailingSetBackend:
    """Backend that simulates a failure on ``set`` (and any subsequent calls)."""

    def set(self, key, value, **kwargs):  # pragma: no cover - exercised via ResilientKV
        raise redis.exceptions.TimeoutError("timed out")

    def get(self, key):  # pragma: no cover - exercised via ResilientKV
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
