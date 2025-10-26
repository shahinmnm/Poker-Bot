# Lobby Edge Case Monitoring

This document captures known race conditions and reliability considerations for the lobby flow. Each item notes the triggering scenario, the current mitigation in place, and any residual risk to keep in mind during future development.

## Potential Edge Cases

### Wallet Creation Race Condition
- **Scenario:** Two users tap **Sit** at the same moment.
- **Current Behavior:** `register_player()` offloads the synchronous wallet creation logic to `asyncio.to_thread`.
- **Mitigation:** Redis `SET` operations remain atomic, preventing duplicate wallet state.
- **Residual Risk:** Low.

### Message Deletion Failure
- **Scenario:** The bot loses permission to delete messages between lobby creation and game start.
- **Current Behavior:** `delete_lobby()` catches `TelegramError` exceptions and logs a warning.
- **Mitigation:** Lobby state is still cleared from Redis so games can progress.
- **Residual Risk:** Low.

### Player Leaves During Lobby Update
- **Scenario:** A player chooses **Leave** while the lobby message is being edited.
- **Current Behavior:** `remove_player()` updates the Redis set first and then edits the message.
- **Mitigation:** Redis set operations are atomic, so membership remains consistent.
- **Residual Risk:** Low.

### Stale Member Metadata
- **Scenario:** A user changes their username after joining the lobby.
- **Current Behavior:** The lobby message keeps the original display name captured at join time.
- **Mitigation:** Cosmetic issue only; gameplay is unaffected.
- **Residual Risk:** Low.

## Performance Considerations

### Redis Operations
- Lobby state is stored as compact JSON to minimize payload size.
- Keys are assigned a one-hour TTL (`ex=3600`) to prevent unbounded memory usage.
- All lobby mutations rely on atomic Redis commands to avoid race conditions.

### Telegram API Usage
- An edit-first approach reduces outbound message volume and prevents chat spam.
- Lobby deletion handles permission errors gracefully while still cleaning up Redis state.
- `get_chat_member` is invoked once per player during game start to obtain the latest profile data.

## Documentation Status

### User-Facing
- The help command describes the lobby flow and available commands.
- Button labels communicate their effects without additional explanation.

### Developer-Facing
- Public coroutine entry points provide docstrings and type hints for easier maintenance.
- In-line comments document the Redis key schema and payload contracts.

Keep this document updated whenever new lobby race conditions or mitigation strategies are introduced so the operational playbook stays current.
