#!/usr/bin/env python3
"""Live message helper utilities for the in-chat game view."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import hashlib
import html
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from pokerapp.entities import Game, GameState, Player, PlayerAction, PlayerState
from pokerapp.device_detector import (
    DeviceDetector,
    DeviceProfile,
    DeviceType,
)
from pokerapp.kvstore import ensure_kv
from pokerapp.render_cache import RenderCache, RenderResult
from pokerapp.compact_formatter import CompactFormatter


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
    banner_task: Optional[asyncio.Task] = None
    pending_task: Optional[asyncio.Task] = None
    stable_text: str = ""
    stable_markup: Optional[InlineKeyboardMarkup] = None
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
    STAGE_EMOJIS = {
        0: "🔒",  # Pre-flop
        3: "🌄",  # Flop
        4: "🌇",  # Turn
        5: "🌃",  # River
    }

    STAGE_NAMES = {
        0: "Pre-flop",
        3: "Flop",
        4: "Turn",
        5: "River",
    }

    STAGE_ICONS = {
        0: "♠️",
        3: "🌼",
        4: "🔁",
        5: "🧊",
    }

    PARSE_MODE = "HTML"
    # Minimum spacing between consecutive updates per chat (seconds)
    DEBOUNCE_WINDOW = 0.35
    # Seconds before the transient banner is cleared
    BANNER_DURATION = 2.5
    # Duration before deleting the private "your turn" ping
    TURN_PING_TTL = 5
    # Approximate per-turn timer in seconds (aligned with model default)
    DEFAULT_TURN_SECONDS = 120

    STATE_CONTEXT_KEYS: Tuple[str, ...] = (
        "street_display_raw",
        "stage_name",
        "stage_icon",
        "to_act_display_raw",
        "actor_user_id",
        "pot_value",
        "last_bet_value",
        "board_display_raw",
        "timer_bucket",
    )

    FLASH_STATE_TTL = 3600

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
        # Track which cards have been revealed per chat for flash detection
        self._flash_states: Dict[int, Set[str]] = {}
        # Track cards currently highlighted with flash markers per chat
        self._active_flash_cards: Dict[int, Set[str]] = {}
        self._kv = ensure_kv(kv) if kv is not None else None
        cache_backend = ensure_kv(kv) if kv is not None else ensure_kv(None)
        self._render_cache = render_cache or RenderCache(cache_backend, logger)
        self._device_detector = DeviceDetector()
        self._language_code = "en"
        self._language_direction = "ltr"
        self._language_font = "system"

    def set_language_metadata(self, *, code: str, direction: str, font: str) -> None:
        """Update active language metadata for renders."""

        self._language_code = code
        self._language_direction = direction
        self._language_font = font

    def _apply_direction(self, text: Optional[str]) -> Optional[str]:
        if not text or self._language_direction != "rtl":
            return text
        if text.startswith("\u202B") and text.endswith("\u202C"):
            return text
        return f"\u202B{text}\u202C"

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
        lock = self._chat_locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_key] = lock

        async with lock:
            await self._apply_debounce(chat_key, state)
            loop = asyncio.get_running_loop()
            try:
                return await self._send_or_update_locked(
                    chat_id=chat_id,
                    chat_key=chat_key,
                    game=game,
                    current_player=current_player,
                    state=state,
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
            self._cancel_banner_task(state)
            version = (
                message_version
                if message_version is not None
                else game.get_live_message_version()
            )
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
                flash_cards=set(
                    self._active_flash_cards.get(chat_id, set())
                )
                or None,
                device_profile=device_profile,
            )

            if bundle.reply_markup is None:
                self._logger.debug(
                    "Raise selector unavailable - no options",
                )
                return False

            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=bundle.stable_text,
                    reply_markup=bundle.reply_markup,
                    parse_mode=self.PARSE_MODE,
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
            state.stable_text = bundle.stable_text
            state.stable_markup = bundle.reply_markup
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
            self._cancel_banner_task(state)
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
                flash_cards=set(
                    self._active_flash_cards.get(chat_id, set())
                )
                or None,
                device_profile=device_profile,
            )

            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=bundle.stable_text,
                    reply_markup=bundle.reply_markup,
                    parse_mode=self.PARSE_MODE,
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
            state.stable_text = bundle.stable_text
            state.stable_markup = bundle.reply_markup
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
        """Calculate adaptive debounce delay based on network quality."""

        quality = render_state.network_quality
        delay_map = {
            "fast": 0.1,
            "slow": 0.3,
            "poor": 1.0,
            "unknown": 0.2,
        }
        delay = delay_map.get(quality, 0.2)

        if getattr(render_state, "pending_task", None) is not None:
            delay *= 1.5

        self._logger.debug(
            "Adaptive debounce: %.1fms (quality=%s)",
            delay * 1000,
            quality,
        )

        return delay

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
        self, chat_key: str, state: ChatRenderState
    ) -> None:
        """Sleep briefly if the last update happened too recently."""

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

    async def _send_or_update_locked(
        self,
        chat_id: int,
        chat_key: str,
        game: Game,
        current_player: Player,
        state: ChatRenderState,
    ) -> Optional[int]:
        """Internal helper executing the actual message update."""

        next_version = None
        if current_player is not None:
            next_version = game.next_live_message_version()

        old_snapshot = state.last_game_snapshot
        new_snapshot = self._capture_game_snapshot(game)
        state_diff = self._calculate_state_diff(old_snapshot, new_snapshot)
        if state_diff.get("type") == "incremental":
            changed_keys = [key for key in state_diff.keys() if key != "type"]
            self._logger.debug(
                "Incremental update detected: %s",
                changed_keys,
            )
        state.last_game_snapshot = new_snapshot

        await self._ensure_flash_state(chat_id)

        cards_on_table = list(getattr(game, "cards_table", []) or [])
        new_cards = self._detect_new_cards(chat_id, cards_on_table)

        if new_cards:
            flash_state = self._flash_states.setdefault(chat_id, set())
            flash_state.update(new_cards)
            active_flash_cards: Set[str] = set(new_cards)
            self._active_flash_cards[chat_id] = set(new_cards)
            await self._persist_flash_state(chat_id)
        else:
            active_flash_cards = set(
                self._active_flash_cards.get(chat_id, set())
            )

        base_hash = self._compute_content_hash(game, current_player)
        flash_suffix = (
            "|flash:" + "|".join(sorted(active_flash_cards))
            if active_flash_cards
            else "|flash:"
        )
        render_token = f"{base_hash}{flash_suffix}"
        previous_token = self._content_hashes.get(chat_id)

        if previous_token == render_token and state.last_payload_hash is not None:
            self._logger.debug(
                "⏭️ ContentSkip - No changes detected (hash=%s)",
                base_hash[:8],
            )
            return (
                game.group_message_id if game.has_group_message() else None
            )

        self._content_hashes[chat_id] = render_token

        if new_cards:

            async def cleanup_flash() -> None:
                await asyncio.sleep(2.5)
                self._flash_states[chat_id] = {
                    str(card)
                    for card in (getattr(game, "cards_table", []) or [])
                }
                self._active_flash_cards.pop(chat_id, None)
                await self._persist_flash_state(chat_id)
                self._logger.debug(
                    "🧹 FlashCleanup - Removing flash markers for chat %s",
                    chat_id,
                )
                await self.send_or_update_game_state(
                    chat_id=chat_id,
                    game=game,
                    current_player=current_player,
                )

            asyncio.create_task(cleanup_flash())

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
            flash_cards=active_flash_cards or None,
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
        state.stable_text = bundle.stable_text
        state.stable_markup = bundle.reply_markup
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
            )
        else:
            self._cancel_banner_task(state)

        await self._ping_player_if_needed(state, bundle.context)

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
        flash_cards: Optional[Set[str]] = None,
        device_profile: DeviceProfile,
    ) -> RenderBundle:
        """Build message text, markup, and hashes for a render pass."""

        context = self._build_render_context(
            game,
            current_player,
            flash_cards=flash_cards,
            device_profile=device_profile,
        )
        context["language_code"] = self._language_code
        context["layout_direction"] = self._language_direction
        context["font_family"] = self._language_font

        preview_text: Optional[str] = None
        stable_text: Optional[str] = None
        reply_markup: Optional[InlineKeyboardMarkup] = None
        options: List[RaiseOptionMeta] = []
        compact_mode = self._should_use_compact_mode(device_profile, state)

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
                display = self._build_display_strings(context, state.last_context)
                stable_text = self._compose_message_body(
                    game=game,
                    current_player=current_player,
                    context=context,
                    display=display,
                    preview_raise=None,
                    device_profile=device_profile,
                    compact=compact_mode,
                )

            options = self._compute_raise_options(game, current_player)

            if cached_layout:
                reply_markup = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(**btn) for btn in row]
                        for row in cached_layout
                    ]
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
            display = self._build_display_strings(context, state.last_context)
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
                display=display,
                preview_raise=preview_text,
                device_profile=device_profile,
                compact=compact_mode,
            )

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
                layout_to_cache = [
                    [
                        {"text": btn.text, "callback_data": btn.callback_data}
                        for btn in row
                    ]
                    for row in reply_markup.inline_keyboard
                ]
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
        *,
        flash_cards: Optional[Set[str]] = None,
        device_profile: DeviceProfile,
    ) -> Dict[str, Any]:
        players = list(getattr(game, "players", []) or [])
        active_count = sum(1 for p in players if p.state != PlayerState.FOLD)
        num_cards = len(game.cards_table or [])
        stage_name = self.STAGE_NAMES.get(num_cards, "Pre-flop")
        stage_icon = self.STAGE_ICONS.get(num_cards, "♠️")
        board_line = self._format_board_line(
            game.cards_table or [],
            flash_cards=flash_cards,
        )

        actor_user_id = getattr(current_player, "user_id", None)
        to_act_name = self._get_player_name(current_player) if current_player else None
        if to_act_name:
            to_act_display = f"{to_act_name} · YOUR TURN"
        else:
            to_act_display = "Waiting…"

        timer_bucket = self._timer_bucket(game)
        timer_label = "—" if timer_bucket is None else f"{timer_bucket}s"

        context = {
            "stage_name": stage_name,
            "stage_icon": stage_icon,
            "stage_emoji": self.STAGE_EMOJIS.get(num_cards, "🔒"),
            "street_display_raw": f"{stage_icon} {stage_name}",
            "board_display_raw": board_line or "—",
            "pot_value": getattr(game, "pot", 0),
            "last_bet_value": getattr(game, "max_round_rate", 0),
            "actor_user_id": actor_user_id,
            "to_act_display_raw": to_act_display,
            "timer_bucket": timer_bucket,
            "timer_label": timer_label,
            "stack_line": self._format_stack_line(players),
            "active_count": active_count,
            "player_rows": self._format_players(
                players,
                actor_user_id,
                timer_seconds=getattr(game, "time_remaining", None),
            ),
            "recent_actions": self._format_recent_actions(game),
            "table_code": str(getattr(game, "id", "----"))[:4].upper(),
            "seat_label": f"{len(players)}-max",
        }

        context["device_profile"] = device_profile
        context["player_rows_mobile"] = self._format_players_mobile(
            players,
            actor_user_id,
            timer_seconds=getattr(game, "time_remaining", None),
            device_profile=device_profile,
        )

        return context

    def _build_display_strings(
        self, context: Dict[str, Any], previous: Dict[str, Any]
    ) -> Dict[str, str]:
        display: Dict[str, str] = {}

        display["street"] = self._format_diff_value(
            previous.get("street_display_raw"),
            context.get("street_display_raw"),
            formatter=lambda value: html.escape(value or "—"),
        )
        display["to_act"] = self._format_diff_value(
            previous.get("to_act_display_raw"),
            context.get("to_act_display_raw"),
            formatter=lambda value: html.escape(value or "Waiting…"),
        )
        display["pot"] = self._format_currency_diff(
            previous.get("pot_value"),
            context.get("pot_value", 0),
        )
        display["last_bet"] = self._format_currency_diff(
            previous.get("last_bet_value"),
            context.get("last_bet_value", 0),
        )
        display["board"] = self._format_diff_value(
            (
                previous.get("board_display_raw"),
                previous.get("stage_icon"),
            ),
            (
                context.get("board_display_raw"),
                context.get("stage_icon"),
            ),
            formatter=lambda value: self._format_board_display(*value),
        )
        display["timer"] = html.escape(context.get("timer_label", "—"))
        display["stacks"] = html.escape(context.get("stack_line", "—"))
        display["table"] = html.escape(f"#{context.get('table_code', '----')}")
        display["seats"] = html.escape(context.get("seat_label", "—"))

        return display

    def _compose_message_body(
        self,
        *,
        game: Game,
        current_player: Optional[Player],
        context: Dict[str, Any],
        display: Dict[str, str],
        preview_raise: Optional[str],
        device_profile: DeviceProfile,
        compact: bool,
    ) -> str:
        if device_profile.device_type == DeviceType.MOBILE:
            body = self._compose_mobile_body(
                game,
                current_player,
                device_profile,
                compact=compact,
            )

            extra_lines: List[str] = []
            if preview_raise is not None:
                extra_lines.extend(["", f"🎯 <b>Selected raise:</b> {preview_raise}"])

            recent = context.get("recent_actions", [])
            if recent:
                extra_lines.extend(["", "📝 <b>Recent</b>"])
                extra_lines.extend(f"• {action}" for action in recent)

            if extra_lines:
                body = body + "\n" + "\n".join(extra_lines)

            return body

        lines: List[str] = []
        lines.append(self._build_hud_block(context, display))
        lines.append("")

        if preview_raise is not None:
            lines.append(f"🎯 <b>Selected raise:</b> {preview_raise}")
            lines.append("")

        lines.append(
            f"👥 <b>Players ({context.get('active_count', 0)} active)</b>"
        )
        lines.extend(context.get("player_rows", []))

        recent = context.get("recent_actions", [])
        if recent:
            lines.append("")
            lines.append("📝 <b>Recent</b>")
            lines.extend(f"• {action}" for action in recent)

        return "\n".join(lines)

    def _compose_mobile_body(
        self,
        game: Game,
        current_player: Optional[Player],
        device_profile: DeviceProfile,
        *,
        compact: bool = True,
    ) -> str:
        """Compose a mobile-friendly summary, optionally using compact mode."""

        lines: List[str] = []
        lines.append("🎴 <b>TEXAS HOLD'EM</b> 🎴")

        board_cards = list(getattr(game, "cards_table", []) or [])
        if compact:
            board_text = CompactFormatter.format_cards(board_cards)
        else:
            board_text = self._format_board_line(board_cards)

        lines.append("")
        if compact:
            lines.append("🃏 <b>BOARD</b>")
            lines.append(html.escape(board_text))
        else:
            stage_name = self.STAGE_NAMES.get(len(board_cards), "Pre-flop")
            stage_icon = self.STAGE_ICONS.get(len(board_cards), "♠️")
            lines.append(f"🃏 <b>{stage_icon} {html.escape(stage_name)}</b>")
            board_display = board_text or "—"
            max_chars = getattr(device_profile, "max_line_length", 0) or 0
            if max_chars and len(board_display) > max_chars:
                board_display = board_display[: max_chars - 1] + "…"
            lines.append(html.escape(board_display))

        side_pots = getattr(game, "side_pots", None)
        if side_pots:
            side_values = [getattr(pot, "amount", pot) for pot in side_pots]
        else:
            side_values = None

        if compact:
            pot_line = CompactFormatter.format_pot_compact(
                getattr(game, "pot", 0), side_values
            )
        else:
            pot_line = f"💰 <b>Pot:</b> {html.escape(self._format_chips(getattr(game, 'pot', 0)))}"

        lines.append("")
        lines.append(pot_line)

        lines.append("")
        lines.append("👥 <b>PLAYERS</b>")
        if not getattr(game, "players", []):
            lines.append("ℹ️ No players seated yet")
        else:
            show_cards = getattr(game, "state", None) == GameState.FINISHED
            for player in getattr(game, "players", []) or []:
                player_line = CompactFormatter.format_player_compact(
                    player,
                    show_cards=show_cards,
                )
                lines.append(html.escape(player_line))

        actor_name = self._get_player_name(current_player)
        if compact and actor_name not in {"", "—"}:
            lines.append("")
            lines.append(f"🎯 To act: {html.escape(actor_name)}")

        recent_actions = getattr(game, "recent_actions", []) or []
        if recent_actions and not compact:
            lines.append("")
            lines.append("📝 <b>Recent</b>")
            lines.extend(f"• {html.escape(action)}" for action in recent_actions[-3:])

        body = "\n".join(lines)
        size_bytes = self._calculate_message_bytes(body)
        self._logger.debug(
            "Mobile message composed: %d bytes (compact=%s)",
            size_bytes,
            compact,
        )

        return body

    @staticmethod
    def _calculate_message_bytes(text: str) -> int:
        """Calculate UTF-8 byte size of message."""

        return len(text.encode("utf-8"))

    def _capture_game_snapshot(self, game: Game) -> dict:
        """Capture minimal game state for diff comparison."""

        return {
            "pot": getattr(game, "pot", 0),
            "board": [str(card) for card in getattr(game, "cards_table", []) or []],
            "players": {
                getattr(p, "user_id", idx): {
                    "stack": getattr(p.wallet, "value", lambda: 0)(),
                    "bet": getattr(p, "round_rate", 0),
                    "state": getattr(getattr(p, "state", None), "name", "UNKNOWN"),
                }
                for idx, p in enumerate(getattr(game, "players", []) or [])
            },
            "state": getattr(getattr(game, "state", None), "name", "UNKNOWN"),
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

        old_board = old_snapshot.get("board", [])
        new_board = new_snapshot.get("board", [])
        if len(new_board) > len(old_board):
            diff["new_cards"] = new_board[len(old_board):]

        player_diffs: Dict[Any, Dict[str, Any]] = {}
        for pid, new_data in new_snapshot.get("players", {}).items():
            old_data = old_snapshot.get("players", {}).get(pid, {})
            player_diff: Dict[str, Any] = {}

            if old_data.get("stack") != new_data.get("stack"):
                player_diff["stack"] = {
                    "old": old_data.get("stack"),
                    "new": new_data.get("stack"),
                }

            if old_data.get("bet") != new_data.get("bet"):
                player_diff["bet"] = {
                    "old": old_data.get("bet", 0),
                    "new": new_data.get("bet"),
                }

            if old_data.get("state") != new_data.get("state"):
                player_diff["state"] = new_data.get("state")

            if player_diff:
                player_diffs[pid] = player_diff

        if player_diffs:
            diff["players"] = player_diffs

        return diff

    def _update_network_metrics(
        self,
        render_state: ChatRenderState,
        latency_ms: float,
    ) -> None:
        """Update rolling average latency and determine quality."""

        n = min(render_state.latency_samples, 9)
        old_avg = render_state.avg_latency_ms
        new_avg = (old_avg * n + latency_ms) / (n + 1)

        render_state.last_update_latency_ms = latency_ms
        render_state.avg_latency_ms = new_avg
        render_state.latency_samples += 1

        if new_avg < 200:
            render_state.network_quality = "fast"
        elif new_avg < 1000:
            render_state.network_quality = "slow"
        else:
            render_state.network_quality = "poor"

        self._logger.debug(
            "Network quality: %s (latency: %.1fms, avg: %.1fms)",
            render_state.network_quality,
            latency_ms,
            new_avg,
        )

    def _should_use_compact_mode(
        self,
        device_profile: DeviceProfile,
        render_state: ChatRenderState,
    ) -> bool:
        """Determine if compact mode should be used."""

        if device_profile.device_type == DeviceType.MOBILE:
            return True

        if (
            device_profile.device_type == DeviceType.TABLET
            and render_state.network_quality == "slow"
        ):
            return True

        if render_state.network_quality == "poor":
            return True

        return False

    def _build_hud_block(
        self, context: Dict[str, Any], display: Dict[str, str]
    ) -> str:
        hud_lines = [
            (
                "Table     : "
                f"{display['table']}  {display['seats']}     Street: {display['street']}"
            ),
            (
                "To Act    : "
                f"{display['to_act']}     Timer: {display['timer']}"
            ),
            (
                "Pot       : "
                f"{display['pot']}     Last bet: {display['last_bet']}"
            ),
            f"Stacks    : {display['stacks']}",
            f"Board     : {display['board']}",
        ]

        return "<pre>" + "\n".join(hud_lines) + "</pre>"

    def _format_mobile_board(
        self,
        context: Dict[str, Any],
        *,
        device_profile: DeviceProfile,
    ) -> str:
        board_raw = context.get("board_display_raw") or "—"
        stage_icon = context.get("stage_icon", "♠️")
        stage_name = html.escape(context.get("stage_name", "Pre-flop"))

        if board_raw.strip() in {"—", "🂠 🂠 🂠"}:
            return "🔒 Cards will be revealed during betting"

        board_display = html.escape(board_raw)
        max_chars = max(device_profile.max_line_length, 10)
        if len(board_display) > max_chars:
            board_display = board_display[: max_chars - 1] + "…"

        return f"{stage_icon} <b>{stage_name}</b>\n📋 <b>{board_display}</b>"

    def _format_players(
        self,
        players: List[Player],
        actor_user_id: Optional[int],
        timer_seconds: Optional[int] = None,
    ) -> List[str]:
        rows: List[str] = []

        for idx, player in enumerate(players, start=1):
            name = html.escape(self._get_player_name(player))
            balance = max(player.wallet.value(), 0)
            bet = max(player.round_rate, 0)

            stack_text = html.escape(self._format_chips(balance))

            bet_text = ""
            if bet:
                minimum_width = len(f"{bet:,}") + 2
                bet_text = html.escape(
                    self._format_chips(bet, width=minimum_width)
                )

            if player.state == PlayerState.FOLD:
                status_symbol = "💤"
                status_text = "Folded"
            elif player.state == PlayerState.ALL_IN:
                status_symbol = "🔥"
                status_text = f"ALL-IN {bet_text}" if bet_text else "ALL-IN"
            elif bet > 0:
                status_symbol = "✓"
                status_text = f"Bet {bet_text}"
            else:
                status_symbol = "—"
                status_text = "Waiting"

            if player.user_id == actor_user_id:
                name = f"<b>{name}</b>"
                status_text = f"⏳ {status_text.upper()}"

                if timer_seconds is not None and timer_seconds > 0:
                    status_text += f" ({timer_seconds}s)"

            rows.append(
                f"P{idx}: {name:<12} <code>{stack_text}</code> {status_symbol} {status_text}"
            )

        if not rows:
            rows.append("ℹ️ No players seated yet")

        return rows

    def _format_players_mobile(
        self,
        players: List[Player],
        actor_user_id: Optional[int],
        *,
        timer_seconds: Optional[int],
        device_profile: DeviceProfile,
    ) -> List[str]:
        if device_profile.device_type != DeviceType.MOBILE:
            return self._format_players(
                players,
                actor_user_id,
                timer_seconds=timer_seconds,
            )

        if not players:
            return ["ℹ️ No players seated yet"]

        lines: List[str] = []
        max_length = max(device_profile.max_line_length, 5)

        for player in players:
            raw_name = self._get_player_name(player)
            if len(raw_name) > max_length:
                raw_name = raw_name[: max_length - 3] + "..."
            safe_name = html.escape(raw_name)

            is_current = player.user_id == actor_user_id
            status_icon = "▶️" if is_current else "⏸"
            state_icon = self._get_player_state_icon(getattr(player, "state", None))

            stack_value = max(player.wallet.value(), 0)
            bet_value = max(player.round_rate, 0)
            stack_text = html.escape(self._format_chips(stack_value))
            bet_text = html.escape(self._format_chips(bet_value))

            if getattr(player, "state", None) == PlayerState.ALL_IN:
                state_label = "All-In"
            elif getattr(player, "state", None) == PlayerState.FOLD:
                state_label = "Folded"
            else:
                state_label = "Active"

            header_line = f"{status_icon} <b>{safe_name}</b>"
            if is_current:
                header_line += " <i>(Your turn)</i>"

            timer_suffix = ""
            if is_current and timer_seconds is not None and timer_seconds > 0:
                timer_suffix = f" · {timer_seconds}s"

            detail_line = (
                f"   {state_icon} {state_label} · Stack: {stack_text} | Bet: {bet_text}"
                f"{timer_suffix}"
            )

            lines.append(f"{header_line}\n{detail_line}")

        return lines

    def _get_player_state_icon(
        self, state: Optional[PlayerState]
    ) -> str:
        mapping = {
            PlayerState.ACTIVE: "✅",
            PlayerState.FOLD: "❌",
            PlayerState.ALL_IN: "🔥",
        }

        if state is None:
            return "❓"

        return mapping.get(state, "❓")

    def _format_recent_actions(self, game: Game) -> List[str]:
        recent = getattr(game, "recent_actions", []) or []
        return [html.escape(action) for action in recent[-3:]]

    def _format_stack_line(self, players: List[Player]) -> str:
        if not players:
            return "—"

        entries: List[str] = []
        for idx, player in enumerate(players, start=1):
            stack = max(player.wallet.value(), 0)
            entries.append(f"P{idx}: {self._format_chips(stack)}")

        return " | ".join(entries)

    def _format_board_line(
        self,
        cards,
        *,
        flash_cards: Optional[Set[str]] = None,
    ) -> str:
        if not cards:
            return "🂠 🂠 🂠"

        if flash_cards:
            return self._format_cards_with_flash(cards, flash_cards)

        from pokerapp.pokerbotview import PokerBotViewer

        return PokerBotViewer._format_cards_line(cards)

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

    def _detect_new_cards(
        self,
        chat_id: int,
        current_cards: List,
    ) -> Set[str]:
        """Return the set of community cards that are newly revealed."""

        previous_flash = self._flash_states.get(chat_id, set())
        current_card_strs = {str(card) for card in current_cards}
        new_cards = current_card_strs - previous_flash

        if new_cards:
            self._logger.debug(
                "✨ FlashDetect - New cards in chat %s: %s",
                chat_id,
                ", ".join(sorted(new_cards)),
            )

        return new_cards

    def _flash_storage_key(self, chat_id: int) -> str:
        return f"flash:{chat_id}"

    async def _ensure_flash_state(self, chat_id: int) -> Set[str]:
        if chat_id in self._flash_states:
            return self._flash_states[chat_id]

        stored = await self._load_flash_state(chat_id)
        state = stored or set()
        self._flash_states[chat_id] = state
        return state

    async def _load_flash_state(self, chat_id: int) -> Optional[Set[str]]:
        if self._kv is None:
            return None

        key = self._flash_storage_key(chat_id)
        try:
            raw = self._kv.get(key)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "⚠️ FlashStorageRead - Failed to load flash state for chat %s: %s",
                chat_id,
                exc,
            )
            return None

        if not raw:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        elif not isinstance(raw, str):
            raw = str(raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "⚠️ FlashStorageRead - Invalid flash payload for chat %s: %s",
                chat_id,
                exc,
            )
            return None

        if isinstance(data, list):
            return {str(item) for item in data}

        return None

    async def _persist_flash_state(self, chat_id: int) -> None:
        if self._kv is None:
            return

        key = self._flash_storage_key(chat_id)
        payload = json.dumps(sorted(self._flash_states.get(chat_id, set())))

        try:
            result = self._kv.set(key, payload, ex=self.FLASH_STATE_TTL)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "⚠️ FlashStorageWrite - Failed to persist flash state for chat %s: %s",
                chat_id,
                exc,
            )

    def _format_cards_with_flash(
        self,
        cards: List,
        flash_cards: Set[str],
    ) -> str:
        """Format board cards and wrap new entries with sparkle markers."""

        if not cards:
            return "🂠 🂠 🂠"

        from pokerapp.pokerbotview import PokerBotViewer

        flash_lookup = set(flash_cards)
        formatted: List[str] = []
        for card in cards:
            card_display = PokerBotViewer._format_card(card)
            card_str = str(card)
            if card_str in flash_lookup:
                formatted.append(f"✨ {card_display} ✨")
            else:
                formatted.append(card_display)

        return "  ".join(formatted)

    def _format_board_display(self, board_text: Optional[str], icon: Optional[str]) -> str:
        text = board_text or "—"
        symbol = icon or "♠️"
        return f"{symbol} {html.escape(text)}"

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

    def _format_diff_value(
        self,
        previous: Any,
        current: Any,
        *,
        formatter,
    ) -> str:
        if current is None:
            current_text = formatter(current)
        else:
            current_text = formatter(current)

        if previous is None or previous == current:
            return current_text

        previous_text = formatter(previous)
        return f"<b>{previous_text} → {current_text}</b>"

    def _format_currency_diff(
        self, previous: Optional[int], current: int
    ) -> str:
        def formatter(value: Optional[int]) -> str:
            amount = value or 0
            return html.escape(self._format_chips(amount))

        return self._format_diff_value(previous, current, formatter=formatter)

    def _select_banner(
        self, context: Dict[str, Any], previous: Dict[str, Any]
    ) -> Optional[str]:
        if not previous:
            return None

        stage_changed = context.get("stage_name") != previous.get("stage_name")
        actor_changed = (
            context.get("actor_user_id") is not None
            and context.get("actor_user_id") != previous.get("actor_user_id")
        )
        pot_changed = context.get("pot_value") != previous.get("pot_value")

        if stage_changed:
            stage_name = context.get("stage_name", "Stage").upper()
            icon = context.get("stage_icon", "♠️")
            if actor_changed:
                return f"🔔 <b>{icon} {stage_name} dealt — your move!</b>"
            return f"🔔 <b>{icon} {stage_name} dealt</b>"

        if actor_changed:
            to_act = context.get("to_act_display_raw", "Your move")
            return f"🔔 <b>{html.escape(to_act)}</b>"

        if pot_changed:
            old_pot = previous.get("pot_value", 0)
            new_pot = context.get("pot_value", 0)
            return (
                "🔔 <b>Pot "
                f"{html.escape(self._format_chips(old_pot))}"
                f" → {html.escape(self._format_chips(new_pot))}</b>"
            )

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

        options: List[RaiseOptionMeta] = []
        amounts: List[int] = []

        if min_raise <= total_stack:
            amounts.append(min_raise)
            for multiplier in (3, 4, 5, 6, 8):
                candidate = big_blind * multiplier
                if candidate >= min_raise and candidate <= total_stack:
                    amounts.append(candidate)
            candidate = current_bet + big_blind
            if candidate >= min_raise and candidate <= total_stack:
                amounts.append(candidate)

        unique_amounts = sorted({amount for amount in amounts if amount >= min_raise})
        for amount in unique_amounts:
            options.append(
                RaiseOptionMeta(
                    key=str(amount),
                    button_label=f"${amount}",
                    preview_label=f"Raise to ${amount}",
                    amount=amount,
                    kind="amount",
                )
            )

        pot_amount = getattr(game, "pot", 0)
        if (
            pot_amount
            and pot_amount >= min_raise
            and pot_amount <= total_stack
            and pot_amount not in unique_amounts
        ):
            options.append(
                RaiseOptionMeta(
                    "POT",
                    f"Pot (${pot_amount})",
                    f"Pot (${pot_amount})",
                    pot_amount,
                    "pot",
                )
            )

        if total_stack > current_bet:
            options.append(
                RaiseOptionMeta(
                    "ALLIN",
                    f"All-in (${total_stack})",
                    f"All-in (${total_stack})",
                    total_stack,
                    "all_in",
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

        profile = device_profile or DeviceDetector.get_profile(DeviceType.DESKTOP)
        is_mobile = profile.device_type == DeviceType.MOBILE
        emoji_scale = getattr(profile, "emoji_size_multiplier", 1.0)
        cache_variant = f"{getattr(profile.device_type, 'value', 'default')}:{self._language_code}"

        cache_allowed = (
            use_cache
            and getattr(self, "_render_cache", None) is not None
            and not is_mobile
        )

        if cache_allowed:
            cached = self._render_cache.get_cached_render(
                game,
                player,
                variant=cache_variant,
            )
            if cached and cached.keyboard_layout:
                markup = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(**btn) for btn in row]
                        for row in cached.keyboard_layout
                    ]
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
            layout = [
                [
                    {"text": btn.text, "callback_data": btn.callback_data}
                    for btn in row
                ]
                for row in buttons
            ]
            self._render_cache.cache_render_result(
                game,
                player,
                keyboard_layout=layout,
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

        regular = [opt for opt in options if opt.kind == "amount"]
        specials = [opt for opt in options if opt.kind == "pot"]
        all_in_opts = [opt for opt in options if opt.kind == "all_in"]

        row: List[InlineKeyboardButton] = []
        for opt in regular:
            text = opt.button_label
            if selected_key == opt.key:
                text = f"✅ {text}"
            row.append(
                InlineKeyboardButton(
                    text,
                    callback_data=":".join(
                        [
                            "raise_amt",
                            opt.key,
                            *version_segment,
                            game_id,
                        ]
                    ),
                )
            )
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        if specials:
            special_row: List[InlineKeyboardButton] = []
            for opt in specials:
                text = opt.button_label
                if selected_key == opt.key:
                    text = f"✅ {text}"
                special_row.append(
                    InlineKeyboardButton(
                        text,
                        callback_data=":".join(
                            [
                                "raise_amt",
                                opt.key,
                                *version_segment,
                                game_id,
                            ]
                        ),
                    )
                )
            rows.append(special_row)

        if all_in_opts:
            opt = all_in_opts[0]
            text = opt.button_label
            if selected_key == opt.key:
                text = f"✅ {text}"
            rows.append(
                [
                    InlineKeyboardButton(
                        text,
                        callback_data=":".join(
                            [
                                "raise_amt",
                                opt.key,
                                *version_segment,
                                game_id,
                            ]
                        ),
                    )
                ]
            )

        control_row = [
            InlineKeyboardButton(
                "⬅ Back",
                callback_data=":".join(
                    ["raise_back", *version_segment, game_id]
                ),
            ),
            InlineKeyboardButton(
                "✔ Confirm",
                callback_data=":".join(
                    ["raise_confirm", *version_segment, game_id]
                ),
            ),
        ]
        rows.append(control_row)

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
                return f"<b>{html.escape(option.preview_label)}</b>"
        if context_options is not None:
            option = context_options.get(selected_key)
            if option is not None:
                return f"<b>{html.escape(option.preview_label)}</b>"
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

    def _cancel_banner_task(self, state: ChatRenderState) -> None:
        task = state.banner_task
        if task and not task.done():
            task.cancel()
        state.banner_task = None

    def _schedule_banner_clear(
        self,
        *,
        chat_key: str,
        chat_id: int,
        message_id: int,
        expected_hash: str,
        state: ChatRenderState,
    ) -> None:
        self._cancel_banner_task(state)

        if not state.stable_text:
            return

        async def _clear() -> None:
            await asyncio.sleep(self.BANNER_DURATION)
            if state.last_payload_hash != expected_hash:
                return
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=state.stable_text,
                    reply_markup=state.stable_markup,
                    parse_mode=self.PARSE_MODE,
                    disable_web_page_preview=True,
                )
            except TelegramError as exc:
                error_msg = str(exc).lower()
                if "not modified" not in error_msg:
                    self._logger.debug(
                        "Banner clear skipped for chat %s: %s",
                        chat_id,
                        exc,
                    )
                return

            keyboard_json = self._serialize_reply_markup(state.stable_markup)
            state.last_payload_hash = self._payload_hash(
                state.stable_text,
                keyboard_json,
            )
            state.last_keyboard_json = keyboard_json

        state.banner_task = asyncio.create_task(_clear())

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
            try:
                message = await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game.group_message_id,
                    text=bundle.message_text,
                    reply_markup=bundle.reply_markup,
                    parse_mode=self.PARSE_MODE,
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
                        game.group_message_id,
                    )
                    return game.group_message_id

                if (
                    "message to edit not found" in error_msg
                    or "message can't be edited" in error_msg
                    or "message_id_invalid" in error_msg
                ):
                    self._logger.warning(
                        "Message %s no longer exists, will send new message",
                        game.group_message_id,
                    )
                    game.group_message_id = None
                else:
                    self._logger.error(
                        "Failed to edit message %s: %s, will send new message",
                        game.group_message_id,
                        exc,
                    )
                    game.group_message_id = None

        try:
            self._logger.debug("Sending new live message to chat %s", chat_id)
            start_time = time.perf_counter()
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=bundle.message_text,
                reply_markup=bundle.reply_markup,
                parse_mode=self.PARSE_MODE,
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
        self, state: ChatRenderState, context: Dict[str, Any]
    ) -> None:
        actor_id = context.get("actor_user_id")
        if not actor_id or actor_id == state.last_actor_user_id:
            state.last_actor_user_id = actor_id
            return

        try:
            message = await self._bot.send_message(
                actor_id,
                "🎯 Your turn — check the table message.",
            )
        except TelegramError as exc:
            self._logger.debug(
                "Unable to send turn ping to %s: %s",
                actor_id,
                exc,
            )
            state.last_actor_user_id = actor_id
            return

        asyncio.create_task(
            self._auto_delete_message(
                message.chat_id,
                message.message_id,
                self.TURN_PING_TTL,
            )
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
