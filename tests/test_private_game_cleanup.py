# Tests for the private game lobby cleanup helper (bug fix from Task 10 review)
"""
Tests for PokerBotModel.delete_private_game_lobby() helper.

Validates:
- Primary lobby key deletion (private_game:{code})
- User mapping cleanup (user:{id}:private_game)
- Pending invite cleanup (user:{id}:pending_invites)
- Idempotent behavior (safe to call multiple times)
- Error resilience (partial cleanup doesn't abort)
- Player ID discovery from JSON snapshot
"""

import json
import unittest
from collections import defaultdict

from unittest.mock import AsyncMock, MagicMock
from telegram import Bot

from pokerapp.pokerbotmodel import PokerBotModel
from pokerapp.kvstore import InMemoryKV
from pokerapp.config import Config


class PrivateGameCleanupTests(unittest.IsolatedAsyncioTestCase):
    """Tests for PokerBotModel.delete_private_game_lobby()."""

    def setUp(self) -> None:
        self.kv_store = InMemoryKV()
        set_store = defaultdict(set)

        def sadd(key, value):
            set_store[key].add(value)
            return 1

        def smembers(key):
            return set_store.get(key, set()).copy()

        def srem(key, value):
            if value in set_store.get(key, set()):
                set_store[key].discard(value)
                return 1
            return 0

        self.kv_store.sadd = sadd  # type: ignore[attr-defined]
        self.kv_store.smembers = smembers  # type: ignore[attr-defined]
        self.kv_store.srem = srem  # type: ignore[attr-defined]

        self.mock_bot = AsyncMock(spec=Bot)
        self.mock_bot.send_message = AsyncMock()

        self.mock_view = MagicMock()
        self.mock_view.send_message = AsyncMock()
        self.mock_view.send_message_reply = AsyncMock()

        cfg = Config()
        application = MagicMock()
        application.chat_data = {}

        self.poker_model = PokerBotModel(
            view=self.mock_view,
            bot=self.mock_bot,
            cfg=cfg,
            kv=self.kv_store,
            application=application,
        )

