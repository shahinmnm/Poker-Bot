"""
Render result caching to minimize redundant UI layout generation.

Caches serialized HUD text and inline keyboard structures based on
deterministic game state signatures.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from pokerapp.entities import Game, Player


@dataclass
class RenderResult:
    """Cached rendering output."""

    hud_text: str
    keyboard_layout: Optional[List[List[Dict[str, str]]]]
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for Redis storage."""
        return {
            "hud_text": self.hud_text,
            "keyboard_layout": self.keyboard_layout,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RenderResult":
        """Deserialize from Redis."""
        return cls(
            hud_text=data["hud_text"],
            keyboard_layout=data.get("keyboard_layout"),
            timestamp=data["timestamp"],
        )


class RenderCache:
    """Cache manager for UI rendering results."""

    CACHE_TTL_SECONDS = 5

    def __init__(self, kv_client, logger) -> None:
        """Initialize render cache."""
        self._kv = kv_client
        self._logger = logger
        self._hits = 0
        self._misses = 0
        self._keys_by_game: Dict[str, Set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_state_signature(self, game: Game, current_player: Optional[Player]) -> str:
        """Generate deterministic hash of renderable game state."""
        components: List[str] = [
            getattr(game.state, "name", str(getattr(game, "state", ""))),
            str(getattr(current_player, "user_id", "none")),
            str(getattr(game, "pot", 0)),
            str(getattr(game, "max_round_rate", 0)),
            ",".join(str(card) for card in getattr(game, "cards_table", []) or []),
        ]

        for player in getattr(game, "players", []) or []:
            components.append(
                ":".join(
                    [
                        str(getattr(player, "user_id", "")),
                        getattr(getattr(player, "state", None), "name", ""),
                        str(getattr(getattr(player, "wallet", None), "value", lambda: 0)()),
                        str(getattr(player, "round_rate", 0)),
                    ]
                )
            )

        signature_str = "|".join(components)
        return hashlib.sha256(signature_str.encode()).hexdigest()[:16]

    def _build_cache_key(self, game_id: Any, signature: str, variant: str) -> str:
        return f"render:{variant}:{game_id}:{signature}"

    def _load_entry(self, cache_key: str) -> Optional[RenderResult]:
        try:
            cached_json = self._kv.get(cache_key)
            if cached_json is None:
                return None

            if isinstance(cached_json, bytes):
                cached_json = cached_json.decode("utf-8")

            data = json.loads(cached_json)
            return RenderResult.from_dict(data)
        except Exception as exc:  # pragma: no cover - defensive logging
            if self._logger:
                self._logger.warning("Failed to load render cache entry %s: %s", cache_key, exc)
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_cached_render(
        self,
        game: Game,
        current_player: Optional[Player],
        *,
        variant: str = "default",
    ) -> Optional[RenderResult]:
        """Retrieve cached render result if available."""
        signature = self._compute_state_signature(game, current_player)
        cache_key = self._build_cache_key(getattr(game, "id", ""), signature, variant)

        result = self._load_entry(cache_key)
        if result is None:
            self._misses += 1
            return None

        self._hits += 1
        if self._logger:
            self._logger.debug(
                "🎯 Render cache HIT for game %s (sig=%s)",
                getattr(game, "id", "?"),
                signature[:8],
            )
        return result

    def cache_render_result(
        self,
        game: Game,
        current_player: Optional[Player],
        *,
        hud_text: Optional[str] = None,
        keyboard_layout: Optional[List[List[Dict[str, str]]]] = None,
        variant: str = "default",
    ) -> None:
        """Store rendered output for future reuse."""
        if hud_text is None and keyboard_layout is None:
            return

        signature = self._compute_state_signature(game, current_player)
        game_id = getattr(game, "id", "")
        cache_key = self._build_cache_key(game_id, signature, variant)

        existing = self._load_entry(cache_key)

        if existing is not None:
            hud_text = hud_text if hud_text is not None else existing.hud_text
            keyboard_layout = (
                keyboard_layout if keyboard_layout is not None else existing.keyboard_layout
            )

        result = RenderResult(
            hud_text=hud_text or "",
            keyboard_layout=keyboard_layout,
            timestamp=time.time(),
        )

        try:
            self._kv.set(cache_key, json.dumps(result.to_dict()), ex=self.CACHE_TTL_SECONDS)
            self._keys_by_game[str(game_id)].add(cache_key)
            if self._logger:
                self._logger.debug(
                    "💾 Cached render result for game %s (sig=%s, ttl=%ds)",
                    game_id,
                    signature[:8],
                    self.CACHE_TTL_SECONDS,
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            if self._logger:
                self._logger.warning("Failed to cache render result: %s", exc)

    def invalidate_game(self, game_id: Any) -> None:
        """Remove cached entries associated with a specific game."""
        key = str(game_id)
        cache_keys = self._keys_by_game.pop(key, set())
        for cache_key in cache_keys:
            try:
                self._kv.delete(cache_key)
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    def get_stats(self) -> Dict[str, Any]:
        """Get cache performance metrics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100.0) if total else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": hit_rate,
        }
