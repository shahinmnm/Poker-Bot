import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pokerapp.i18n import TranslationManager
from pokerapp.kvstore import InMemoryKV
from pokerapp.middleware import PokerBotMiddleware


TRANSLATIONS_DIR = Path(__file__).resolve().parent.parent / "translations"


def _build_middleware() -> tuple[PokerBotMiddleware, InMemoryKV]:
    """Create a middleware instance wired with lightweight fakes."""

    kv_store = InMemoryKV()

    translation = TranslationManager(translations_dir=str(TRANSLATIONS_DIR))
    translation.attach_kvstore(kv_store)

    model = SimpleNamespace(
        has_pending_invite=AsyncMock(return_value=False),
        get_user_private_game=AsyncMock(return_value=None),
        get_active_group_game=AsyncMock(return_value=None),
    )

    middleware = PokerBotMiddleware(
        model=model,
        store=kv_store,
        translation_manager_module=translation,
    )

    return middleware, kv_store


def test_build_menu_context_prefers_stored_user_language() -> None:
    """Stored language preferences should override Telegram defaults."""

    middleware, kv_store = _build_middleware()

    user_id = 123
    kv_store.set_user_language(user_id, "es")

    context = asyncio.run(
        middleware.build_menu_context(
            chat_id=555,
            chat_type="private",
            user_id=user_id,
            language_code="en",
        )
    )

    assert context.language_code == "es"
