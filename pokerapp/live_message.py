#!/usr/bin/env python3
"""Live message helper utilities for the in-chat game view."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import hashlib
import html
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from pokerapp.entities import Game, GameState, Player, PlayerAction, PlayerState
from pokerapp.device_detector import (
    DeviceDetector,
    DeviceProfile,
)
from pokerapp.kvstore import ensure_kv
from pokerapp.render_cache import RenderCache, RenderResult
from pokerapp.i18n import translation_manager
from pokerapp.keyboard_utils import (
    rehydrate_keyboard_layout,
    serialise_keyboard_layout,
)


NUMBER_NORMALIZATION_TABLE = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_numbers(text: str) -> str:
    """Convert Eastern Arabic and Persian digits to ASCII numerals."""

    if not text:
        return text

    return text.translate(NUMBER_NORMALIZATION_TABLE)


class UnicodeTextFormatter:
    """Format text using Unicode characters and emojis - no HTML/Markdown."""

    BOLD_MAP = {
        "A": "𝗔",
        "B": "𝗕",
        "C": "𝗖",
        "D": "𝗗",
        "E": "𝗘",
        "F": "𝗙",
        "G": "𝗚",
        "H": "𝗛",
        "I": "𝗜",
        "J": "𝗝",
        "K": "𝗞",
        "L": "𝗟",
        "M": "𝗠",
        "N": "𝗡",
        "O": "𝗢",
        "P": "𝗣",
        "Q": "𝗤",
        "R": "𝗥",
        "S": "𝗦",
        "T": "𝗧",
        "U": "𝗨",
        "V": "𝗩",
        "W": "𝗪",
        "X": "𝗫",
        "Y": "𝗬",
        "Z": "𝗭",
        "a": "𝗮",
        "b": "𝗯",
        "c": "𝗰",
        "d": "𝗱",
        "e": "𝗲",
        "f": "𝗳",
        "g": "𝗴",
        "h": "𝗵",
        "i": "𝗶",
        "j": "𝗷",
        "k": "𝗸",
        "l": "𝗹",
        "m": "𝗺",
        "n": "𝗻",
        "o": "𝗼",
        "p": "𝗽",
        "q": "𝗾",
        "r": "𝗿",
        "s": "𝘀",
        "t": "𝘁",
        "u": "𝘂",
        "v": "𝘃",
        "w": "𝘄",
        "x": "𝘅",
        "y": "𝘆",
        "z": "𝘇",
        "0": "𝟬",
        "1": "𝟭",
        "2": "𝟮",
        "3": "𝟯",
        "4": "𝟰",
        "5": "𝟱",
        "6": "𝟲",
        "7": "𝟳",
        "8": "𝟴",
        "9": "𝟵",
    }

    PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

    @staticmethod
    def make_bold(text: str) -> str:
        """Convert text to Unicode bold characters."""

        return "".join(UnicodeTextFormatter.BOLD_MAP.get(c, c) for c in text)

    @staticmethod
    def strip_all_html(text: str) -> str:
        """Remove ALL HTML tags and convert to plain text with Unicode styling."""

        import re

        text = re.sub(
            r"<b>(.*?)</b>",
            lambda m: UnicodeTextFormatter.make_bold(m.group(1)),
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r"<i>(.*?)</i>",
            lambda m: f"{m.group(1)}",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(r"<code>(.*?)</code>", r"\1", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<pre>(.*?)</pre>", r"\1", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        return text

    @staticmethod
    def localize_digits(text: str, language_code: str) -> str:
        """Convert Western digits to localized digits based on language."""

        if language_code == "fa":
            return text.translate(UnicodeTextFormatter.PERSIAN_DIGITS)
        if language_code == "ar":
            return text.translate(UnicodeTextFormatter.ARABIC_DIGITS)
        return text


@dataclass(slots=True)
class RaiseOptionMeta:
    """Metadata describing a single raise selection option."""

    key: str
    button_label: str
    preview_label: str
    amount: Optional[int]
    kind: str  # "amount", "pot", "all_in"


@dataclass(slots=True)
class ChatRenderState:
    """Mutable rendering data tracked per chat for diffing & UX features."""

    last_context: Dict[str, Any] = field(default_factory=dict)
    last_payload_hash: Optional[str] = None
    last_content_hash: Optional[str] = None
    last_keyboard_json: str = ""
    pending_task: Optional[asyncio.Task] = None
    last_actor_user_id: Optional[int] = None
    raise_options: Dict[str, RaiseOptionMeta] = field(default_factory=dict)
    raise_order: List[str] = field(default_factory=list)
    raise_selections: Dict[int, Optional[str]] = field(default_factory=dict)
    device_profile: Optional[DeviceProfile] = None
    last_game_snapshot: Optional[dict] = None
    last_update_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    latency_samples: int = 0
    network_quality: str = "unknown"


@dataclass(slots=True)
class RenderBundle:
    """Container describing a prepared message payload."""

    message_text: str
    stable_text: str
    reply_markup: Optional[InlineKeyboardMarkup]
    keyboard_json: str
    payload_hash: str
    banner: Optional[str]
    context: Dict[str, Any]
    raise_options: Dict[str, RaiseOptionMeta]
    raise_order: List[str]


class LiveMessageManager:
    """Manage the single live game message shown in group chats."""

    # Stage emojis for card reveals
    STAGE_NAME_KEYS = {
        0: "game.round.pre_flop",
        3: "game.round.flop",
        4: "game.round.turn",
        5: "game.round.river",
    }

    STAGE_ICONS = {
        0: "🎴",
        3: "🃏",
        4: "🃏",
        5: "🃏",
    }
    # Minimum spacing between consecutive updates per chat (seconds)
    DEBOUNCE_WINDOW = 0.0
    # BANNER_DURATION removed - system deleted
    # Duration before deleting the private "your turn" ping
    TURN_PING_TTL = 5
    # Approximate per-turn timer in seconds (aligned with model default)
    DEFAULT_TURN_SECONDS = 120

    STATE_CONTEXT_KEYS: Tuple[str, ...] = (
        "table_code",
        "seat_label",
        "stage_name",
        "stage_icon",
        "last_bet_value",
        "timer_label",
        "actor_user_id",
        "recent_actions",
    )


    def __init__(
        self,
        bot,
        logger,
        *,
        kv: Optional[Any] = None,
        render_cache: Optional[RenderCache] = None,
    ):
        self._bot = bot
        self._logger = logger
        self._chat_locks: Dict[str, asyncio.Lock] = {}
        self._last_update_at: Dict[str, float] = {}
        self._chat_states: Dict[str, ChatRenderState] = {}
        # Track hashes of rendered content to detect redundant updates
        self._content_hashes: Dict[int, str] = {}
        self._kv = ensure_kv(kv) if kv is not None else None
        cache_backend = ensure_kv(kv) if kv is not None else ensure_kv(None)
        self._render_cache = render_cache or RenderCache(cache_backend, logger)
        self._device_detector = DeviceDetector()
        self._language_code = "en"
        self._language_direction = "ltr"
        self._language_font = "system"

    def _prepare_plain_text(self, text: str) -> str:
        """Convert any formatted text to plain Unicode text for all languages."""

        if not text:
            return ""

        clean_text = UnicodeTextFormatter.strip_all_html(text)
        clean_text = normalize_numbers(clean_text)

        if self._language_code in ("fa", "ar", "he", "ur"):
            clean_text = f"\u200F{clean_text}\u200E"

        return clean_text

    def set_language_metadata(self, *, code: str, direction: str, font: str) -> None:
        """Update active language metadata for renders."""

        self._language_code = code
        self._language_direction = direction
        self._language_font = font

    def _get_stage_name(self, card_count: int) -> str:
        """Return localized stage name for the given number of community cards."""

        key = self.STAGE_NAME_KEYS.get(card_count)
        if key:
            return translation_manager.t(key, lang=self._language_code)

        return translation_manager.t("game.state.initial", lang=self._language_code)

    def _apply_direction(self, text: Optional[str]) -> Optional[str]:
        if not text or self._language_direction != "rtl":
            return text
        if text.startswith("\u202B") and text.endswith("\u202C"):
            return text
        return f"\u202B{text}\u202C"

    @staticmethod
    def _sanitize_text(value: Any, *, default: str = "") -> str:
        """Return plain-text representation with all markup removed."""

        if value is None:
            value = default
        return UnicodeTextFormatter.strip_all_html(str(value))

    @staticmethod
    def _format_chips(amount: int, width: int = 6) -> str:
        """Format chip amounts with right-aligned monospace layout.

        Args:
            amount: Chip value to format
            width: Total character width (default 6 handles up to 99,999)

        Returns:
            Formatted string like "$ 4,250" or "$ 875" or "$ 50"

        Examples:
            _format_chips(4250) -> "$ 4,250"
            _format_chips(875) -> "$ 875"
            _format_chips(50) -> "$ 50"
        """

        formatted = f"{amount:,}"
        return f"$ {formatted:>{width - 2}}"

    @staticmethod
    def _format_mobile_button_label(
        emoji: str,
        text: str,
        *,
        emoji_scale: float = 1.5,
    ) -> str:
        if emoji_scale > 1.0:
            return f"{emoji}\u200A {text}"

        return f"{emoji} {text}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_or_update_live_message(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Public wrapper maintaining backwards compatibility."""

        game_identifier = getattr(game, "game_id", getattr(game, "id", "?"))
        self._logger.info(
            "🔍 LiveMessageManager.send_or_update_live_message called - "
            "chat_id=%s, game_id=%s",
            chat_id,
            game_identifier,
        )
        return await self.send_or_update_game_state(
            chat_id=chat_id,
            game=game,
            current_player=current_player,
        )

    async def send_or_update_game_state(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
    ) -> Optional[int]:
        """Send a new live message or update the existing one."""

        chat_key = str(chat_id)
        state = self._get_state(chat_key)
        last_snapshot_state = None
        if state.last_game_snapshot:
            last_snapshot_state = state.last_game_snapshot.get("state")
        finished_snapshot = False
        if last_snapshot_state is not None:
            if last_snapshot_state == GameState.FINISHED:
                finished_snapshot = True
            else:
                finished_snapshot = (
                    getattr(last_snapshot_state, "name", "")
                    == GameState.FINISHED.name
                )

        if game.state == GameState.INITIAL or finished_snapshot:
            self._logger.info(
                "🔄 Game reset detected - forcing message recreation | chat_id=%s",
                chat_id,
            )
            game.group_message_id = None
            state.last_game_snapshot = None
            state.last_context = {}
            state.last_payload_hash = None
            state.last_content_hash = None
            state.last_keyboard_json = ""
            self._content_hashes.pop(chat_id, None)
            self._logger.info(
                "🧹 Cleared all overlay/banner state for new game | chat=%s, game=%s",
                chat_id,
                getattr(game, "id", None),
            )
        lock = self._chat_locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_key] = lock

        async with lock:
            pending_snapshot = self._capture_game_snapshot(game)
            pending_snapshot["chat_id"] = chat_id
            skip_debounce = self._should_skip_debounce(
                state.last_game_snapshot,
                pending_snapshot,
            )

            await self._apply_debounce(chat_key, state, skip=skip_debounce)
            loop = asyncio.get_running_loop()
            try:
                return await self._send_or_update_locked(
                    chat_id=chat_id,
                    chat_key=chat_key,
                    game=game,
                    current_player=current_player,
                    state=state,
                    new_snapshot=pending_snapshot,
                )
            finally:
                self._last_update_at[chat_key] = loop.time()

    def get_render_cache_stats(self) -> Dict[str, Any]:
        """Return current cache statistics for diagnostics."""

        if getattr(self, "_render_cache", None) is None:
            return {"hits": 0, "misses": 0, "total": 0, "hit_rate": 0.0}
        return self._render_cache.get_stats()

    def invalidate_render_cache(self, game: Game) -> None:
        """Remove cached render entries for the provided game."""

        if getattr(self, "_render_cache", None) is None:
            return
        self._render_cache.invalidate_game(getattr(game, "id", ""))

    async def present_raise_selector(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
        *,
        user_id: int,
        message_id: int,
        message_version: Optional[int],
        selection_key: Optional[str],
    ) -> bool:
        """Swap the main action keyboard for the raise amount selector."""

        chat_key = str(chat_id)
        state = self._get_state(chat_key)
        lock = self._chat_locks.setdefault(chat_key, asyncio.Lock())

        async with lock:
            version = (
                message_version
                if message_version is not None
                else game.get_live_message_version()
            )
            if selection_key is None:
                selection_key = state.raise_selections.get(user_id)
            device_profile = self._resolve_device_profile(
                chat_id,
                state,
                user_id=getattr(current_player, "user_id", None),
            )
            bundle = self._prepare_render_bundle(
                chat_key=chat_key,
                game=game,
                current_player=current_player,
                state=state,
                version=version,
                mode="raise_selection",
                include_banner=False,
                selected_raise=selection_key,
                device_profile=device_profile,
            )

            if bundle.reply_markup is None:
                self._logger.debug(
                    "Raise selector unavailable - no options",
                )
                return False

            try:
                plain_text = self._prepare_plain_text(bundle.stable_text)
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=plain_text,
                    reply_markup=bundle.reply_markup,
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                self._logger.error(
                    "Failed to present raise selector in chat %s: %s",
                    chat_id,
                    exc,
                )
                return False

            state.last_context = bundle.context
            state.last_payload_hash = bundle.payload_hash
            state.last_keyboard_json = bundle.keyboard_json
            state.raise_options = bundle.raise_options
            state.raise_order = bundle.raise_order
            state.raise_selections[user_id] = selection_key

            return True

    async def restore_action_keyboard(
        self,
        chat_id: int,
        game: Game,
        current_player: Player,
        *,
        message_id: int,
    ) -> bool:
        """Restore the default action keyboard after leaving raise picker."""

        chat_key = str(chat_id)
        state = self._get_state(chat_key)
        lock = self._chat_locks.setdefault(chat_key, asyncio.Lock())

        async with lock:
            device_profile = self._resolve_device_profile(
                chat_id,
                state,
                user_id=getattr(current_player, "user_id", None),
            )
            bundle = self._prepare_render_bundle(
                chat_key=chat_key,
                game=game,
                current_player=current_player,
                state=state,
                version=game.get_live_message_version(),
                mode="actions",
                include_banner=False,
                device_profile=device_profile,
            )

            try:
                plain_text = self._prepare_plain_text(bundle.stable_text)
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=plain_text,
                    reply_markup=bundle.reply_markup,
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                self._logger.error(
                    "Failed to restore action keyboard in chat %s: %s",
                    chat_id,
                    exc,
                )
                return False

            state.last_context = bundle.context
            state.last_payload_hash = bundle.payload_hash
            state.last_keyboard_json = bundle.keyboard_json
            state.raise_options = bundle.raise_options
            state.raise_order = bundle.raise_order
            state.raise_selections.clear()

            return True

    def get_raise_selection(
        self, chat_id: int, user_id: int
    ) -> Tuple[Optional[str], Optional[RaiseOptionMeta]]:
        """Return the raise selection currently stored for a user."""

        state = self._chat_states.get(str(chat_id))
        if state is None:
            return None, None

        key = state.raise_selections.get(user_id)
        if key is None:
            return None, None

        return key, state.raise_options.get(key)

    def clear_raise_selection(self, chat_id: int, user_id: int) -> None:
        """Remove the stored raise selection for a given user."""

        state = self._chat_states.get(str(chat_id))
        if state is None:
            return

        state.raise_selections.pop(user_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self, chat_key: str) -> ChatRenderState:
        state = self._chat_states.get(chat_key)
        if state is None:
            state = ChatRenderState()
            self._chat_states[chat_key] = state
        return state

    def _get_debounce_delay(self, render_state: ChatRenderState) -> float:
        """No debounce delay."""

        return 0.0

    def _resolve_device_profile(
        self,
        chat_id: int,
        state: ChatRenderState,
        *,
        user_id: Optional[int] = None,
    ) -> DeviceProfile:
        """Return the device profile associated with the chat."""

        if state.device_profile is not None:
            return state.device_profile

        chat_type = "private" if chat_id > 0 else "group"
        profile = self._device_detector.detect_device(
            user_id=user_id,
            chat_type=chat_type,
        )
        state.device_profile = profile
        return profile

    async def _apply_debounce(
        self, chat_key: str, state: ChatRenderState, *, skip: bool = False
    ) -> None:
        """Sleep briefly if the last update happened too recently."""

        if skip:
            self._logger.debug(
                "Debounce skipped for chat %s due to high-priority update",
                chat_key,
            )
            return

        window = self._get_debounce_delay(state)
        if window <= 0:
            return

        last_update = self._last_update_at.get(chat_key)
        if last_update is None:
            return

        loop = asyncio.get_running_loop()
        elapsed = loop.time() - last_update
        if elapsed < window:
            await asyncio.sleep(window - elapsed)

    def _should_skip_debounce(
        self,
        previous_snapshot: Optional[dict],
        new_snapshot: dict,
    ) -> bool:
        """Always update immediately."""

        return True

    async def _send_or_update_locked(
        self,
        chat_id: int,
        chat_key: str,
        game: Game,
        current_player: Player,
        state: ChatRenderState,
        *,
        new_snapshot: dict,
    ) -> Optional[int]:
        """Internal helper executing the actual message update."""

        next_version = None
        if current_player is not None:
            next_version = game.next_live_message_version()

        old_snapshot = state.last_game_snapshot
        state_diff = self._calculate_state_diff(old_snapshot, new_snapshot)
        if state_diff.get("type") == "incremental":
            changed_keys = [key for key in state_diff.keys() if key != "type"]
            self._logger.debug(
                "Incremental update detected: %s",
                changed_keys,
            )
        state.last_game_snapshot = new_snapshot

        # Simple board state tracking (no flash effects)
        current_board = {
            str(card) for card in (getattr(game, "cards_table", []) or [])
        }
        # Stored for potential future comparisons (no visual effects)

        # Simple content check (no flash state)
        render_token = self._compute_content_hash(game, current_player)
        previous_token = self._content_hashes.get(chat_id)

        if previous_token == render_token and state.last_payload_hash is not None:
            return (
                game.group_message_id if game.has_group_message() else None
            )

        self._content_hashes[chat_id] = render_token

        device_profile = self._resolve_device_profile(
            chat_id,
            state,
            user_id=getattr(current_player, "user_id", None),
        )

        bundle = self._prepare_render_bundle(
            chat_key=chat_key,
            game=game,
            current_player=current_player,
            state=state,
            version=next_version,
            mode="actions",
            include_banner=True,
            device_profile=device_profile,
        )

        keyboard_component = bundle.keyboard_json or ""
        content_hash = hashlib.sha256(
            bundle.message_text.encode()
            + b"\x1f"
            + keyboard_component.encode()
        ).hexdigest()
        if content_hash == state.last_content_hash:
            self._logger.debug(
                "Skipping identical message update for chat %s", chat_id
            )
            return game.group_message_id if game.has_group_message() else None

        if bundle.payload_hash == state.last_payload_hash:
            self._logger.debug(
                "Message payload unchanged for chat %s; skipping edit",
                chat_id,
            )
            return game.group_message_id if game.has_group_message() else None

        message_id = await self._dispatch_payload(
            chat_id=chat_id,
            game=game,
            bundle=bundle,
            state=state,
        )

        if message_id is None:
            return None

        state.last_context = bundle.context
        state.last_payload_hash = bundle.payload_hash
        state.last_content_hash = content_hash
        state.last_keyboard_json = bundle.keyboard_json
        state.raise_options = bundle.raise_options
        state.raise_order = bundle.raise_order
        state.raise_selections.clear()

        if next_version is not None:
            game.mark_live_message_version(next_version)

        if bundle.banner:
            self._schedule_banner_clear(
                chat_key=chat_key,
                chat_id=chat_id,
                message_id=message_id,
                expected_hash=bundle.payload_hash,
                state=state,
                game=game,
            )

        await self._ping_player_if_needed(
            state,
            bundle.context,
            chat_id=chat_id,
            game=game,
        )

        return message_id

    def _prepare_render_bundle(
        self,
        *,
        chat_key: str,
        game: Game,
        current_player: Optional[Player],
        state: ChatRenderState,
        version: Optional[int],
        mode: str,
        include_banner: bool,
        selected_raise: Optional[str] = None,
        device_profile: DeviceProfile,
    ) -> RenderBundle:
        """Build message text, markup, and hashes for a render pass."""

        context = self._build_render_context(
            game,
            current_player,
        )
        context["language_code"] = self._language_code
        context["layout_direction"] = self._language_direction
        context["font_family"] = self._language_font

        preview_text: Optional[str] = None
        stable_text: Optional[str] = None
        reply_markup: Optional[InlineKeyboardMarkup] = None
        options: List[RaiseOptionMeta] = []
        cache_variant = f"{getattr(device_profile.device_type, 'value', 'default')}:{self._language_code}"
        use_cache = (
            mode == "actions"
            and current_player is not None
            and getattr(self, "_render_cache", None) is not None
        )

        cached_layout: Optional[List[List[Dict[str, str]]]] = None
        if use_cache:
            cached_result: Optional[RenderResult] = self._render_cache.get_cached_render(
                game,
                current_player,
                variant=cache_variant,
            )
            if cached_result is not None:
                stable_text = cached_result.hud_text or None
                cached_layout = cached_result.keyboard_layout

        if mode == "actions":
            if stable_text is None:
                stable_text = self._compose_message_body(
                    game=game,
                    current_player=current_player,
                    context=context,
                    preview_raise=None,
                    compact=False,
                )

            options = self._compute_raise_options(game, current_player)

            if cached_layout:
                reply_markup = rehydrate_keyboard_layout(
                    cached_layout,
                    version=version,
                )
            else:
                reply_markup, options = self._build_action_inline_keyboard(
                    game=game,
                    player=current_player,
                    version=version,
                    use_cache=False,
                    device_profile=device_profile,
                )
        else:
            options = self._compute_raise_options(game, current_player)
            reply_markup = self._build_raise_selection_keyboard(
                game=game,
                player=current_player,
                version=version,
                options=options,
                selected_key=selected_raise,
            )
            preview_text = self._format_raise_preview(
                selected_raise,
                state_options=None,
                options_order=None,
                context_options={opt.key: opt for opt in options},
            )
            stable_text = self._compose_message_body(
                game=game,
                current_player=current_player,
                context=context,
                preview_raise=preview_text,
                compact=False,
            )

        message_bytes = self._calculate_message_bytes(stable_text or "")
        if self._needs_compact_mode(
            message_bytes=message_bytes,
            device_profile=device_profile,
            render_state=state,
        ):
            stable_text = self._compose_message_body(
                game=game,
                current_player=current_player,
                context=context,
                preview_raise=preview_text,
                compact=True,
            )
            message_bytes = self._calculate_message_bytes(stable_text)

        stable_text_value = self._apply_direction(stable_text) or ""

        banner = None
        if include_banner:
            banner = self._select_banner(context, state.last_context)

        banner_text = self._apply_direction(banner) if banner else None
        message_text = (
            f"{banner_text}\n{stable_text_value}" if banner_text else stable_text_value
        )

        if use_cache and current_player is not None:
            layout_to_cache: Optional[List[List[Dict[str, str]]]] = None
            if reply_markup is not None and getattr(reply_markup, "inline_keyboard", None):
                layout_to_cache = serialise_keyboard_layout(
                    reply_markup.inline_keyboard,
                    version=version,
                )
            self._render_cache.cache_render_result(
                game,
                current_player,
                hud_text=stable_text_value,
                keyboard_layout=layout_to_cache,
                variant=cache_variant,
            )

        option_map = {opt.key: opt for opt in options}
        option_order = [opt.key for opt in options]

        keyboard_json = self._serialize_reply_markup(reply_markup)
        payload_hash = self._payload_hash(message_text, keyboard_json)

        diff_context = {
            key: context.get(key)
            for key in self.STATE_CONTEXT_KEYS
        }
        diff_context["language_code"] = self._language_code
        diff_context["layout_direction"] = self._language_direction
        diff_context["font_family"] = self._language_font

        return RenderBundle(
            message_text=message_text,
            stable_text=stable_text_value,
            reply_markup=reply_markup,
            keyboard_json=keyboard_json,
            payload_hash=payload_hash,
            banner=banner,
            context=diff_context,
            raise_options=option_map,
            raise_order=option_order,
        )

    def _build_render_context(
        self,
        game: Game,
        current_player: Optional[Player],
    ) -> Dict[str, Any]:
        players = list(getattr(game, "players", []) or [])
        num_cards = len(game.cards_table or [])
        stage_name = self._get_stage_name(num_cards)
        stage_icon = self.STAGE_ICONS.get(num_cards, "🎴")

        actor_user_id = getattr(current_player, "user_id", None)
        timer_bucket = self._timer_bucket(game)
        timer_label = "—" if timer_bucket is None else f"{timer_bucket}s"

        return {
            "table_code": str(getattr(game, "id", "----"))[:4].upper(),
            "seat_label": f"{len(players)}-max",
            "stage_name": stage_name,
            "stage_icon": stage_icon,
            "last_bet_value": max(getattr(game, "max_round_rate", 0), 0),
            "timer_label": timer_label,
            "actor_user_id": actor_user_id,
            "recent_actions": self._format_recent_actions(game),
            "t_cards_locked": translation_manager.t(
                "viewer.board.cards_locked", lang=self._language_code
            ),
            "t_board_label": translation_manager.t(
                "viewer.table.header", lang=self._language_code
            ),
            "t_pot_label": translation_manager.t(
                "viewer.game.pot", lang=self._language_code
            ),
            "t_bet_label": translation_manager.t(
                "viewer.game.bet", lang=self._language_code
            ),
            "t_waiting": translation_manager.t(
                "viewer.game.waiting_players", lang=self._language_code
            ),
            "t_turn_label": translation_manager.t(
                "viewer.game.turn", lang=self._language_code
            ),
            "t_no_players": translation_manager.t(
                "viewer.lobby.no_players", lang=self._language_code
            ),
            "t_active_count": translation_manager.t(
                "viewer.game.active_players", lang=self._language_code
            ),
            "t_recent_label": translation_manager.t(
                "viewer.game.recent_actions", lang=self._language_code
            ),
            "t_selected_label": translation_manager.t(
                "viewer.game.selected_raise", lang=self._language_code
            ),
        }

    def _compose_message_body(
        self,
        *,
        game: Game,
        current_player: Optional[Player],
        context: Dict[str, Any],
        preview_raise: Optional[str],
        compact: bool,
    ) -> str:
        message = self._build_game_state_text(
            game=game,
            current_player=current_player,
            context=context,
            preview_raise=preview_raise,
            compact=compact,
        )

        if not compact:
            self._logger.info(
                "🎯 Live message built for game_id=%s | turn=%s",
                getattr(game, "id", None),
                getattr(current_player, "user_id", None) if current_player else None,
            )

        return message



    def _build_game_state_text(
        self,
        *,
        game: Game,
        current_player: Optional[Player],
        context: Dict[str, Any],
        preview_raise: Optional[Dict],
        compact: bool = False,
    ) -> str:
        """Build game state message with precise section spacing."""

        def _inline_amount(value: Any) -> str:
            try:
                numeric = int(value)
            except (TypeError, ValueError):
                numeric = 0
            if numeric < 0:
                numeric = 0
            return self._sanitize_text(f"${numeric:,}")

        language_code = context.get("language_code", "en")
        sections: List[str] = []

        # ═══════════════════════════════════════════════════
        # SECTION 1: HEADER (Game ID • Seats • Stage)
        # ═══════════════════════════════════════════════════
        header_parts: List[str] = []

        table_id = context.get("table_code", "")
        if table_id and table_id != "----":
            bold_id = UnicodeTextFormatter.make_bold(f"#{table_id}")
            header_parts.append(bold_id)

        seat_label = context.get("seat_label", "")
        if seat_label and seat_label != "—":
            bold_seat = UnicodeTextFormatter.make_bold(seat_label)
            header_parts.append(bold_seat)

        stage_icon = context.get("stage_icon", "")
        stage_name = context.get("stage_name", "")
        # Remove duplicate emojis from stage text (keep only text after emoji prefix)
        if stage_name and "🃏" in stage_name:
            parts = stage_name.split("🃏", 2)
            if len(parts) > 2:
                stage_name = "🃏 " + parts[-1].strip()
        if stage_name and stage_name != "—":
            stage_text = f"{stage_icon} {stage_name}".strip()
            bold_stage = UnicodeTextFormatter.make_bold(stage_text)
            header_parts.append(bold_stage)

        if header_parts:
            header_line = "🎴 " + " • ".join(header_parts)
            sections.append(header_line)

        # ═══════════════════════════════════════════════════
        # SECTION 2: BOARD CARDS
        # ═══════════════════════════════════════════════════
        board_cards = list(getattr(game, "cards_table", []) or [])
        board_label = translation_manager.t("viewer.board.label", lang=language_code)

        if board_cards:
            card_text = self._format_board_cards(board_cards)
            sections.append(f"🃏 {board_label}: {card_text}")
        else:
            sections.append(f"🃏 {board_label}: 🂠 🂠 🂠")

        # ═══════════════════════════════════════════════════
        # SECTION 3: POT & BET
        # ═══════════════════════════════════════════════════
        pot_amount = max(getattr(game, "pot", 0), 0)
        bet_amount = max(context.get("last_bet_value", 0) or 0, 0)

        pot_label = translation_manager.t("viewer.game.pot", lang=language_code)
        bet_label = translation_manager.t("viewer.game.bet", lang=language_code)

        pot_line = (
            f"💰 {pot_label}: {_inline_amount(pot_amount)} | "
            f"{bet_label}: {_inline_amount(bet_amount)}"
        )
        sections.append(pot_line)

        # ═══════════════════════════════════════════════════
        # SECTION 4: TURN INDICATOR
        # ═══════════════════════════════════════════════════
        if current_player:
            actor_name = self._get_player_name(current_player)
            timer_display = context.get("timer_label", "")

            turn_label = translation_manager.t("viewer.game.turn", lang=language_code)
            turn_line = f"⏰ {turn_label}: {actor_name}"

            if timer_display and timer_display != "—":
                turn_line += f" ({timer_display})"

            sections.append(turn_line)

        # ═══════════════════════════════════════════════════
        # SECTION 5: ACTIVE PLAYER COUNT
        # ═══════════════════════════════════════════════════
        players = list(getattr(game, "players", []) or [])
        active_count = sum(
            1 for p in players if getattr(p, "state", None) not in {PlayerState.FOLD, None}
        )

        active_format = translation_manager.t(
            "viewer.game.active_players",
            lang=language_code
        )
        active_text = active_format.format(active=active_count, total=len(players))
        sections.append(f"👥 {active_text}")

        if not players:
            waiting_message = translation_manager.t(
                "viewer.game.waiting_players",
                lang=language_code
            )
            sections.append(f"⏳ {waiting_message}")

        # ═══════════════════════════════════════════════════
        # SECTION 6: PLAYER LIST (joined with newline, not blank line)
        # ═══════════════════════════════════════════════════
        actor_id = getattr(current_player, "user_id", None) if current_player else None
        player_lines: List[str] = []

        fold_label = translation_manager.t(
            "viewer.game.player_state.fold",
            lang=language_code
        )
        all_in_label = translation_manager.t(
            "viewer.game.player_state.all_in",
            lang=language_code
        )
        waiting_label = translation_manager.t(
            "viewer.game.player_state.waiting",
            lang=language_code
        )

        for player in players:
            name = self._sanitize_text(self._get_player_name(player))
            if len(name) > 20:
                name = name[:19] + "…"

            wallet = getattr(player, "wallet", None)
            stack = 0
            if wallet:
                wallet_val = getattr(wallet, "value", 0)
                stack = max(int(wallet_val() if callable(wallet_val) else wallet_val), 0)

            state = getattr(player, "state", None)

            if state == PlayerState.FOLD:
                icon = "❌"
                status = f" • {fold_label}"
            elif state == PlayerState.ALL_IN:
                icon = "🔥"
                status = f" • {all_in_label}"
            elif actor_id and player.user_id == actor_id:
                icon = "✅"
                status = ""
            else:
                icon = "🚫"
                status = f" • {waiting_label}"

            player_line = f"{icon} {name} • {_inline_amount(stack)}{status}"
            player_lines.append(player_line)

        if player_lines:
            sections.append("\n".join(player_lines))
        else:
            no_players_text = translation_manager.t(
                "viewer.lobby.no_players",
                lang=language_code
            )
            sections.append(f"👥 {no_players_text}")

        # ═══════════════════════════════════════════════════
        # SECTION 7: RECENT ACTIONS HEADER
        # ═══════════════════════════════════════════════════
        recent = context.get("recent_actions", [])
        if recent:
            header_text = translation_manager.t(
                "viewer.game.recent_actions",
                lang=language_code
            )

            bold_header = UnicodeTextFormatter.make_bold(header_text)
            sections.append(f"📝 {bold_header}")

        # ═══════════════════════════════════════════════════
        # SECTION 8: ACTION ITEMS (joined with newline, not blank line)
        # ═══════════════════════════════════════════════════
        action_lines: List[str] = []
        for action in recent[-5:]:  # Last 5 actions
            action_text = self._sanitize_text(action)
            action_lines.append(f"• {action_text}")

        if action_lines:
            sections.append("\n".join(action_lines))

        # ═══════════════════════════════════════════════════
        # FINAL ASSEMBLY: Join sections with double newlines
        # ═══════════════════════════════════════════════════
        message_text = "\n\n".join(section for section in sections if section)

        return normalize_numbers(message_text)

    def _format_game_state(
        self,
        game: Game,
        current_player: Optional[Player] = None,
    ) -> str:
        resolved_player = current_player
        players = list(getattr(game, "players", []) or [])
        if resolved_player is None and players:
            index = getattr(game, "current_player_index", -1)
            if 0 <= index < len(players):
                resolved_player = players[index]

        context = self._build_render_context(game, resolved_player)
        return self._build_game_state_text(
            game=game,
            current_player=resolved_player,
            context=context,
            preview_raise=None,
            compact=False,
        )

    @staticmethod
    def _calculate_message_bytes(text: str) -> int:
        """Calculate UTF-8 byte size of message."""

        return len(text.encode("utf-8"))

    def _capture_game_snapshot(self, game: Game) -> dict:
        """Capture current game state for diffing."""

        return {
            "game_id": getattr(game, "id", None),
            "chat_id": getattr(game, "chat_id", None),
            "state": getattr(game, "state", None),
            "cards_table": list(getattr(game, "cards_table", []) or []),
            "pot": getattr(game, "pot", 0),
            "current_player_index": getattr(game, "current_player_index", -1),
            "player_count": len(getattr(game, "players", []) or []),
            "max_round_rate": getattr(game, "max_round_rate", 0),
            "snapshot_time": time.time(),
        }

    def _calculate_state_diff(
        self,
        old_snapshot: Optional[dict],
        new_snapshot: dict,
    ) -> dict:
        """Return dictionary of changes between snapshots."""

        if not old_snapshot:
            return {"type": "full_refresh"}

        diff: Dict[str, Any] = {"type": "incremental"}

        if old_snapshot.get("pot") != new_snapshot.get("pot"):
            diff["pot"] = {
                "old": old_snapshot.get("pot"),
                "new": new_snapshot.get("pot"),
            }

        old_board = old_snapshot.get("cards_table", [])
        new_board = new_snapshot.get("cards_table", [])
        if len(new_board) > len(old_board):
            diff["new_cards"] = new_board[len(old_board):]

        if old_snapshot.get("state") != new_snapshot.get("state"):
            diff["state"] = {
                "old": old_snapshot.get("state"),
                "new": new_snapshot.get("state"),
            }

        if (
            old_snapshot.get("current_player_index")
            != new_snapshot.get("current_player_index")
        ):
            diff["current_player_index"] = {
                "old": old_snapshot.get("current_player_index"),
                "new": new_snapshot.get("current_player_index"),
            }

        if old_snapshot.get("player_count") != new_snapshot.get("player_count"):
            diff["player_count"] = {
                "old": old_snapshot.get("player_count"),
                "new": new_snapshot.get("player_count"),
            }

        if old_snapshot.get("max_round_rate") != new_snapshot.get(
            "max_round_rate"
        ):
            diff["max_round_rate"] = {
                "old": old_snapshot.get("max_round_rate"),
                "new": new_snapshot.get("max_round_rate"),
            }

        return diff

    def _update_network_metrics(
        self,
        render_state: ChatRenderState,
        latency_ms: float,
    ) -> None:
        """Track latency for diagnostics only - not used for delays."""

        render_state.last_update_latency_ms = latency_ms
        render_state.network_quality = "fast"

    def _needs_compact_mode(
        self,
        *,
        message_bytes: int,
        device_profile: DeviceProfile,
        render_state: ChatRenderState,
    ) -> bool:
        """Determine if the message should be rendered in compact form."""

        if message_bytes > 2000:
            return True

        if getattr(device_profile, "max_line_length", 70) <= 40:
            return True

        if render_state.network_quality in {"poor", "slow"}:
            return True

        return False

    def _format_recent_actions(self, game: Game) -> List[str]:
        recent = getattr(game, "recent_actions", []) or []
        return [self._sanitize_text(action) for action in recent[-3:]]

    def _format_board_cards(self, cards: List) -> str:
        """Format board cards without any flash effects.

        Replaced the old _format_board_line and _format_cards_with_flash
        which added sparkle markers (✨) around newly revealed cards.
        Now cards are displayed immediately without animation delays.
        """
        if not cards:
            return "🂠 🂠 🂠"

        from pokerapp.pokerbotview import PokerBotViewer

        formatted = [PokerBotViewer._format_card(card) for card in cards]
        return " - ".join(formatted)

    def _get_action_emoji(self, action_text: str) -> str:
        """Return emoji based on action type."""
        lower = action_text.lower()

        if "check" in lower:
            return "✅"
        elif "call" in lower:
            return "💵"
        elif "raise" in lower or "bet" in lower:
            return "🔺"
        elif "fold" in lower:
            return "⛔️"
        elif "all" in lower and "in" in lower:
            return "🔥"
        return "•"

    def _compute_content_hash(
        self,
        game: Game,
        current_player: Optional[Player],
    ) -> str:
        """Generate a deterministic digest of the visible game state."""

        cards = getattr(game, "cards_table", []) or []
        cards_repr = (
            "".join(sorted(str(card) for card in cards)) if cards else "NONE"
        )

        pot_value = getattr(game, "pot", 0)
        actor_id = getattr(current_player, "user_id", "NONE")
        state_obj = getattr(game, "state", None)
        street_name = getattr(state_obj, "name", str(state_obj) if state_obj else "UNKNOWN")

        player_states: List[str] = []
        for player in getattr(game, "players", []) or []:
            player_id = getattr(player, "user_id", "?")
            status = "ACTIVE"
            player_state = getattr(player, "state", None)
            if player_state == PlayerState.FOLD:
                status = "FOLDED"
            elif player_state == PlayerState.ALL_IN:
                status = "ALL_IN"
            player_states.append(f"{player_id}:{status}")

        components = [
            f"cards:{cards_repr}",
            f"pot:{pot_value}",
            f"actor:{actor_id}",
            f"street:{street_name}",
            "players:" + "|".join(sorted(player_states)),
        ]

        content = "||".join(components)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _timer_bucket(self, game: Game) -> Optional[int]:
        last_turn = getattr(game, "last_turn_time", None)
        if not isinstance(last_turn, _dt.datetime):
            return None

        now = _dt.datetime.now(tz=last_turn.tzinfo)
        elapsed = int((now - last_turn).total_seconds())
        remaining = self.DEFAULT_TURN_SECONDS - elapsed
        if remaining <= 0:
            return 0
        if remaining <= 5:
            return remaining
        if remaining <= 10:
            return 10
        bucket = (remaining // 5) * 5
        return max(bucket, 5)

    def _select_banner(
        self, context: Dict[str, Any], previous: Dict[str, Any]
    ) -> Optional[str]:
        """Banner system disabled - returns None to suppress notification line."""
        return None

    def _compute_raise_options(
        self, game: Game, player: Optional[Player]
    ) -> List[RaiseOptionMeta]:
        if player is None:
            return []

        wallet = max(player.wallet.value(), 0)
        current_bet = max(game.max_round_rate, 0)
        player_bet = max(player.round_rate, 0)
        total_stack = player_bet + wallet
        if wallet <= 0 or total_stack <= current_bet:
            return []

        big_blind = max((getattr(game, "table_stake", 0) or 0) * 2, 1)
        min_raise = max(current_bet * 2, big_blind)
        if min_raise > total_stack:
            return []

        language_code = getattr(self, "_language_code", translation_manager.DEFAULT_LANGUAGE)

        def _format_amount(value: int) -> str:
            return translation_manager.format_currency(value, language=language_code)

        def _snap(value: Optional[int]) -> Optional[int]:
            if value is None:
                return None
            snapped = int(value)
            if snapped < min_raise:
                snapped = min_raise
            if snapped > total_stack:
                return None
            if big_blind > 0:
                remainder = snapped % big_blind
                if remainder:
                    snapped += big_blind - remainder
            if snapped > total_stack:
                return None
            return snapped

        amount_options: List[RaiseOptionMeta] = []
        pot_options: List[RaiseOptionMeta] = []
        seen_amounts: Set[int] = set()

        def _add_amount_option(candidate: Optional[int]) -> None:
            snapped = _snap(candidate)
            if snapped is None or snapped in seen_amounts or snapped == total_stack:
                return
            seen_amounts.add(snapped)
            formatted = _format_amount(snapped)
            amount_options.append(
                RaiseOptionMeta(
                    key=str(snapped),
                    button_label=formatted,
                    preview_label=f"Raise to {formatted}",
                    amount=snapped,
                    kind="amount",
                )
            )

        def _add_pot_option(suffix: str, candidate: Optional[int], label: str) -> None:
            snapped = _snap(candidate)
            if snapped is None or snapped in seen_amounts or snapped == total_stack:
                return
            seen_amounts.add(snapped)
            formatted = _format_amount(snapped)
            pot_options.append(
                RaiseOptionMeta(
                    key=f"POT_{suffix}",
                    button_label=f"{label} {formatted}",
                    preview_label=f"{label} ({formatted})",
                    amount=snapped,
                    kind="pot",
                )
            )

        _add_amount_option(min_raise)

        max_raise = total_stack
        span = max(max_raise - min_raise, 0)
        if span > 0:
            step = max(big_blind, span // 5 or big_blind)
            for idx in range(1, 6):
                _add_amount_option(min_raise + step * idx)

        if len(amount_options) < 4:
            for offset in range(1, 6):
                _add_amount_option(min_raise + big_blind * offset)
                if len(amount_options) >= 4:
                    break

        pot_amount = max(getattr(game, "pot", 0), 0)
        if pot_amount > 0:
            for ratio, suffix, label in (
                (0.5, "HALF", "½ Pot"),
                (2 / 3, "TWO_THIRDS", "⅔ Pot"),
                (0.75, "THREE_QUARTERS", "¾ Pot"),
                (1.0, "FULL", "Pot"),
                (1.5, "ONE_HALF", "1½ Pot"),
                (2.0, "DOUBLE", "2× Pot"),
            ):
                _add_pot_option(
                    suffix,
                    int(round(pot_amount * ratio)),
                    label,
                )

        amount_options.sort(key=lambda option: option.amount or 0)
        pot_options.sort(key=lambda option: option.amount or 0)

        options: List[RaiseOptionMeta] = [*amount_options, *pot_options]

        formatted_stack = _format_amount(total_stack)
        options.append(
            RaiseOptionMeta(
                key="ALLIN",
                button_label=f"All-in {formatted_stack}",
                preview_label=f"All-in ({formatted_stack})",
                amount=total_stack,
                kind="all_in",
            )
        )

        return options

    def _build_action_inline_keyboard(
        self,
        game: Game,
        player: Optional[Player],
        version: Optional[int],
        *,
        use_cache: bool = True,
        device_profile: Optional[DeviceProfile] = None,
    ) -> Tuple[Optional[InlineKeyboardMarkup], List[RaiseOptionMeta]]:
        if player is None:
            return None, []

        profile = device_profile or self._device_detector.detect_device()
        emoji_scale = getattr(profile, "emoji_size_multiplier", 1.0)
        is_mobile = getattr(profile, "max_line_length", 0) <= 40
        variant_key = getattr(getattr(profile, "device_type", None), "value", "default")
        cache_variant = f"{variant_key}:{self._language_code}"

        cache_allowed = (
            use_cache
            and getattr(self, "_render_cache", None) is not None
            and getattr(profile, "max_line_length", 0) >= 50
        )

        if cache_allowed:
            cached = self._render_cache.get_cached_render(
                game,
                player,
                variant=cache_variant,
            )
            if cached and cached.keyboard_layout:
                markup = rehydrate_keyboard_layout(
                    cached.keyboard_layout,
                    version=version,
                )
                options = self._compute_raise_options(game, player)
                return markup, options

        buttons: List[List[InlineKeyboardButton]] = []

        current_bet = max(game.max_round_rate, 0)
        player_bet = max(player.round_rate, 0)
        player_balance = max(player.wallet.value(), 0)
        call_amount = max(current_bet - player_bet, 0)
        game_id = str(getattr(game, "id", ""))
        version_segment: List[str] = []
        if version is not None:
            version_segment.append(str(version))

        options = self._compute_raise_options(game, player)
        can_raise = any(opt.kind in {"amount", "pot"} for opt in options)
        has_all_in_option = any(opt.kind == "all_in" for opt in options)

        stake_config = getattr(game, "stake_config", None)
        config_big_blind = (
            getattr(stake_config, "big_blind", 0) if stake_config else 0
        )
        table_big_blind = (getattr(game, "table_stake", 0) or 0) * 2
        baseline_big_blind = max(config_big_blind, table_big_blind, 20)
        min_raise = max(current_bet * 2, baseline_big_blind)

        available_actions: Set[PlayerAction] = {PlayerAction.FOLD}
        if call_amount <= 0:
            available_actions.add(PlayerAction.CHECK)
        elif call_amount < player_balance:
            available_actions.add(PlayerAction.CALL)
        elif player_balance > 0:
            available_actions.add(PlayerAction.ALL_IN)

        if can_raise and player_balance > 0:
            available_actions.add(PlayerAction.RAISE_RATE)
        if has_all_in_option and player_balance > 0:
            available_actions.add(PlayerAction.ALL_IN)

        def _callback(action: str, *extra: str) -> str:
            return ":".join(["action", action, *extra, *version_segment, game_id])

        if is_mobile:
            def _build_mobile_buttons() -> List[List[InlineKeyboardButton]]:
                mobile_rows: List[List[InlineKeyboardButton]] = []

                if PlayerAction.CHECK in available_actions:
                    mobile_rows.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_label(
                                    "✅",
                                    "CHECK",
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("check"),
                            )
                        ]
                    )
                elif PlayerAction.CALL in available_actions:
                    mobile_rows.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_label(
                                    "💰",
                                    f"CALL ${call_amount:,}",
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("call"),
                            )
                        ]
                    )

                if PlayerAction.RAISE_RATE in available_actions:
                    max_raise = player_balance
                    presets: List[Tuple[str, str, int]] = []

                    if min_raise <= max_raise:
                        presets.append(("📈", f"MIN (${min_raise:,})", min_raise))

                    pot_amount = max(getattr(game, "pot", 0), 0)
                    two_pot = pot_amount * 2
                    if min_raise <= two_pot <= max_raise:
                        presets.append(("📈", f"2×POT (${two_pot:,})", two_pot))

                    half_stack = max_raise // 2
                    if (
                        half_stack >= min_raise
                        and half_stack <= max_raise
                        and all(option[2] != half_stack for option in presets)
                    ):
                        presets.append(("💼", f"½STACK (${half_stack:,})", half_stack))

                    for i in range(0, len(presets), 2):
                        chunk = presets[i: i + 2]
                        row: List[InlineKeyboardButton] = []
                        for emoji, label, amount in chunk:
                            row.append(
                                InlineKeyboardButton(
                                    self._format_mobile_button_label(
                                        emoji,
                                        label,
                                        emoji_scale=emoji_scale,
                                    ),
                                    callback_data=_callback("raise", str(amount)),
                                )
                            )
                        if row:
                            mobile_rows.append(row)

                if PlayerAction.ALL_IN in available_actions:
                    mobile_rows.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_label(
                                    "🔥",
                                    f"ALL-IN ${player_balance:,}",
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("all_in"),
                            )
                        ]
                    )

                if PlayerAction.FOLD in available_actions:
                    mobile_rows.append(
                        [
                            InlineKeyboardButton(
                                self._format_mobile_button_label(
                                    "❌",
                                    "FOLD",
                                    emoji_scale=emoji_scale,
                                ),
                                callback_data=_callback("fold"),
                            )
                        ]
                    )

                return mobile_rows

            mobile_keyboard = _build_mobile_buttons()
            if mobile_keyboard:
                return InlineKeyboardMarkup(mobile_keyboard), options

        first_row: List[InlineKeyboardButton] = []
        show_primary_all_in = False

        if PlayerAction.CHECK in available_actions:
            first_row.append(
                InlineKeyboardButton(
                    "✅ Check",
                    callback_data=_callback("check"),
                )
            )
        elif PlayerAction.CALL in available_actions:
            first_row.append(
                InlineKeyboardButton(
                    f"💵 Call ${call_amount}",
                    callback_data=_callback("call"),
                )
            )
        else:
            show_primary_all_in = player_balance > 0
            if show_primary_all_in:
                first_row.append(
                    InlineKeyboardButton(
                        f"🔥 All-In (${player_balance})",
                        callback_data=_callback("all_in"),
                    )
                )

        first_row.append(
            InlineKeyboardButton(
                "🚪 Fold",
                callback_data=_callback("fold"),
            )
        )
        buttons.append(first_row)

        if player_balance > 0:
            second_row: List[InlineKeyboardButton] = []

            if can_raise:
                second_row.append(
                    InlineKeyboardButton(
                        "📈 Raise",
                        callback_data=_callback("raise", "start"),
                    )
                )

            if not show_primary_all_in and has_all_in_option:
                second_row.append(
                    InlineKeyboardButton(
                        f"💥 All-In (${player_balance})",
                        callback_data=_callback("all_in"),
                    )
                )

            if second_row:
                buttons.append(second_row)

        if not buttons:
            return None, options

        markup = InlineKeyboardMarkup(buttons)

        if cache_allowed and buttons:
            self._render_cache.cache_render_result(
                game,
                player,
                keyboard_layout=serialise_keyboard_layout(
                    markup.inline_keyboard,
                    version=version,
                ),
                variant=cache_variant,
            )

        return markup, options

    def _build_raise_selection_keyboard(
        self,
        *,
        game: Game,
        player: Optional[Player],
        version: Optional[int],
        options: List[RaiseOptionMeta],
        selected_key: Optional[str],
    ) -> Optional[InlineKeyboardMarkup]:
        if player is None or not options:
            return None

        version_segment: List[str] = []
        if version is not None:
            version_segment.append(str(version))

        game_id = str(getattr(game, "id", ""))
        rows: List[List[InlineKeyboardButton]] = []

        option_map = {opt.key: opt for opt in options}
        selected_option = option_map.get(selected_key) if selected_key else None

        def _callback(action: str, *payload: str) -> str:
            return ":".join(
                ["action", action, *payload, *version_segment, game_id]
            )

        def _button_text(opt: RaiseOptionMeta) -> str:
            text = opt.button_label
            if selected_key == opt.key:
                text = f"✅ {text}"
            return text

        regular = [opt for opt in options if opt.kind == "amount"]
        specials = [opt for opt in options if opt.kind == "pot"]
        all_in_opts = [opt for opt in options if opt.kind == "all_in"]

        for index in range(0, len(regular), 2):
            chunk = regular[index : index + 2]
            if not chunk:
                continue
            rows.append(
                [
                    InlineKeyboardButton(
                        _button_text(opt),
                        callback_data=_callback("raise_amt", opt.key),
                    )
                    for opt in chunk
                ]
            )

        for index in range(0, len(specials), 2):
            chunk = specials[index : index + 2]
            if not chunk:
                continue
            rows.append(
                [
                    InlineKeyboardButton(
                        _button_text(opt),
                        callback_data=_callback("raise_amt", opt.key),
                    )
                    for opt in chunk
                ]
            )

        for opt in all_in_opts:
            rows.append(
                [
                    InlineKeyboardButton(
                        _button_text(opt),
                        callback_data=_callback("raise_amt", opt.key),
                    )
                ]
            )

        confirm_label = "✅ Confirm Raise"
        if selected_option is not None:
            if selected_option.kind == "all_in":
                confirm_label = "✅ Confirm All-in"
            elif selected_option.amount is not None:
                amount_display = translation_manager.format_currency(
                    selected_option.amount,
                    language=self._language_code,
                )
                confirm_label = f"✅ Confirm {amount_display}"

        rows.append(
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data=_callback("raise_back"),
                ),
                InlineKeyboardButton(
                    confirm_label,
                    callback_data=_callback("raise_confirm"),
                ),
            ]
        )

        return InlineKeyboardMarkup(rows)

    def _format_raise_preview(
        self,
        selected_key: Optional[str],
        *,
        state_options: Optional[Dict[str, RaiseOptionMeta]],
        options_order: Optional[List[str]],
        context_options: Optional[Dict[str, RaiseOptionMeta]],
    ) -> Optional[str]:
        if selected_key is None:
            return "—"

        if state_options is not None:
            option = state_options.get(selected_key)
            if option is not None:
                return UnicodeTextFormatter.make_bold(
                    self._sanitize_text(option.preview_label)
                )
        if context_options is not None:
            option = context_options.get(selected_key)
            if option is not None:
                return UnicodeTextFormatter.make_bold(
                    self._sanitize_text(option.preview_label)
                )
        return "—"

    def _serialize_reply_markup(
        self, reply_markup: Optional[InlineKeyboardMarkup]
    ) -> str:
        if reply_markup is None:
            return ""
        try:
            return json.dumps(reply_markup.to_dict(), sort_keys=True)
        except Exception:
            return ""

    def _payload_hash(self, text: str, keyboard_json: str) -> str:
        data = f"{text}\u241E{keyboard_json}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _schedule_banner_clear(
        self,
        *,
        chat_key: str,
        chat_id: int,
        message_id: int,
        expected_hash: str,
        state: ChatRenderState,
        game: Game,
    ) -> None:
        """Banner system removed - no-op."""
        return

    async def _dispatch_payload(
        self,
        *,
        chat_id: int,
        game: Game,
        bundle: RenderBundle,
        state: ChatRenderState,
    ) -> Optional[int]:
        message_id = None

        if game.has_group_message():
            start_time = time.perf_counter()
            old_message_id = game.group_message_id
            try:
                plain_text = self._prepare_plain_text(bundle.message_text)
                message = await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=old_message_id,
                    text=plain_text,
                    reply_markup=bundle.reply_markup,
                    disable_web_page_preview=True,
                )
                latency_ms = (time.perf_counter() - start_time) * 1000
                self._update_network_metrics(state, latency_ms)
                message_id = getattr(
                    message,
                    "message_id",
                    game.group_message_id,
                )
                self._logger.debug(
                    "✅ Successfully edited message %s", message_id
                )
                return message_id
            except TelegramError as exc:
                error_msg = str(exc).lower()

                if (
                    "not modified" in error_msg
                    or "message is not modified" in error_msg
                ):
                    self._logger.debug(
                        "Message %s content unchanged, skipping update",
                        old_message_id,
                    )
                    return game.group_message_id

                if (
                    "message to edit not found" in error_msg
                    or "message can't be edited" in error_msg
                    or "message_id_invalid" in error_msg
                ):
                    self._logger.warning(
                        "Message %s no longer exists, will send new message",
                        old_message_id,
                    )
                    game.group_message_id = None
                    self._logger.warning(
                        "🔄 Message edit failed, will recreate | old_message_id=%s, error=%s",
                        old_message_id,
                        str(exc)[:100],
                    )
                else:
                    self._logger.error(
                        "Failed to edit message %s: %s, will send new message",
                        old_message_id,
                        exc,
                    )
                    game.group_message_id = None
                    self._logger.warning(
                        "🔄 Message edit failed, will recreate | old_message_id=%s, error=%s",
                        old_message_id,
                        str(exc)[:100],
                    )

        try:
            self._logger.debug("Sending new live message to chat %s", chat_id)
            start_time = time.perf_counter()
            plain_text = self._prepare_plain_text(bundle.message_text)
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=plain_text,
                reply_markup=bundle.reply_markup,
                disable_notification=True,
                disable_web_page_preview=True,
            )
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._update_network_metrics(state, latency_ms)
            message_id = getattr(message, "message_id", None)

            if message_id is not None:
                self._logger.info(
                    "✅ Created new live message %s in chat %s",
                    message_id,
                    chat_id,
                )
                game.set_group_message(message_id)

            return message_id

        except TelegramError as exc:
            self._logger.error(
                "❌ Unable to send new live message to chat %s: %s",
                chat_id,
                exc,
            )
            return None

    async def _ping_player_if_needed(
        self,
        state: ChatRenderState,
        context: Dict[str, Any],
        *,
        chat_id: int,
        game: Game,
    ) -> None:
        actor_id = context.get("actor_user_id")
        if not actor_id or actor_id == state.last_actor_user_id:
            state.last_actor_user_id = actor_id
            return

        # The turn ping feature has been retired; simply record the new actor
        # so we avoid duplicate notifications without sending a transient
        # message to the chat.
        self._logger.debug(
            "Skipping turn ping for chat %s (feature disabled)", chat_id
        )
        state.last_actor_user_id = actor_id

    async def _auto_delete_message(
        self, chat_id: int, message_id: int, delay: int
    ) -> None:
        await asyncio.sleep(delay)
        with contextlib.suppress(TelegramError):
            await self._bot.delete_message(chat_id, message_id)

    def _get_player_name(self, player: Optional[Player]) -> str:
        """Extract display name from player for UI display."""

        if player is None:
            return "—"

        mention = getattr(player, "mention_markdown", None)

        if mention and mention.startswith("[") and "](" in mention:
            try:
                name = mention.split("]")[0][1:]
                if name:
                    return name
            except (IndexError, AttributeError):
                pass

        return f"User {player.user_id}"
