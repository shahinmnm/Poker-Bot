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
