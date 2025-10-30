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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from pokerapp.entities import Game, Player, PlayerState
from pokerapp.kvstore import ensure_kv


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
    last_keyboard_json: str = ""
    banner_task: Optional[asyncio.Task] = None
    stable_text: str = ""
    stable_markup: Optional[InlineKeyboardMarkup] = None
    last_actor_user_id: Optional[int] = None
    raise_options: Dict[str, RaiseOptionMeta] = field(default_factory=dict)
    raise_order: List[str] = field(default_factory=list)
    raise_selections: Dict[int, Optional[str]] = field(default_factory=dict)


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

    def __init__(self, bot, logger, *, kv: Optional[Any] = None):
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
            await self._apply_debounce(chat_key)
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

    async def _apply_debounce(self, chat_key: str) -> None:
        """Sleep briefly if the last update happened too recently."""

        window = self.DEBOUNCE_WINDOW
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

        bundle = self._prepare_render_bundle(
            chat_key=chat_key,
            game=game,
            current_player=current_player,
            state=state,
            version=next_version,
            mode="actions",
            include_banner=True,
            flash_cards=active_flash_cards or None,
        )

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
        )

        if message_id is None:
            return None

        state.last_context = bundle.context
        state.last_payload_hash = bundle.payload_hash
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
    ) -> RenderBundle:
        """Build message text, markup, and hashes for a render pass."""

        context = self._build_render_context(
            game,
            current_player,
            flash_cards=flash_cards,
        )
        display = self._build_display_strings(context, state.last_context)

        preview_text: Optional[str] = None
        if mode == "raise_selection":
            preview_text = self._format_raise_preview(
                selected_raise,
                state_options=state.raise_options,
                options_order=state.raise_order,
                context_options=None,
            )

        stable_text = self._compose_message_body(
            context=context,
            display=display,
            preview_raise=preview_text,
        )

        banner = None
        if include_banner:
            banner = self._select_banner(context, state.last_context)

        message_text = f"{banner}\n{stable_text}" if banner else stable_text

        if mode == "actions":
            reply_markup, options = self._build_action_inline_keyboard(
                game=game,
                player=current_player,
                version=version,
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
                context=context,
                display=display,
                preview_raise=preview_text,
            )
            message_text = f"{banner}\n{stable_text}" if banner else stable_text

        option_map = {opt.key: opt for opt in options}
        option_order = [opt.key for opt in options]

        keyboard_json = self._serialize_reply_markup(reply_markup)
        payload_hash = self._payload_hash(message_text, keyboard_json)

        diff_context = {
            key: context.get(key)
            for key in self.STATE_CONTEXT_KEYS
        }

        return RenderBundle(
            message_text=message_text,
            stable_text=stable_text,
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
            "player_rows": self._format_players(players, actor_user_id),
            "recent_actions": self._format_recent_actions(game),
            "table_code": str(getattr(game, "id", "----"))[:4].upper(),
            "seat_label": f"{len(players)}-max",
        }

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
        context: Dict[str, Any],
        display: Dict[str, str],
        preview_raise: Optional[str],
    ) -> str:
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

    def _format_players(
        self, players: List[Player], actor_user_id: Optional[int]
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

            rows.append(
                f"P{idx}: {name:<12} <code>{stack_text}</code> {status_symbol} {status_text}"
            )

        if not rows:
            rows.append("ℹ️ No players seated yet")

        return rows

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
                    key="POT",
                    button_label=f"Pot (${pot_amount})",
                    preview_label=f"Pot (${pot_amount})",
                    amount=pot_amount,
                    kind="pot",
                )
            )

        if total_stack > current_bet:
            options.append(
                RaiseOptionMeta(
                    key="ALLIN",
                    button_label=f"All-in (${total_stack})",
                    preview_label=f"All-in (${total_stack})",
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
    ) -> Tuple[Optional[InlineKeyboardMarkup], List[RaiseOptionMeta]]:
        if player is None:
            return None, []

        buttons: List[List[InlineKeyboardButton]] = []

        current_bet = game.max_round_rate
        player_bet = player.round_rate
        player_balance = player.wallet.value()
        call_amount = max(current_bet - player_bet, 0)
        game_id = str(getattr(game, "id", ""))
        version_segment: List[str] = []
        if version is not None:
            version_segment.append(str(version))

        first_row: List[InlineKeyboardButton] = []
        show_primary_all_in = False

        if call_amount <= 0:
            first_row.append(
                InlineKeyboardButton(
                    "✅ Check",
                    callback_data=":".join(
                        ["action", "check", *version_segment, game_id]
                    ),
                )
            )
        elif call_amount < player_balance:
            first_row.append(
                InlineKeyboardButton(
                    f"💵 Call ${call_amount}",
                    callback_data=":".join(
                        ["action", "call", *version_segment, game_id]
                    ),
                )
            )
        else:
            show_primary_all_in = player_balance > 0
            first_row.append(
                InlineKeyboardButton(
                    f"🔥 All-In (${player_balance})",
                    callback_data=":".join(
                        ["action", "all_in", *version_segment, game_id]
                    ),
                )
            )

        first_row.append(
            InlineKeyboardButton(
                "🚪 Fold",
                callback_data=":".join(
                    ["action", "fold", *version_segment, game_id]
                ),
            )
        )
        buttons.append(first_row)

        options = self._compute_raise_options(game, player)
        can_raise = any(opt.kind in {"amount", "pot"} for opt in options)
        has_all_in_option = any(opt.kind == "all_in" for opt in options)

        if player_balance > 0:
            second_row: List[InlineKeyboardButton] = []

            if can_raise:
                second_row.append(
                    InlineKeyboardButton(
                        "📈 Raise",
                        callback_data=":".join(
                            [
                                "action",
                                "raise",
                                "start",
                                *version_segment,
                                game_id,
                            ]
                        ),
                    )
                )

            if not show_primary_all_in and has_all_in_option:
                second_row.append(
                    InlineKeyboardButton(
                        f"💥 All-In (${player_balance})",
                        callback_data=":".join(
                            ["action", "all_in", *version_segment, game_id]
                        ),
                    )
                )

            if second_row:
                buttons.append(second_row)

        if not buttons:
            return None, options

        return InlineKeyboardMarkup(buttons), options

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
    ) -> Optional[int]:
        message_id = None

        if game.has_group_message():
            try:
                message = await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=game.group_message_id,
                    text=bundle.message_text,
                    reply_markup=bundle.reply_markup,
                    parse_mode=self.PARSE_MODE,
                    disable_web_page_preview=True,
                )
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
            message = await self._bot.send_message(
                chat_id=chat_id,
                text=bundle.message_text,
                reply_markup=bundle.reply_markup,
                parse_mode=self.PARSE_MODE,
                disable_notification=True,
                disable_web_page_preview=True,
            )
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