# ============================================================================
# BASIC CLEANUP TESTS
# ============================================================================

    async def test_delete_lobby_removes_primary_key(self):
        """Verify primary lobby key is deleted."""

        game_code = "ABC123"
        chat_id = -100

        # Create lobby snapshot
        lobby_data = {
            "game_code": game_code,
            "chat_id": chat_id,
            "players": [100, 200],
            "state": "lobby",
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        # Delete lobby
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Verify key deleted
        self.assertFalse(self.kv_store.exists("private_game:" + game_code))

    async def test_delete_lobby_removes_user_mappings(self):
        """Verify user:{id}:private_game keys are cleaned up."""

        game_code = "XYZ789"
        chat_id = -200
        player_ids = [100, 200, 300]

        # Create lobby with player mappings
        lobby_data = {
            "game_code": game_code,
            "chat_id": chat_id,
            "players": player_ids,
            "state": "lobby",
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        # Create user mappings
        for pid in player_ids:
            self.kv_store.set("user:" + str(pid) + ":private_game", game_code)

        # Delete lobby
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Verify all user mappings deleted
        for pid in player_ids:
            self.assertFalse(
                self.kv_store.exists("user:" + str(pid) + ":private_game")
            )

    async def test_delete_lobby_clears_pending_invites(self):
        """Verify pending invites are removed from user sets."""

        game_code = "INV456"
        chat_id = -300
        player_id = 100

        # Create lobby
        lobby_data = {
            "game_code": game_code,
            "chat_id": chat_id,
            "players": [player_id],
            "state": "lobby",
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        # Add to pending invites (using Redis set)
        pending_key = "user:" + str(player_id) + ":pending_invites"
        self.kv_store.sadd(pending_key, game_code)

        # Delete lobby
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Verify invite removed
        members = self.kv_store.smembers(pending_key)
        if isinstance(members, set):
            decoded_members = {
                m.decode("utf-8") if isinstance(m, bytes) else m
                for m in members
            }
            self.assertNotIn(game_code, decoded_members)

# ============================================================================
# PLAYER ID DISCOVERY TESTS
# ============================================================================

    async def test_discovers_players_from_json_snapshot(self):
        """Verify player IDs are extracted from persisted lobby JSON."""

        game_code = "DISC01"
        chat_id = -400

        # Create lobby with players in JSON
        lobby_data = {
            "game_code": game_code,
            "chat_id": chat_id,
            "players": [111, 222, 333],
            "state": "lobby",
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        # Create user mappings for all players
        for pid in [111, 222, 333]:
            self.kv_store.set("user:" + str(pid) + ":private_game", game_code)

        # Delete lobby
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Verify all mappings cleaned (proves discovery worked)
        for pid in [111, 222, 333]:
            self.assertFalse(
                self.kv_store.exists("user:" + str(pid) + ":private_game")
            )

    async def test_fallback_to_context_when_json_missing(self):
        """Verify cleanup works even if Redis snapshot is unavailable."""

        game_code = "FALLBK"
        chat_id = -500

        # No lobby JSON exists, but user mapping does
        self.kv_store.set("user:999:private_game", game_code)

        # Delete lobby (should clean up based on context/brute-force)
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Primary key deletion should still work
        self.assertFalse(self.kv_store.exists("private_game:" + game_code))

# ============================================================================
# IDEMPOTENCY & ERROR RESILIENCE
# ============================================================================

    async def test_delete_lobby_is_idempotent(self):
        """Verify calling delete_lobby multiple times is safe."""

        game_code = "IDEMP1"
        chat_id = -600

        # Create minimal lobby
        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps({"game_code": game_code, "players": []}),
        )

        # Delete twice
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Should not raise, key stays deleted
        self.assertFalse(self.kv_store.exists("private_game:" + game_code))

    async def test_delete_lobby_handles_missing_lobby_gracefully(self):
        """Verify deleting non-existent lobby doesn't error."""

        # Should not raise
        await self.poker_model.delete_private_game_lobby(-999, "NOEXIST")

    async def test_partial_cleanup_on_redis_error(self):
        """Verify cleanup continues even if some operations fail."""

        game_code = "PARTIAL"
        chat_id = -700

        # Create lobby
        lobby_data = {
            "game_code": game_code,
            "players": [100, 200],
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        self.kv_store.set("user:100:private_game", game_code)
        self.kv_store.set("user:200:private_game", game_code)

        # Mock one delete to fail
        original_delete = self.kv_store.delete

        def failing_delete(key):
            if "user:100" in key:
                raise Exception("Network error")
            return original_delete(key)

        self.kv_store.delete = failing_delete

        # Should not abort entire cleanup
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Primary key still deleted
        self.assertFalse(self.kv_store.exists("private_game:" + game_code))

        # Other user key still cleaned
        self.assertFalse(self.kv_store.exists("user:200:private_game"))

# ============================================================================
# JSON PARSING EDGE CASES
# ============================================================================

    async def test_handles_malformed_json_snapshot(self):
        """Verify cleanup works even if JSON is corrupted."""

        game_code = "BADJSON"
        chat_id = -800

        # Store invalid JSON
        self.kv_store.set(
            "private_game:" + game_code,
            b"{ invalid json ]",
        )

        # Should not crash
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Key should still be deleted
        self.assertFalse(self.kv_store.exists("private_game:" + game_code))

    async def test_handles_json_without_players(self):
        """Verify cleanup tolerates missing 'players' key."""

        game_code = "NOPLYR"
        chat_id = -900

        # JSON without players field
        lobby_data = {
            "game_code": game_code,
            "state": "lobby",  # No 'players' key
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        # Should not crash
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        self.assertFalse(self.kv_store.exists("private_game:" + game_code))

# ============================================================================
# BYTES VS STRING HANDLING
# ============================================================================

    async def test_handles_bytes_game_code(self):
        """Verify cleanup works when game_code is bytes."""

        game_code_str = "BYTES1"
        game_code_bytes = b"BYTES1"
        chat_id = -1000

        # Store with string key
        self.kv_store.set(
            "private_game:" + game_code_str,
            json.dumps({"game_code": game_code_str, "players": [100]}),
        )

        self.kv_store.set("user:100:private_game", game_code_str)

        # Call with bytes
        await self.poker_model.delete_private_game_lobby(
            chat_id, game_code_bytes
        )

        # Should still clean up
        self.assertFalse(self.kv_store.exists("private_game:" + game_code_str))
        self.assertFalse(self.kv_store.exists("user:100:private_game"))

# ============================================================================
# INTEGRATION WITH PRIVATE GAME FLOW
# ============================================================================

    async def test_cleanup_callable(self):
        """Verify the cleanup helper is exposed for use in flows."""

        self.assertTrue(hasattr(self.poker_model, "delete_private_game_lobby"))
        self.assertTrue(callable(self.poker_model.delete_private_game_lobby))

# ============================================================================
# PENDING INVITES WITH srem
# ============================================================================

    async def test_pending_invites_cleared_with_srem(self):
        """Verify srem is used to clear pending invites if available."""

        game_code = "SREM01"
        chat_id = -1100
        player_id = 500

        # Create lobby
        lobby_data = {
            "game_code": game_code,
            "players": [player_id],
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        # Add to pending set
        pending_key = "user:" + str(player_id) + ":pending_invites"
        self.kv_store.sadd(pending_key, game_code)
        self.kv_store.sadd(pending_key, "OTHER_CODE")  # Keep this one

        # Delete lobby
        await self.poker_model.delete_private_game_lobby(chat_id, game_code)

        # Verify only this game removed
        remaining = self.kv_store.smembers(pending_key)
        if isinstance(remaining, set):
            remaining_decoded = {
                m.decode("utf-8") if isinstance(m, bytes) else m
                for m in remaining
            }
            self.assertNotIn(game_code, remaining_decoded)
            self.assertIn("OTHER_CODE", remaining_decoded)

# ============================================================================
# LOGGING VERIFICATION
# ============================================================================

    async def test_cleanup_logs_deleted_keys(self):
        """Verify cleanup operation is logged for debugging."""

        game_code = "LOG001"
        chat_id = -1200

        lobby_data = {
            "game_code": game_code,
            "players": [100],
        }

        self.kv_store.set(
            "private_game:" + game_code,
            json.dumps(lobby_data),
        )

        with self.assertLogs("pokerapp.pokerbotmodel", level="INFO"):
            await self.poker_model.delete_private_game_lobby(
                chat_id, game_code
            )

# ============================================================================
# RUN TESTS
# ============================================================================


if __name__ == "__main__":
    unittest.main()
