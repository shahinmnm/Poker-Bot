# Python-Telegram-Bot v21 Migration Roadmap

## Phase 1 – Foundation Upgrade
- Update dependency pins to `python-telegram-bot` 21.x and verify runtime prerequisites.
- Replace legacy `Updater`/`Dispatcher` bootstrapping with the asynchronous `Application` API.
- Ensure bot configuration and Redis connectivity keep working after the bootstrapping change.

## Phase 2 – Async Core Refactor
- Convert controller callbacks and model logic to `async def`, propagating awaitables through the call stack.
- Replace blocking utilities (custom message queue, `threading.Timer`) with asyncio-native constructs or PTB helpers.
- Introduce an explicit help command that reuses the descriptive assets and works in both private and group chats.

## Phase 3 – View Layer Modernisation
- Adapt the view to await bot API methods, ensuring media helpers open files safely and return PTB `Message` objects.
- Centralise flood-control to PTB's built-in rate limiter instead of custom threading machinery.
- Validate that inline keyboards, reply markups, and media helpers still render as expected.

## Phase 4 – Experience Polish & QA
- Add bonus delivery feedback via async scheduling to keep dice animations smooth.
- Smoke-test key poker flows (`/ready`, `/start`, betting actions) under the async engine.
- Document migration notes and follow-up ideas for future automation or feature work.

## Migration Complete ✅

All tasks (1-9) have been successfully implemented:

1. ✅ Infrastructure setup (LiveMessageManager)
2. ✅ Image removal (emoji-based cards)
3. ✅ Integration with game engine
4. ✅ Game state rendering
5. ✅ Community card integration
6. ✅ Action logging and activity feed
7. ✅ UI/UX redesign (plain text + emojis)
8. ✅ Action button redesign (2-column layout)
9. ✅ Legacy code removal

### Key Changes:
- **Single Message:** One live message per game replaces multiple card/state messages
- **No Images:** Cards displayed as text emojis (e.g., 🂡 🃁)
- **Plain Text Only:** No HTML/Markdown for Persian font compatibility
- **Modern Buttons:** 2-column action button layout
- **Activity Feed:** Last 5 actions displayed in live message

### Removed Components:
- `send_or_update_table_cards()` method
- `send_desk_cards_img()` method
- `_legacy_table_messages` cache
- Image generation dependencies
- HTML/ParseMode imports (where unused)
