import logging

from pokerapp.private_game import DEFAULT_WALLET_BALANCE, PrivateGameModel


class _KVWithSetNX:
    def __init__(self):
        self.values = {}
        self.calls = 0

    def setnx(self, key, value):
        self.calls += 1
        if key in self.values:
            return False
        self.values[key] = value
        return True


class _LegacyKV:
    def __init__(self):
        self.values = {}
        self.set_calls = 0

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, **_):
        self.values[key] = value
        self.set_calls += 1
        return True


def test_ensure_wallet_prefers_setnx_when_available():
    kv = _KVWithSetNX()
    model = PrivateGameModel(kv, logging.getLogger("test"))

    model._ensure_wallet(1)
    assert kv.values["pokerbot:1"] == DEFAULT_WALLET_BALANCE

    kv.values["pokerbot:1"] = 42
    model._ensure_wallet(1)

    assert kv.values["pokerbot:1"] == 42
    assert kv.calls == 2


def test_ensure_wallet_falls_back_to_set_when_setnx_missing():
    kv = _LegacyKV()
    model = PrivateGameModel(kv, logging.getLogger("test"))
    model._kv = kv

    model._ensure_wallet(2)

    assert kv.values["pokerbot:2"] == DEFAULT_WALLET_BALANCE
    assert kv.set_calls == 1

    model._ensure_wallet(2)
    assert kv.values["pokerbot:2"] == DEFAULT_WALLET_BALANCE
    assert kv.set_calls == 1
