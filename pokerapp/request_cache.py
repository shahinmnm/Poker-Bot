#!/usr/bin/env python3

"""Request-scoped cache for expensive lookups.

This module provides a lightweight caching layer that lives only for the
duration of a single request/handler execution. It prevents redundant Redis
reads without risking stale data.
"""

import logging
from typing import Dict, Optional, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class RequestCache:
    """Per-request cache for expensive operations."""

    def __init__(self):
        self._wallets: Dict[int, Any] = {}
        self._games: Dict[str, Any] = {}
        self._usernames: Dict[int, str] = {}
        self._custom: Dict[str, Any] = {}
        self._hit_count = 0
        self._miss_count = 0

    async def get_wallet(self, user_id: int, kv, logger_instance=None):
        if user_id in self._wallets:
            self._hit_count += 1
            return self._wallets[user_id]

        self._miss_count += 1

        from pokerapp.pokerbotmodel import WalletManagerModel

        wallet = await WalletManagerModel.load(
            user_id,
            kv,
            logger_instance or logger,
        )

        self._wallets[user_id] = wallet
        return wallet

    def cache_wallet(self, user_id: int, wallet) -> None:
        self._wallets[user_id] = wallet

    def get_username(self, user_id: int) -> Optional[str]:
        if user_id in self._usernames:
            self._hit_count += 1
            return self._usernames[user_id]

        self._miss_count += 1
        return None

    def cache_username(self, user_id: int, username: str) -> None:
        self._usernames[user_id] = username

    def get_game(self, game_id: str) -> Optional[Any]:
        if game_id in self._games:
            self._hit_count += 1
            return self._games[game_id]

        self._miss_count += 1
        return None

    def cache_game(self, game_id: str, game: Any) -> None:
        """Cache a mutable game object for the lifetime of the request.

        NOTE: Game objects are mutable. This cache assumes modifications
        during a single request are intentional and won't cause conflicts.
        Do not share cached games across concurrent requests.
        """
        self._games[game_id] = game

    def get_custom(self, key: str) -> Optional[Any]:
        if key in self._custom:
            self._hit_count += 1
            return self._custom[key]

        self._miss_count += 1
        return None

    def cache_custom(self, key: str, value: Any) -> None:
        self._custom[key] = value

    def get_stats(self) -> Dict[str, int]:
        total = self._hit_count + self._miss_count
        hit_rate = (self._hit_count / total * 100) if total > 0 else 0

        return {
            "hits": self._hit_count,
            "misses": self._miss_count,
            "total": total,
            "hit_rate_pct": round(hit_rate, 1),
            "wallets_cached": len(self._wallets),
            "usernames_cached": len(self._usernames),
            "games_cached": len(self._games),
        }

    def log_stats(self, prefix: str = "RequestCache") -> None:
        stats = self.get_stats()

        if stats["total"] > 0:
            logger.debug(
                "%s: %d hits, %d misses (%.1f%% hit rate) - "
                "%d wallets, %d usernames, %d games cached",
                prefix,
                stats["hits"],
                stats["misses"],
                stats["hit_rate_pct"],
                stats["wallets_cached"],
                stats["usernames_cached"],
                stats["games_cached"],
            )

    def clear(self) -> None:
        self._wallets.clear()
        self._games.clear()
        self._usernames.clear()
        self._custom.clear()
        self._hit_count = 0
        self._miss_count = 0


@contextmanager
def request_cache_context():
    cache = RequestCache()
    try:
        yield cache
    finally:
        cache.log_stats()
