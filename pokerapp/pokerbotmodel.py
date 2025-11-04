#!/usr/bin/env python3

import asyncio
import datetime
from datetime import datetime as dt
import json
import logging
import secrets
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import redis
from telegram import Bot, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackContext, ContextTypes
from telegram.helpers import escape_markdown

from pokerapp.config import Config, STAKE_PRESETS
from pokerapp.cards import Cards, get_shuffled_deck
from pokerapp.privatechatmodel import UserPrivateChatModel
from pokerapp.entities import (
    Game,
    GameMode,
    GameState,
    Player,
    ChatId,
    UserId,
    UserException,
    Money,
    PlayerAction,
    PlayerState,
    Wallet,
    BalanceValidator,
    Score,
)
from pokerapp.game_coordinator import GameCoordinator
from pokerapp.group_lobby import GroupLobbyManager
from pokerapp.game_engine import TurnResult
from pokerapp.notify_utils import NotificationManager
from pokerapp.i18n import translation_manager
from pokerapp.pokerbotview import PokerBotViewer
from pokerapp.live_message import UnicodeTextFormatter
from pokerapp.kvstore import ensure_kv
from pokerapp.winnerdetermination import get_combination_name
from pokerapp.request_cache import RequestCache


logger = logging.getLogger(__name__)


DICE_MULT = 10
DICE_DELAY_SEC = 5
BONUSES = (5, 20, 40, 80, 160, 320)
DICES = "⚀⚁⚂⚃⚄⚅"

KEY_CHAT_DATA_GAME = "game"
KEY_OLD_PLAYERS = "old_players"
KEY_LAST_TIME_ADD_MONEY = "last_time"
KEY_NOW_TIME_ADD_MONEY = "now_time"

MAX_PLAYERS = 8
MIN_PLAYERS = 2
ONE_DAY = 86400
DEFAULT_MONEY = 1000
MAX_TIME_FOR_TURN = datetime.timedelta(minutes=2)
DESCRIPTION_FILE = "assets/description_bot.md"


class ModelTextKeys:
    JOIN_CODE_REQUIRED = "model.join.code_required"
    JOIN_CODE_INVALID = "model.join.code_invalid"
    JOIN_USAGE = "model.join.usage"
    JOIN_GAME_NOT_FOUND = "model.join.game_not_found"
    JOIN_GAME_STARTED = "model.join.game_started"
    JOIN_ALREADY_IN_GAME = "model.join.already_in_game"
    JOIN_GAME_FULL = "model.join.game_full"
    JOIN_NOT_ACCEPTING = "model.join.not_accepting"
    JOIN_SUCCESS = "model.join.success"


@dataclass(slots=True)
class PreparedPlayerAction:
    """Lightweight container describing a validated action request."""

    chat_id: int
    chat_id_str: str
    user_id: int
    user_id_str: str
    action_type: str
    raise_amount: Optional[int]
    game: Game
    current_player: Player


@dataclass(slots=True)
class PlayerActionValidation:
    """Result of validating a requested player action."""

    success: bool
    message: Optional[str] = None
    prepared_action: Optional[PreparedPlayerAction] = None


class PokerBotModel:
    def __init__(
        self,
        view: PokerBotViewer,
        bot: Bot,
        cfg: Config,
        kv,
        application: Application,
    ):
        self._view: PokerBotViewer = view
        self._bot: Bot = bot
        self._kv = ensure_kv(kv)
        self._cfg: Config = cfg
        self._application = application
        self._logger = logging.getLogger(__name__)

        # NEW: Replace old logic with coordinator
        self._coordinator = GameCoordinator(view=view, kv=self._kv)
        self._stake_config = STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL]

        self._readyMessages = {}
        self._username_cache: Dict[int, str] = {}
        self._request_cache = None  # Per-request cache instance

        self._lobby_manager = GroupLobbyManager(
            bot=self._bot,
            kvstore=self._kv,
            logger=self._logger,
        )

    def _get_or_create_cache(self) -> RequestCache:
        """Get or create request-scoped cache."""

        if self._request_cache is None:
            self._request_cache = RequestCache()
        return self._request_cache

    def _clear_request_cache(self) -> None:
        """Clear and log request cache statistics."""

        if self._request_cache is not None:
            self._request_cache.log_stats("ActionHandler")
            self._request_cache = None

    def _translate(
        self,
        key: str,
        *,
        user_id: Optional[int] = None,
        lang: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Translate *key* for the current user context."""

        return translation_manager.t(
            key,
            user_id=user_id,
            lang=lang,
            **kwargs,
        )

    async def _ensure_minimum_balance(
        self,
        update: Update,
        user_id: int,
        wallet: Wallet,
        min_balance: int,
        reply_to_message_id: Optional[int] = None,
    ) -> bool:
        """
        Centralized balance validation with error messaging.

        Returns:
            True if balance sufficient, False otherwise (error sent to user)
        """
        self._apply_user_language(update)

        balance = wallet.value()
        if balance < min_balance:
            await self._view.send_insufficient_balance_error(
                chat_id=update.effective_chat.id,
                balance=balance,
                required=min_balance,
                reply_to_message_id=reply_to_message_id,
            )
            return False
        return True

    def _validate_game_code(
        self,
        code: Optional[str],
        *,
        lang: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> tuple[bool, str]:
        """
        Validate game code format (6 alphanumeric characters).

        Returns:
            (is_valid, error_message)
        """
        language = translation_manager.resolve_language(
            user_id=user_id,
            lang=lang,
        )

        def _t(key: str, **kwargs: Any) -> str:
            return translation_manager.t(
                key,
                user_id=user_id,
                lang=language,
                **kwargs,
            )

        if not code:
            return False, _t(ModelTextKeys.JOIN_CODE_REQUIRED)

        if len(code) != 6 or not code.isalnum():
            return False, _t(ModelTextKeys.JOIN_CODE_INVALID, code=code)

        return True, ""

    def _generate_game_code(self) -> str:
        return secrets.token_urlsafe(4).upper()[:6]

    def _track_user(self, user_id: int, username: Optional[str]) -> None:
        """Store username→user_id mapping for invitation lookups."""

        if not username:
            return

        key = "username:" + username.lower()
        try:
            self._kv.set(key, str(user_id), ex=86400 * 30)
            self._username_cache[user_id] = username
        except Exception as exc:
            logger.debug(
                "Failed to cache username mapping for %s: %s",
                username,
                exc,
            )

    def _detect_and_cache_language(self, update: Update) -> str:
        """Detect user's language from Telegram and cache it."""

        user = update.effective_user
        if not user:
            return "en"

        telegram_lang = getattr(user, "language_code", None)

        return translation_manager.get_user_language_or_detect(
            user.id,
            telegram_language_code=telegram_lang,
        )

    def _apply_user_language(self, update: Update) -> str:
        """Ensure view uses detected language for this update."""

        chat = update.effective_chat
        user = update.effective_user

        user_language = self._detect_and_cache_language(update)
        resolved_language = translation_manager.resolve_language(lang=user_language)

        if chat is not None:
            chat_language = self._kv.get_chat_language(getattr(chat, "id", 0))
            if chat_language:
                resolved_language = translation_manager.resolve_language(lang=chat_language)
            elif chat.type in ("group", "supergroup"):
                self._kv.set_chat_language(chat.id, resolved_language)

        self._view.set_language_context(
            resolved_language,
            user_id=getattr(user, "id", None),
        )
        return resolved_language

    async def refresh_language_for_user(self, user_id: int) -> None:
        """Re-render active UI components when a user changes language."""

        affected_games: List[Tuple[int, Game]] = []

        for chat_id, chat_data in self._application.chat_data.items():
            if not isinstance(chat_data, dict):
                continue

            game = chat_data.get(KEY_CHAT_DATA_GAME)
            if not isinstance(game, Game):
                continue

            players = getattr(game, "players", []) or []
            if any(getattr(player, "user_id", None) == user_id for player in players):
                try:
                    chat_id_int = int(chat_id)
                except (TypeError, ValueError):
                    continue
                affected_games.append((chat_id_int, game))

        if not affected_games:
            return

        async def _refresh(chat_id: int, game: Game) -> None:
            current_player = None
            players = getattr(game, "players", []) or []
            index = getattr(game, "current_player_index", -1)
            if 0 <= index < len(players):
                current_player = players[index]

            try:
                await self._coordinator._send_or_update_game_state(
                    game=game,
                    current_player=current_player,
                    chat_id=chat_id,
                )
            except Exception as exc:  # pragma: no cover - network side-effects
                self._logger.warning(
                    "Failed to refresh UI for chat %s after language change: %s",
                    chat_id,
                    exc,
                )

        await asyncio.gather(
            *(_refresh(chat_id, game) for chat_id, game in affected_games),
            return_exceptions=True,
        )

    def _lookup_user_by_username(self, username: str) -> Optional[int]:
        """Resolve @username to user_id."""

        key = "username:" + username.lstrip("@").lower()
        user_id = self._kv.get(key)

        if isinstance(user_id, bytes):
            try:
                user_id = user_id.decode("utf-8")
            except Exception:
                return None

        return int(user_id) if user_id else None

    async def _send_response(
        self,
        update: Update,
        message: str,
        reply_to_message_id: Optional[int] = None,
    ) -> None:
        """
        Centralized message sending with consistent interface.
        """

        plain_message = UnicodeTextFormatter.strip_all_html(message)
        effective_message = update.effective_message

        if effective_message is not None:
            await effective_message.reply_text(
                plain_message,
                reply_to_message_id=reply_to_message_id,
            )
            return

        await self._bot.send_message(
            chat_id=update.effective_chat.id,
            text=plain_message,
        )

    @property
    def _min_players(self):
        if self._cfg.DEBUG:
            return 1

        return MIN_PLAYERS

    @staticmethod
    def _game_from_context(context: ContextTypes.DEFAULT_TYPE) -> Game:
        if KEY_CHAT_DATA_GAME not in context.chat_data:
            context.chat_data[KEY_CHAT_DATA_GAME] = Game()
        return context.chat_data[KEY_CHAT_DATA_GAME]

    def _game(self, chat_id: ChatId) -> Game:
        chat_data = self._application.chat_data.get(chat_id)
        if chat_data is None:
            self._application.chat_data[chat_id] = {}
            chat_data = self._application.chat_data[chat_id]

        if KEY_CHAT_DATA_GAME not in chat_data:
            chat_data[KEY_CHAT_DATA_GAME] = Game()

        return chat_data[KEY_CHAT_DATA_GAME]

    def _save_game(
        self,
        chat_id: Optional[ChatId] = None,
        game: Optional[Game] = None,
    ) -> None:
        """Save game to storage.

        Supports two signatures:

        * Legacy: _save_game(chat_id, game)
        * Phase 6: _save_game(game)

        Args:
            chat_id: Optional chat ID (inferred from game if not provided).
            game: Game instance to save.
        """

        if chat_id is None and game is not None:
            logger.debug("Saving game %s (Phase 6 signature)", game.id)
            return

        if chat_id is not None and game is not None:
            chat_data = self._application.chat_data.get(int(chat_id))
            if chat_data is None:
                self._application.chat_data[int(chat_id)] = {}
            chat_data = self._application.chat_data[int(chat_id)]
            chat_data[KEY_CHAT_DATA_GAME] = game
            logger.debug("Saved game %s to chat %s", game.id, chat_id)
            return

        raise ValueError("Invalid _save_game arguments")

    async def get_user_private_game(
        self,
        user_id: int,
    ) -> Optional[Dict[str, object]]:
        """Return lightweight info about a user's private game."""

        user_game_key = ":".join(["user", str(user_id), "private_game"])
        raw_value = self._kv.get(user_game_key)

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        if not raw_value:
            return None

        value_str = str(raw_value)
        game_code: Optional[str] = None
        chat_id: Optional[int] = None
        host_id: Optional[int] = None
        players: List[int] = []

        if value_str.isdigit():
            try:
                chat_id = int(value_str)
                game = self._game(chat_id)
                players = [int(p.user_id) for p in getattr(game, "players", [])]
                if players:
                    host_id = players[0]
            except Exception:
                chat_id = None
        else:
            game_code = value_str
            game_key = ":".join(["private_game", game_code])
            game_json = self._kv.get(game_key)

            if isinstance(game_json, bytes):
                game_json = game_json.decode("utf-8")

            if game_json:
                try:
                    from pokerapp.private_game import PrivateGame

                    private_game = PrivateGame.from_json(game_json)
                    host_id = int(private_game.host_user_id)
                    players = [int(pid) for pid in private_game.players]
                except Exception:
                    pass

        if game_code is None and chat_id is None:
            return None

        return {
            "code": game_code,
            "chat_id": chat_id,
            "host_id": host_id,
            "players": players,
        }

    async def get_active_group_game(
        self,
        chat_id: int,
    ) -> Optional[Dict[str, object]]:
        """Return active group game details for ``chat_id`` if available."""

        try:
            game = self._game(chat_id)
        except Exception:
            return None

        if game is None:
            return None

        if getattr(game, "state", GameState.INITIAL) == GameState.INITIAL and not getattr(
            game, "players", None
        ):
            return None

        players = [int(player.user_id) for player in getattr(game, "players", [])]
        host_id = players[0] if players else None

        return {
            "chat_id": chat_id,
            "host_id": host_id,
            "players": players,
            "state": getattr(game, "state", GameState.INITIAL),
        }

    async def has_pending_invite(self, user_id: int) -> bool:
        """Return ``True`` if the user has pending private game invites."""

        pending_key = "user:" + str(user_id) + ":pending_invites"

        try:
            if hasattr(self._kv, "scard"):
                count = self._kv.scard(pending_key)
                if count:
                    return True
            if hasattr(self._kv, "smembers"):
                members = self._kv.smembers(pending_key)
                if members:
                    return True
        except Exception:
            pass

        try:
            return bool(self._kv.exists(pending_key))
        except Exception:
            return False

    async def _show_game_results(
        self,
        chat_id: str,
        game: Game,
        winners_results: Union[
            Dict[Score, List[Tuple[Player, Cards]]],
            List[Tuple[Player, Cards, Money]],
        ],
    ) -> None:
        """
        Display final game results and distribute winnings.

        Args:
            chat_id: Chat identifier
            game: Completed game instance
            winners_results: Dict mapping scores to list of (Player, Cards)
                tuples
                Format: {score: [(player, hand_cards), ...], ...}
        """

        try:
            normalized_results: Dict[
                Score, List[Tuple[Player, Cards, Optional[Money]]]
            ]

            if isinstance(winners_results, dict):
                normalized_results = {
                    score: [(player, cards, None) for player, cards in players]
                    for score, players in winners_results.items()
                }
            else:
                aggregated: Dict[
                    Score, Dict[int, Tuple[Player, Cards, Money]]
                ] = defaultdict(dict)

                for player, hand_cards, amount in winners_results:
                    try:
                        determine = self._coordinator.winner_determine
                        # type: ignore[attr-defined]
                        score = determine._check_hand_get_score(
                            hand_cards
                        )
                    except Exception:
                        # Fallback: use same score when scoring fails
                        score = 0

                    player_entries = aggregated[score]

                    if player.user_id in player_entries:
                        prev_player, prev_hand, prev_amount = player_entries[
                            player.user_id
                        ]
                        player_entries[player.user_id] = (
                            prev_player,
                            prev_hand,
                            prev_amount + amount,
                        )
                    else:
                        player_entries[player.user_id] = (
                            player,
                            hand_cards,
                            amount,
                        )

                normalized_results = {
                    score: list(entries.values())
                    for score, entries in aggregated.items()
                }

            sorted_scores = sorted(normalized_results.keys(), reverse=True)

            if not sorted_scores:
                await self._bot.send_message(
                    chat_id=int(chat_id),
                    text="🎲 Game ended with no winners (all folded).",
                )
                return

            heading = UnicodeTextFormatter.make_bold("Game Results:")
            lines: List[str] = [f"🏆 {heading}", ""]

            for rank, score in enumerate(sorted_scores, start=1):
                players_with_score = normalized_results[score]
                hand_name = get_combination_name(score)

                if rank == 1:
                    winner_heading = UnicodeTextFormatter.make_bold(
                        f"Winner(s) - {hand_name}"
                    )
                    lines.append(f"🥇 {winner_heading}")
                else:
                    lines.append(f"{rank}. {hand_name}")

                for player, hand_cards, winnings in players_with_score:
                    mention_raw = getattr(
                        player,
                        "mention_markdown",
                        f"Player {player.user_id}",
                    )
                    mention_clean = mention_raw.strip("`")
                    if mention_clean.startswith("[") and "](" in mention_clean:
                        label, _link = mention_clean[1:].split("](", 1)
                        mention = label
                    else:
                        mention = mention_clean

                    cards_str = " ".join(str(card) for card in hand_cards[:5])

                    lines.append(f"• {mention}")
                    if cards_str:
                        lines.append(f"  Cards: {cards_str}")
                    if winnings is not None:
                        winnings_display = UnicodeTextFormatter.make_bold(
                            f"Winnings: {winnings}$"
                        )
                        lines.append(f"  {winnings_display}")
                lines.append("")

            total_pot = UnicodeTextFormatter.make_bold(
                f"Total Pot: {game.pot}"
            )
            lines.append(f"💰 {total_pot}")

            result_text = "\n".join(lines).strip()
            await self._bot.send_message(
                chat_id=int(chat_id),
                text=result_text,
            )

            logger.info(
                "Game results sent to chat %s: %d winners",
                chat_id,
                sum(len(players) for players in normalized_results.values()),
            )

        except Exception as exc:
            logger.exception(
                "Error displaying game results for chat %s: %s",
                chat_id,
                exc,
            )

            await self._bot.send_message(
                chat_id=int(chat_id),
                text="❌ Error displaying results. Check logs.",
            )

        finally:
            try:
                chat_key = int(chat_id)
                chat_data = self._application.chat_data.get(chat_key, {})
                if KEY_CHAT_DATA_GAME in chat_data:
                    del chat_data[KEY_CHAT_DATA_GAME]
                    logger.debug("Cleared game state for chat %s", chat_id)
            except Exception as cleanup_exc:
                logger.error(
                    "Failed to cleanup game state for chat %s: %s",
                    chat_id,
                    cleanup_exc,
                )

    @staticmethod
    def _has_available_seat(game: Game) -> bool:
        return len(game.players) < MAX_PLAYERS

    def _get_wallet(self, user_id: UserId) -> 'WalletManagerModel':
        return WalletManagerModel(user_id, self._kv)

    @staticmethod
    def _current_turn_player(game: Game) -> Player:
        i = game.current_player_index % len(game.players)
        return game.players[i]

    def _get_player_name(self, player: Player) -> str:
        """Extract display name from player for action descriptions.

        Args:
            player: Player whose name to extract

        Returns:
            First name or user_id as fallback
        """

        mention = getattr(player, "mention_markdown", None)

        if mention and mention.startswith("[") and "](" in mention:
            try:
                name = mention.split("]")[0][1:]
                if name:
                    return name
            except (IndexError, AttributeError):
                pass

        return f"User {player.user_id}"

    async def ready(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        user = update.effective_user

        if chat is None or user is None or message is None:
            return

        user_language = self._apply_user_language(update)
        user_id = getattr(update.effective_user, "id", None)

        chat_id = chat.id
        game = self._game_from_context(context)

        if chat.type in ("group", "supergroup"):
            if game.state != GameState.INITIAL:
                await self._view.send_message_reply(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=self._translate(
                        "msg.lobby.game_in_progress",
                        user_id=user_id,
                        lang=user_language,
                    ),
                )
                return

            if len(game.players) >= MAX_PLAYERS:
                await self._view.send_message_reply(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=self._translate(
                        "msg.lobby.room_full",
                        user_id=user_id,
                        lang=user_language,
                        max_players=MAX_PLAYERS,
                    ),
                )
                return

            if user.id in game.ready_users:
                await self._view.send_message_reply(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=self._translate(
                        "msg.lobby.already_ready",
                        user_id=user_id,
                        lang=user_language,
                    ),
                )
                return

            wallet = WalletManagerModel(user.id, self._kv)

            if not BalanceValidator.can_afford_table(
                balance=wallet.value(),
                stake_config=STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL],
            ):
                await self._view.send_message_reply(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=self._translate(
                        "msg.lobby.insufficient_funds",
                        user_id=user_id,
                        lang=user_language,
                    ),
                )
                return

            player = Player(
                user_id=user.id,
                mention_markdown=user.mention_markdown(),
                wallet=wallet,
                ready_message_id=message.message_id,
            )

            game.ready_users.add(user.id)
            # Ensure latest details overwrite any stale entry.
            game.players = [p for p in game.players if p.user_id != user.id]
            game.players.append(player)

            display_name = user.first_name or user.full_name or user.username
            if not display_name:
                display_name = str(user.id)

            await self._lobby_manager.add_player(
                chat_id=chat_id,
                user_id=user.id,
                user_name=display_name,
            )

            return

        await self.show_help(update, context)

    async def remove_lobby_player(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        user_id: int,
    ) -> None:
        """Remove player from lobby and internal ready cache."""

        game = self._game_from_context(context)
        game.ready_users.discard(user_id)
        game.players = [p for p in game.players if p.user_id != user_id]

        await self._lobby_manager.remove_player(chat_id, user_id)

    async def stop(self, user_id: UserId) -> None:
        UserPrivateChatModel(user_id=user_id, kv=self._kv).delete()

    async def start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message

        user = update.effective_user

        if user is not None:
            self._track_user(user.id, getattr(user, "username", None))

        if chat is None or message is None:
            return

        user_language = self._apply_user_language(update)

        chat_id = chat.id
        game = self._game_from_context(context)
        user_id = getattr(user, "id", None)

        if game.state not in (GameState.INITIAL, GameState.FINISHED):
            await self._view.send_message(
                chat_id=chat_id,
                text=self._translate(
                    "msg.game_in_progress",
                    user_id=user_id,
                    lang=user_language,
                ),
            )
            return

        if chat.type in ("group", "supergroup"):
            seated_players = self._lobby_manager.get_seated_players(chat_id)
            if seated_players and len(seated_players) >= self._min_players:
                await self._start_game_from_lobby(
                    update=update,
                    context=context,
                    chat_id=chat_id,
                    player_ids=list(seated_players),
                )
                return

            await self._view.send_message(
                chat_id=chat_id,
                text=self._translate(
                    "msg.error.not_enough_players_ready",
                    user_id=user_id,
                    lang=user_language,
                ),
            )
            return

        await self.show_help(update, context)

    async def _start_game_from_lobby(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        player_ids: List[int],
    ) -> None:
        """Initialize a group game using players from the lobby."""

        user_language = self._apply_user_language(update)

        cache = RequestCache()

        try:
            game = self._game_from_context(context)
            valid_players: List[Player] = []
            missing_funds: List[str] = []

            for user_id in player_ids:
                wallet = await cache.get_wallet(
                    user_id,
                    self._kv,
                    self._logger,
                )
                balance_ok = BalanceValidator.can_afford_table(
                    balance=wallet.value(),
                    stake_config=STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL],
                )

                try:
                    member = await self._bot.get_chat_member(chat_id, user_id)
                    member_user = getattr(member, "user", None)
                except Exception as exc:  # pragma: no cover - Telegram API
                    self._logger.error(
                        "Failed to fetch chat member %s in %s: %s",
                        user_id,
                        chat_id,
                        exc,
                    )
                    member_user = None

                cached_display_name = cache.get_username(user_id)
                display_name = (
                    cached_display_name
                    or getattr(member_user, "first_name", None)
                    or getattr(member_user, "full_name", None)
                    or getattr(member_user, "username", None)
                    or str(user_id)
                )

                cache.cache_username(user_id, display_name)

                if not balance_ok:
                    missing_funds.append(display_name)
                    continue

                mention = (
                    member_user.mention_markdown()
                    if member_user is not None
                    else f"User {user_id}"
                )

                valid_players.append(
                    Player(
                        user_id=user_id,
                        mention_markdown=mention,
                        wallet=wallet,
                        ready_message_id=None,
                    )
                )

            if missing_funds:
                await self._view.send_message(
                    chat_id=chat_id,
                    text=self._translate(
                        "msg.error.players_insufficient_funds",
                        user_id=user_id,
                        lang=user_language,
                        players=", ".join(missing_funds),
                    ),
                )
                return

            if len(valid_players) < self._min_players:
                await self._view.send_message(
                    chat_id=chat_id,
                    text=self._translate(
                        "msg.error.not_enough_players_ready",
                        user_id=user_id,
                        lang=user_language,
                    ),
                )
                return

            game.reset()
            game.players = valid_players
            game.ready_users = {player.user_id for player in valid_players}
            game.stake_config = self._stake_config

            await self._lobby_manager.delete_lobby(chat_id)

            await self._start_game(context=context, game=game, chat_id=chat_id)
        finally:
            cache.log_stats("GroupGameStart")

    async def show_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_language = self._apply_user_language(update)
        user_id = getattr(update.effective_user, "id", None)

        chat_id = update.effective_message.chat_id
        try:
            with open(DESCRIPTION_FILE, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            text = self._translate(
                "help.model.fallback",
                user_id=user_id,
                lang=user_language,
            )

        await self._view.send_message(
            chat_id=chat_id,
            text=text,
        )

    async def _start_game(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        game: Game,
        chat_id: ChatId
    ) -> None:
        print(f"new game: {game.id}, players count: {len(game.players)}")

        language_context = getattr(self._view, "language_context", None)
        active_lang = getattr(
            language_context,
            "code",
            translation_manager.DEFAULT_LANGUAGE,
        )
        start_message = self._translate(
            "msg.game_started",
            lang=active_lang,
        )

        await self._view.send_message(
            chat_id=chat_id,
            text=start_message,
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[["poker"]],
                resize_keyboard=True,
            ),
        )

        old_players_ids = context.chat_data.get(KEY_OLD_PLAYERS, [])
        old_players_ids = old_players_ids[-1:] + old_players_ids[:-1]

        def index(ln: List, obj) -> int:
            try:
                return ln.index(obj)
            except ValueError:
                return -1

        game.players.sort(key=lambda p: index(old_players_ids, p.user_id))

        game.state = GameState.ROUND_PRE_FLOP
        await self._coordinator.register_webapp_game(game.id, int(chat_id), game)
        await self._divide_cards(game=game, chat_id=chat_id)

        game.current_player_index = 1
        self._coordinator.apply_pre_flop_blinds(
            game=game,
            small_blind=self._stake_config.small_blind,
            big_blind=self._stake_config.big_blind,
        )

        turn_result, next_player = self._coordinator.process_game_turn(game)

        await self._handle_turn_result(
            game,
            chat_id,
            turn_result,
            next_player,
        )

        context.chat_data[KEY_OLD_PLAYERS] = list(
            map(lambda p: p.user_id, game.players),
        )

    async def bonus(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_language = self._apply_user_language(update)
        user_id = getattr(update.effective_user, "id", None)

        wallet = WalletManagerModel(
            update.effective_message.from_user.id, self._kv)
        money = wallet.value()

        chat_id = update.effective_message.chat_id
        message_id = update.effective_message.message_id

        if wallet.has_daily_bonus():
            await self._view.send_message_reply(
                chat_id=chat_id,
                message_id=message_id,
                text=self._translate(
                    "msg.bonus.already_claimed",
                    user_id=user_id,
                    lang=user_language,
                    amount=money,
                ),
            )
            return

        SATURDAY = 5
        if dt.today().weekday() == SATURDAY:
            dice_msg = await self._view.send_dice_reply(
                chat_id=chat_id,
                message_id=message_id,
                emoji='🎰'
            )
            icon = '🎰'
            bonus_amount = dice_msg.dice.value * 20
        else:
            dice_msg = await self._view.send_dice_reply(
                chat_id=chat_id,
                message_id=message_id,
            )
            dice_value = dice_msg.dice.value
            icon = DICES[dice_value - 1]
            bonus_amount = BONUSES[dice_value - 1]

        message_id = dice_msg.message_id
        money = wallet.add_daily(amount=bonus_amount)

        async def print_bonus() -> None:
            await asyncio.sleep(DICE_DELAY_SEC)
            await self._view.send_message_reply(
                chat_id=chat_id,
                message_id=message_id,
                text=self._translate(
                    "msg.bonus.result",
                    user_id=user_id,
                    lang=user_language,
                    bonus=bonus_amount,
                    icon=icon,
                    total=money,
                ),
            )

        self._application.create_task(print_bonus())

    async def send_cards_to_user(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self._apply_user_language(update)

        game = self._game_from_context(context)

        current_player: Optional[Player] = None
        for player in game.players:
            if player.user_id == update.effective_user.id:
                current_player = player
                break

        if current_player is None or not current_player.cards:
            return

        await self._view.send_or_update_private_hand(
            chat_id=current_player.user_id,
            cards=current_player.cards,
            table_cards=game.cards_table,
            mention_markdown=current_player.mention_markdown,
            disable_notification=False,
            user_id=current_player.user_id,
        )

    async def _check_access(self, chat_id: ChatId, user_id: UserId) -> bool:
        chat_admins = await self._bot.get_chat_administrators(chat_id)
        for m in chat_admins:
            if m.user.id == user_id:
                return True
        return False

    async def _send_cards_batch(
        self,
        players: List[Player],
        chat_id: ChatId,
    ) -> None:
        """Send cards to multiple players concurrently for performance."""

        async def send_to_player(player: Player) -> None:
            private_chat: Optional[UserPrivateChatModel] = None
            private_chat_id: Optional[ChatId] = None
            existing_message_id: Optional[int] = None

            try:
                private_chat = UserPrivateChatModel(
                    user_id=player.user_id,
                    kv=self._kv,
                )
                private_chat_id = private_chat.get_chat_id()

                if isinstance(private_chat_id, bytes):
                    private_chat_id = private_chat_id.decode('utf-8')

                if private_chat_id:
                    existing_message_id_raw = private_chat.pop_message()

                    if existing_message_id_raw is not None:
                        try:
                            if isinstance(existing_message_id_raw, bytes):
                                existing_message_id_raw = (
                                    existing_message_id_raw.decode('utf-8')
                                )
                            existing_message_id = int(existing_message_id_raw)
                        except (TypeError, ValueError):
                            existing_message_id = None
            except Exception as exc:
                logger.warning(
                    "Failed to prepare private chat for %s: %s",
                    player.user_id,
                    exc,
                )

            target_chat_id = private_chat_id or player.user_id
            message_id = existing_message_id if private_chat_id else None

            try:
                new_msg_id = await self._view.send_or_update_private_hand(
                    chat_id=target_chat_id,
                    cards=player.cards,
                    mention_markdown=player.mention_markdown,
                    table_cards=None,
                    message_id=message_id,
                    disable_notification=False,
                    user_id=player.user_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to send cards privately to %s: %s",
                    player.user_id,
                    exc,
                )
                return

                if private_chat_id and private_chat:
                    if new_msg_id is not None:
                        private_chat.push_message(new_msg_id)
                    elif (
                        existing_message_id is not None
                        and message_id is not None
                    ):
                        try:
                            await self._view.remove_message(
                                chat_id=private_chat_id,
                                message_id=existing_message_id,
                            )
                        except Exception as exc:
                            logger.debug(
                                "Failed to remove stale private hand message "
                                "for %s: %s",
                                player.user_id,
                                exc,
                            )

        await asyncio.gather(
            *[send_to_player(player) for player in players],
            return_exceptions=True,
        )

    def _deal_cards_to_players(self, game: Game) -> None:
        """Deal two cards to each player and refresh the deck."""

        deck = get_shuffled_deck()

        for player in game.players:
            player.cards.clear()
            for _ in range(2):
                if deck:
                    player.cards.append(deck.pop())

        game.deck = deck
        game.remain_cards = deck

    async def _send_private_cards_to_all(
        self,
        game: Game,
        destination: Union[ChatId, CallbackContext],
    ) -> None:
        """Send private cards either via chat or direct messages."""

        if self._view is None:
            return

        if isinstance(destination, str) or isinstance(destination, int):
            # Destination is a chat_id - fall back to the legacy
            # reply keyboard flow.
            await self._send_cards_batch(game.players, destination)
            return

        # Destination is likely a CallbackContext; send direct messages.
        for player in game.players:
            try:
                await self._view.send_or_update_private_hand(
                    chat_id=player.user_id,
                    cards=player.cards,
                    table_cards=game.cards_table,
                    mention_markdown=player.mention_markdown,
                    disable_notification=False,
                    footer=f"Table stake: {game.table_stake}",
                    user_id=player.user_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to send cards to %s: %s",
                    player.user_id,
                    exc,
                )

    async def _divide_cards(self, game: Game, chat_id: ChatId) -> None:
        self._deal_cards_to_players(game)

        await self._send_private_cards_to_all(game, chat_id)

        logger.info(
            "Cards distributed to %s players concurrently",
            len(game.players),
        )

    async def _send_live_manager_update(
        self,
        game: Game,
        chat_id: int,
        *,
        current_player: Optional[Player] = None,
    ) -> None:
        """Send the latest game state via LiveMessageManager when available."""

        live_manager = getattr(self._view, "_live_manager", None)
        if live_manager is None:
            return

        resolved_player = self._resolve_live_current_player(
            game,
            current_player,
        )
        if resolved_player is None:
            return

        for index, player in enumerate(game.players):
            if player.user_id == resolved_player.user_id:
                game.current_player_index = index
                break

        try:
            await live_manager.send_or_update_game_state(
                chat_id=chat_id,
                game=game,
                current_player=resolved_player,
            )
        except Exception as exc:  # pragma: no cover - Telegram failures
            self._logger.warning(
                "Failed to update live message for %s: %s",
                resolved_player.user_id,
                exc,
            )

    async def _handle_post_action(self, game: Game, chat_id: int) -> None:
        """Advance turn and react to the resulting state transition."""

        self._coordinator.engine.advance_after_action(game)

        turn_result, next_player = self._coordinator.process_game_turn(game)

        await self._handle_turn_result(
            game,
            chat_id,
            turn_result,
            next_player,
        )

    async def _handle_turn_result(
        self,
        game: Game,
        chat_id: int,
        turn_result: TurnResult,
        next_player: Optional[Player],
        *,
        update_live: bool = True,
    ) -> None:
        """React to the result of processing a turn."""

        if turn_result == TurnResult.CONTINUE_ROUND and next_player:
            game.last_turn_time = dt.now()
            if update_live:
                await self._send_live_manager_update(
                    game,
                    chat_id,
                    current_player=next_player,
                )
        elif turn_result == TurnResult.END_ROUND:
            await self._advance_to_next_street(game, chat_id)
        elif turn_result == TurnResult.END_GAME:
            await self._finish_game(game, chat_id)

    @staticmethod
    def _resolve_live_current_player(
        game: Game, current_player: Optional[Player]
    ) -> Optional[Player]:
        """Determine which player should be highlighted in live updates."""

        if game.state == GameState.FINISHED:
            return None

        if current_player is not None:
            return current_player

        if not game.players:
            return None

        index = game.current_player_index
        if index < 0 or index >= len(game.players):
            return None

        return game.players[index]

    async def _advance_to_next_street(
        self,
        game: Game,
        chat_id: int,
    ) -> None:
        """Handle transition to next betting round."""

        # Move round bets to pot
        self._coordinator.commit_round_bets(game)

        # Advance street
        new_state, cards_to_deal = self._coordinator.advance_game_street(game)

        # If advancing lands on FINISHED, the hand is over and we should
        # immediately settle the game instead of requesting more actions.
        if new_state == GameState.FINISHED:
            await self._finish_game(game, chat_id)
            return

        # Deal community cards if needed and track count
        cards_dealt = 0

        if cards_to_deal > 0:
            for _ in range(cards_to_deal):
                if game.remain_cards:
                    game.cards_table.append(game.remain_cards.pop())
                    cards_dealt += 1

        # ✅ FIX: Update live message immediately if cards were dealt
        # This ensures users see Flop/Turn/River cards as soon as they’re dealt
        if cards_dealt > 0:
            if hasattr(self._view, "invalidate_render_cache"):
                self._view.invalidate_render_cache(game)
            await self._send_live_manager_update(game, chat_id)

        # Check if next street needs action
        turn_result, next_player = self._coordinator.process_game_turn(game)

        await self._handle_turn_result(
            game,
            chat_id,
            turn_result,
            next_player,
            # ✅ FIX: update_live=True highlights the active player while
            # LiveMessageManager debouncing prevents spam
            update_live=True,  # ✅ CHANGED FROM False TO True
        )

    async def _deal_community_cards(
        self,
        *,
        game: Game,
        chat_id: ChatId,
        count: int,
    ) -> None:
        if count <= 0:
            return

        dealt_cards = 0

        for _ in range(count):
            if not game.remain_cards:
                self._logger.debug(
                    "No more cards remaining when attempting to deal to table"
                )
                break

            card = game.remain_cards.pop()
            game.cards_table.append(card)
            dealt_cards += 1
            self._logger.debug("Dealt community card %s", card)

        if dealt_cards == 0:
            return

        if hasattr(self._view, "invalidate_render_cache"):
            self._view.invalidate_render_cache(game)

        await self._send_live_manager_update(game, chat_id)

    async def _finish_game(self, game: Game, chat_id: int) -> None:
        """Finish game using coordinator (REPLACES old _finish)"""

        print(
            "game finished: "
            f"{game.id}, players: {len(game.players)}, pot: {game.pot}"
        )

        winners_results = self._coordinator.finish_game_with_winners(game)

        active_players = game.players_by(
            states=(PlayerState.ACTIVE, PlayerState.ALL_IN)
        )
        only_one_player = len(active_players) == 1

        text = "Game is finished with result:\n\n"
        for player, best_hand, money in winners_results:
            win_hand = " ".join(best_hand)
            text += f"{player.mention_markdown}\nGOT: *{money} $*\n"
            if not only_one_player:
                text += f"With combination of cards\n{win_hand}\n\n"

        text += "/ready to continue"
        await self._view.send_message(chat_id=chat_id, text=text)

        # Approve wallet transactions
        for winner, _hand, amount in winners_results:
            logger.debug(
                "Winner %s takes %s from pot %s",
                winner.user_id,
                amount,
                game.pot,
            )

        for player in game.players:
            player.wallet.approve(game.id)

        game.reset()

    def middleware_user_turn(
        self,
        fn: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
    ) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
        async def m(update: Update, context: ContextTypes.DEFAULT_TYPE):
            game = self._game_from_context(context)
            if game.state == GameState.INITIAL:
                return

            if update.callback_query is None:
                return

            current_player = self._current_turn_player(game)
            current_user_id = update.callback_query.from_user.id
            if str(current_user_id) != str(current_player.user_id):
                return

            try:
                await self._view.remove_markup(
                    chat_id=update.effective_message.chat_id,
                    message_id=update.effective_message.message_id,
                )
            except Exception as exc:  # pragma: no cover - Telegram failures
                self._logger.warning(
                    "Failed to clear markup for user turn: %s", exc
                )

            await fn(update, context)

        return m

    async def ban_player(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._apply_user_language(update)

        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id

        if game.state in (GameState.INITIAL, GameState.FINISHED):
            return

        diff = dt.now() - game.last_turn_time
        if diff < MAX_TIME_FOR_TURN:
            await self._view.send_message(
                chat_id=chat_id,
                text="You can't ban. Max turn time is 2 minutes",
            )
            return

        await self._view.send_message(
            chat_id=chat_id,
            text="Time is over!",
        )
        await self.fold(update, context)

    async def fold(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._apply_user_language(update)

        game = self._game_from_context(context)
        player = self._current_turn_player(game)

        player.state = PlayerState.FOLD

        player_name = self._get_player_name(player)
        game.add_action(f"{player_name} folded")

        await self._view.send_message(
            chat_id=update.effective_message.chat_id,
            text=f"{player.mention_markdown} {PlayerAction.FOLD.value}"
        )

        chat_id = update.effective_message.chat_id
        await self._handle_post_action(game, chat_id)

    async def call_or_check(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        self._apply_user_language(update)

        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        player_name = self._get_player_name(player)

        action = PlayerAction.CALL.value
        if player.round_rate == game.max_round_rate:
            action = PlayerAction.CHECK.value

        try:
            amount = game.max_round_rate - player.round_rate
            if player.wallet.value() <= amount:
                await self.all_in(update=update, context=context)
                return

            mention_markdown = self._current_turn_player(game).mention_markdown
            await self._view.send_message(
                chat_id=chat_id,
                text=f"{mention_markdown} {action}"
            )
            call_amount = self._coordinator.player_call_or_check(game, player)

            if action == PlayerAction.CHECK.value:
                action_text = f"{player_name} checked"
            else:
                action_text = f"{player_name} called ${call_amount}"

            game.add_action(action_text)
        except UserException as e:
            await self._view.send_message(chat_id=chat_id, text=str(e))
            return
        await self._handle_post_action(game, chat_id)

    async def raise_rate_bet(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        raise_bet_rate: PlayerAction
    ) -> None:
        self._apply_user_language(update)

        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        player_name = self._get_player_name(player)

        try:
            action = PlayerAction.RAISE_RATE
            if player.round_rate == game.max_round_rate:
                action = PlayerAction.BET

            call_amount = max(game.max_round_rate - player.round_rate, 0)
            total_required = call_amount + raise_bet_rate.value
            target_amount = game.max_round_rate + raise_bet_rate.value

            if player.wallet.value() < total_required:
                await self.all_in(update=update, context=context)
                return

            await self._view.send_message(
                chat_id=chat_id,
                text=player.mention_markdown +
                f" {action.value} {raise_bet_rate.value}$"
            )

            self._coordinator.player_raise_bet(
                game=game,
                player=player,
                amount=target_amount,
            )
            game.add_action(f"{player_name} raised to ${target_amount}")
        except UserException as e:
            await self._view.send_message(chat_id=chat_id, text=str(e))
            return
        await self._handle_post_action(game, chat_id)

    async def all_in(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        self._apply_user_language(update)

        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        player_name = self._get_player_name(player)
        mention = player.mention_markdown
        amount = self._coordinator.player_all_in(game, player)
        await self._view.send_message(
            chat_id=chat_id,
            text=f"{mention} {PlayerAction.ALL_IN.value} {amount}$"
        )
        player.state = PlayerState.ALL_IN
        game.add_action(f"{player_name} went ALL-IN (${amount})")
        await self._handle_post_action(game, chat_id)

    async def create_private_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """
        Start private game creation flow - show stake selection menu.

        This is the entry point when user types /private command.
        After stake selection, create_private_game_with_stake() is called.
        """
        user_language = self._apply_user_language(update)

        user = update.effective_message.from_user
        chat_id = update.effective_chat.id

        self._track_user(user.id, getattr(user, "username", None))

        # Check if user already has an active private game
        existing_game_key = ":".join(["user", str(user.id), "private_game"])
        if self._kv.exists(existing_game_key):
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.already_active",
                    user_id=user.id,
                    lang=user_language,
                ),
            )
            return

        # Show stake selection menu
        await self._view.send_stake_selection(
            chat_id=chat_id,
            user_name=user.full_name,
            language_code=user_language,
        )

    async def create_private_game_with_stake(
        self,
        update: Update,
        context: CallbackContext,
        stake_level: str,
    ) -> None:
        """
        Create private game after user selects stake level from button.

        Args:
            update: Telegram update from callback query
            context: Callback context
            stake_level: Selected stake level ("low", "medium", "high")
        """
        user_language = self._apply_user_language(update)

        from pokerapp.private_game import PrivateGame, PrivateGameState

        query = update.callback_query
        user = query.from_user
        self._track_user(user.id, user.username)
        # Validate stake level
        stake_config = self._cfg.PRIVATE_STAKES.get(stake_level)
        if not stake_config:
            error_text = self._translate(
                "msg.private.error.invalid_stake",
                user_id=user.id,
                lang=user_language,
                level=stake_level,
            )
            await query.edit_message_text(error_text)
            return

        # Check user balance
        wallet = self._get_wallet(user.id)
        min_buyin = stake_config["min_buyin"]

        if not await self._ensure_minimum_balance(
            update, user.id, wallet, min_buyin
        ):
            return

        # Generate unique 6-character game code
        game_code = self._generate_game_code()

        # Create private game instance
        private_game = PrivateGame(
            game_code=game_code,
            host_user_id=user.id,
            stake_level=stake_level,
            state=PrivateGameState.LOBBY,
            players=[user.id],
        )

        # Store in Redis
        game_key = ":".join(["private_game", game_code])
        self._kv.set(
            game_key,
            private_game.to_json(),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Link user to game
        user_game_key = ":".join(["user", str(user.id), "private_game"])
        self._kv.set(
            user_game_key,
            game_code,
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Show game created confirmation with lobby status
        message = self._translate(
            "msg.private.created",
            user_id=user.id,
            lang=user_language,
            code=game_code,
            stake=stake_config["name"],
            min_buyin=min_buyin,
            max_buyin=stake_config["max_buyin"],
            host=user.full_name,
            max_players=self._cfg.PRIVATE_MAX_PLAYERS,
        )
        plain_message = UnicodeTextFormatter.strip_all_html(message)
        await query.edit_message_text(
            text=plain_message,
        )

    async def accept_private_game_invite(
        self,
        update: Update,
        context: CallbackContext,
        game_code: str,
    ) -> None:
        """
        Accept a private game invitation from inline button.

        Args:
            update: Telegram update from callback query
            context: Callback context
            game_code: Game code from callback data
        """
        self._apply_user_language(update)

        from pokerapp.private_game import PrivateGame, PrivateGameState

        query = update.callback_query
        user = query.from_user

        self._track_user(user.id, getattr(user, "username", None))
        player_handle = getattr(user, "username", None)
        if player_handle:
            player_display = f"@{player_handle}"
        else:
            player_display = getattr(user, "full_name", None) or getattr(
                user, "first_name", None
            )
            if not player_display:
                player_display = str(user.id)

        # Load game from Redis
        lobby_key = ":".join(["private_game", game_code])
        game_data = self._kv.get(lobby_key)

        if not game_data:
            try:
                message = self._translate(
                    "msg.private.error.game_not_found",
                    user_id=user.id,
                )
                await NotificationManager.popup(
                    query,
                    text=message,
                    show_alert=True,
                    event="ModelPopup",
                )
                logger.info(
                    "💬 Popup sent to user %s: %s",
                    getattr(user, "id", "?"),
                    message,
                )
            except TelegramError as exc:
                logger.warning(
                    "⚠️ Popup failed for user %s: %s",
                    getattr(user, "id", "?"),
                    exc,
                )
            return

        if isinstance(game_data, bytes):
            game_data = game_data.decode('utf-8')

        private_game = PrivateGame.from_json(game_data)

        # Check if user is invited
        if user.id not in private_game.invited_players:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.not_invited",
                    user_id=user.id,
                )
            )
            return

        # Check if already accepted
        invite = private_game.invited_players[user.id]
        if invite.accepted:
            await query.edit_message_text(
                self._translate(
                    "msg.private.invite.already_accepted",
                    user_id=user.id,
                )
            )
            return

        # Check if game is still accepting players
        if private_game.state != PrivateGameState.LOBBY:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.already_started",
                    user_id=user.id,
                )
            )
            return

        # Check user balance
        stake_config = self._cfg.PRIVATE_STAKES.get(private_game.stake_level)
        if not stake_config:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.stake_missing",
                    user_id=user.id,
                )
            )
            return
        wallet = self._get_wallet(user.id)
        min_balance = stake_config["min_buyin"]

        if not await self._ensure_minimum_balance(
            update,
            user.id,
            wallet,
            min_balance,
        ):
            return

        # Accept invitation
        invite.accepted = True
        invite.accepted_at = int(asyncio.get_event_loop().time())

        # Save updated game
        self._kv.set(
            lobby_key,
            private_game.to_json(),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Link user to game
        user_game_key = ":".join(["user", str(user.id), "private_game"])
        self._kv.set(
            user_game_key,
            game_code,
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Update invitation message
        acceptance_text = self._translate(
            "msg.private.invite.accepted",
            user_id=user.id,
            code=game_code,
            stake=stake_config["name"],
        )
        waiting_text = self._translate(
            "msg.lobby.waiting",
            user_id=user.id,
        )

        await query.edit_message_text(
            text=f"{acceptance_text}\n\n{waiting_text}",
        )

        # Notify host
        player_handle = getattr(user, "username", None)
        if player_handle:
            player_display = f"@{player_handle}"
        else:
            player_display = getattr(user, "full_name", None) or getattr(
                user, "first_name", None
            )
            if not player_display:
                player_display = str(user.id)
        await context.bot.send_message(
            chat_id=private_game.host_user_id,
            text=self._translate(
                "msg.private.invite.accepted_host",
                user_id=private_game.host_user_id,
                player=player_display,
            )
        )

    async def decline_private_game_invite(
        self,
        update: Update,
        context: CallbackContext,
        game_code: str,
    ) -> None:
        """
        Decline a private game invitation from inline button.

        Args:
            update: Telegram update from callback query
            context: Callback context
            game_code: Game code from callback data
        """
        self._apply_user_language(update)

        from pokerapp.private_game import PrivateGame

        query = update.callback_query
        user = query.from_user

        self._track_user(user.id, getattr(user, "username", None))

        # Load game from Redis
        game_key = ":".join(["private_game", game_code])
        game_data = self._kv.get(game_key)

        if not game_data:
            try:
                message = self._translate(
                    "msg.private.error.game_not_found",
                    user_id=user.id,
                )
                await NotificationManager.popup(
                    query,
                    text=message,
                    show_alert=True,
                    event="ModelPopup",
                )
                logger.info(
                    "💬 Popup sent to user %s: %s",
                    getattr(user, "id", "?"),
                    message,
                )
            except TelegramError as exc:
                logger.warning(
                    "⚠️ Popup failed for user %s: %s",
                    getattr(user, "id", "?"),
                    exc,
                )
            return

        if isinstance(game_data, bytes):
            game_data = game_data.decode('utf-8')

        private_game = PrivateGame.from_json(game_data)

        # Check if user is invited
        if user.id not in private_game.invited_players:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.not_invited",
                    user_id=user.id,
                )
            )
            return

        # Remove invitation
        del private_game.invited_players[user.id]

        # Save updated game
        self._kv.set(
            game_key,
            private_game.to_json(),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Update invitation message
        message = self._translate(
            "msg.private.invite.declined_with_hint",
            user_id=user.id,
            code=game_code,
        )
        await query.edit_message_text(
            text=message,
        )

        # Notify host
        await context.bot.send_message(
            chat_id=private_game.host_user_id,
            text=self._translate(
                "msg.private.invite.declined_host",
                user_id=private_game.host_user_id,
                player=player_display,
            )
        )

    async def _get_user_balance(self, user_id: int) -> int:
        """Fetch a user's wallet balance using the wallet manager keys."""

        try:
            wallet = await WalletManagerModel.load(user_id, self._kv, logger)
            return wallet.value()
        except (ValueError, TypeError, redis.RedisError):
            logger.exception("Failed to load wallet for user %s", user_id)
            return getattr(self._cfg, "INITIAL_MONEY", DEFAULT_MONEY)

    async def join_private_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /join <code> command to join private game lobby."""

        user_language = self._apply_user_language(update)

        user = update.effective_user
        self._track_user(user.id, user.username)
        user_id = user.id
        chat_id = update.effective_message.chat_id

        if not context.args or len(context.args) != 1:
            await self._send_response(
                update,
                self._translate(
                    ModelTextKeys.JOIN_USAGE,
                    lang=user_language,
                    user_id=user_id,
                ),
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        game_code = context.args[0].upper()

        is_valid, error_msg = self._validate_game_code(
            game_code,
            lang=user_language,
            user_id=user_id,
        )
        if not is_valid:
            await self._send_response(
                update,
                error_msg,
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        lobby_key = ":".join(["private_game", game_code])
        game_chat_id = self._kv.get(lobby_key)

        if isinstance(game_chat_id, bytes):
            game_chat_id = game_chat_id.decode("utf-8")

        if not game_chat_id:
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=self._translate(
                    ModelTextKeys.JOIN_GAME_NOT_FOUND,
                    lang=user_language,
                    user_id=user_id,
                    code=game_code,
                ),
                message_id=update.effective_message.message_id,
            )
            return

        try:
            game_chat_id = int(game_chat_id)
        except (TypeError, ValueError):
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=self._translate(
                    ModelTextKeys.JOIN_GAME_NOT_FOUND,
                    lang=user_language,
                    user_id=user_id,
                    code=game_code,
                ),
                message_id=update.effective_message.message_id,
            )
            return

        game = self._game(game_chat_id)

        if game.state != GameState.INITIAL:
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=self._translate(
                    ModelTextKeys.JOIN_GAME_STARTED,
                    lang=user_language,
                    user_id=user_id,
                    code=game_code,
                ),
                message_id=update.effective_message.message_id,
            )
            return

        if any(p.user_id == user_id for p in game.players):
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=self._translate(
                    ModelTextKeys.JOIN_ALREADY_IN_GAME,
                    lang=user_language,
                    user_id=user_id,
                    code=game_code,
                ),
                message_id=update.effective_message.message_id,
            )
            return

        if not self._has_available_seat(game):
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=self._translate(
                    ModelTextKeys.JOIN_GAME_FULL,
                    lang=user_language,
                    user_id=user_id,
                    code=game_code,
                    max_players=MAX_PLAYERS,
                ),
                message_id=update.effective_message.message_id,
            )
            return

        stake_config = game.stake_config

        if stake_config is None:
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=self._translate(
                    ModelTextKeys.JOIN_NOT_ACCEPTING,
                    lang=user_language,
                    user_id=user_id,
                    code=game_code,
                ),
                message_id=update.effective_message.message_id,
            )
            return

        wallet = self._get_wallet(user_id)
        min_balance = stake_config.min_buy_in

        if not await self._ensure_minimum_balance(
            update,
            user_id,
            wallet,
            min_balance,
            reply_to_message_id=update.effective_message.message_id,
        ):
            return

        try:
            user_chat = await context.bot.get_chat(user_id)
            username = getattr(user_chat, "username", None)
            first_name = getattr(user_chat, "first_name", None)
            display_name = username or first_name or f"User{user_id}"
            if username:
                mention = f"@{username}"
            else:
                mention = "[{}](tg://user?id={})".format(
                    escape_markdown(display_name, version=1),
                    user_id,
                )
        except Exception:
            display_name = f"User{user_id}"
            mention = "[{}](tg://user?id={})".format(
                escape_markdown(display_name, version=1),
                user_id,
            )

        player = Player(
            user_id=user_id,
            mention_markdown=mention,
            wallet=wallet,
            ready_message_id=None,
        )

        game.players.append(player)

        self._save_game(game_chat_id, game)

        user_game_key = ":".join(["user", str(user_id), "private_game"])
        self._kv.set(user_game_key, game_chat_id)

        await self._view.send_message_reply(
            chat_id=chat_id,
            text=self._translate(
                ModelTextKeys.JOIN_SUCCESS,
                lang=user_language,
                user_id=user_id,
                code=game_code,
                stake=stake_config.name,
                players=len(game.players),
                max_players=MAX_PLAYERS,
            ),
            message_id=update.effective_message.message_id,
        )

        lobby_text = (
            f"🎉 {mention} joined the game!\n\n"
            f"👥 Current players ({len(game.players)}/{MAX_PLAYERS})"
            ":\n"
        )

        for idx, player_entry in enumerate(game.players, 1):
            lobby_text += f"{idx}. {player_entry.mention_markdown}"
            if idx == 1:
                lobby_text += " 👑 (Host)"
            lobby_text += "\n"

        try:
            await self._view.send_message(
                chat_id=game_chat_id,
                text=lobby_text,
            )
        except Exception as exc:
            logger.warning(
                "Failed to notify lobby %s about new player: %s",
                game_chat_id,
                exc,
            )

    async def invite_player(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Host invites player to their private game.

        Usage: /invite @username
        """

        self._apply_user_language(update)

        user = update.effective_user
        user_id = getattr(user, "id", None)
        self._track_user(user.id, getattr(user, "username", None))

        # Check if user has active private game
        user_game_key = "user:" + str(user.id) + ":private_game"
        game_code = self._kv.get(user_game_key)

        if isinstance(game_code, bytes):
            game_code = game_code.decode("utf-8")

        if not game_code:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.no_active_game",
                    user_id=user_id,
                ),
            )
            return

        # Parse target username
        if not context.args:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.missing_player",
                    user_id=user_id,
                ),
            )
            return

        target_username = context.args[0].lstrip("@")

        if not target_username:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.missing_player",
                    user_id=user_id,
                ),
            )
            return

        target_user_id = self._lookup_user_by_username(target_username)

        if not target_user_id:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.user_not_found",
                    user_id=user_id,
                    username=f"@{target_username}",
                ),
            )
            return

        # Check if already in game
        target_game_key = "user:" + str(target_user_id) + ":private_game"
        if self._kv.get(target_game_key):
            await self._send_response(
                update,
                self._translate(
                    "model.error.already_in_game_other",
                    user_id=user.id if user else None,
                    player=f"@{target_username}",
                ),
            )
            return

        # Load game data
        game_key = "private_game:" + str(game_code)
        game_json = self._kv.get(game_key)

        if isinstance(game_json, bytes):
            game_json = game_json.decode("utf-8")

        if not game_json:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.game_not_found",
                    user_id=user_id,
                ),
            )
            return

        game_data = json.loads(game_json)
        stake_level = game_data.get("stake_level")

        try:
            stake_config = self._cfg.PRIVATE_STAKES[stake_level]
        except KeyError:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.game_misconfigured",
                    user_id=user_id,
                ),
            )
            return

        # Store invitation
        invite_key = "invite:" + str(game_code) + ":" + str(target_user_id)
        invite_data = {
            "host_id": user.id,
            "host_name": user.first_name,
            "stake_level": stake_level,
            "invited_at": dt.now().isoformat(),
            "status": "pending",
        }
        self._kv.set(
            invite_key,
            json.dumps(invite_data),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Track pending invite
        pending_key = "user:" + str(target_user_id) + ":pending_invites"
        try:
            self._kv.sadd(pending_key, game_code)
            self._kv.expire(pending_key, self._cfg.PRIVATE_GAME_TTL_SECONDS)
        except Exception as exc:
            logger.debug(
                "Failed to track pending invite for %s/%s: %s",
                target_user_id,
                game_code,
                exc,
            )

        # Send invitation to player
        message, keyboard = self._view.build_invitation_message(
            host_name=user.first_name,
            game_code=game_code,
            stake_config=stake_config,
        )

        try:
            plain_message = UnicodeTextFormatter.strip_all_html(message)
            await self._bot.send_message(
                chat_id=target_user_id,
                text=plain_message,
                reply_markup=keyboard,
            )
            await self._send_response(
                update,
                self._translate(
                    "msg.private.invite.sent",
                    user_id=user_id,
                    username=f"@{target_username}",
                ),
            )
        except Exception as exc:
            logger.error("Failed to send invitation: %s", exc)
            await self._send_response(
                update,
                self._translate(
                    "msg.private.invite.send_failed",
                    user_id=user_id,
                    username=f"@{target_username}",
                ),
            )

    async def start_private_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Start a private game after validating lobby requirements."""

        from pokerapp.private_game import PrivateGame, PrivateGameState

        self._apply_user_language(update)

        cache = RequestCache()

        def _log_and_return():
            cache.log_stats("PrivateGameStart")
            return

        user = update.effective_user
        user_id = getattr(user, "id", None)
        message = update.effective_message
        reply_to_id = getattr(message, "message_id", None)
        chat_id = update.effective_chat.id

        # Get game code from user's active game
        user_game_key = ":".join(["user", str(user.id), "private_game"])
        game_code = self._kv.get(user_game_key)

        if not game_code:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.no_active_game",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        if isinstance(game_code, bytes):
            game_code = game_code.decode("utf-8")

        # Load game from Redis
        lobby_key = ":".join(["private_game", game_code])
        game_json = self._kv.get(lobby_key)

        if isinstance(game_json, bytes):
            game_json = game_json.decode("utf-8")

        if not game_json:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.game_expired",
                    user_id=user_id,
                    code=game_code,
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        private_game = PrivateGame.from_json(game_json)

        # Validate caller is host
        if user.id != private_game.host_user_id:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.host_only_start",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        # Validate game state
        if private_game.state != PrivateGameState.LOBBY:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.already_started",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        # Collect accepted players
        accepted_players = [
            player_id
            for player_id, invite in private_game.invited_players.items()
            if invite.accepted
        ]

        # Always include host
        if private_game.host_user_id not in accepted_players:
            accepted_players.insert(0, private_game.host_user_id)

        # Validate minimum players (2+)
        if len(accepted_players) < self._cfg.PRIVATE_MIN_PLAYERS:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.min_players",
                    user_id=user_id,
                    min_players=self._cfg.PRIVATE_MIN_PLAYERS,
                    current=len(accepted_players),
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        # Validate maximum players (prevent overflow)
        if len(accepted_players) > self._cfg.PRIVATE_MAX_PLAYERS:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.too_many_players",
                    user_id=user_id,
                    max_players=self._cfg.PRIVATE_MAX_PLAYERS,
                    current=len(accepted_players),
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        # Get stake configuration
        stake_config = self._cfg.PRIVATE_STAKES.get(private_game.stake_level)

        if not stake_config:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.stake_missing",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return _log_and_return()

        small_blind = int(stake_config["small_blind"])
        big_blind = int(stake_config["big_blind"])
        min_buyin = int(stake_config["min_buyin"])
        minimum_required = max(
            min_buyin,
            big_blind * self._cfg.MINIMUM_BALANCE_MULTIPLIER,
        )

        # Validate ALL players have sufficient balance
        insufficient_players = []
        wallet_map: Dict[int, Wallet] = {}

        for player_id in accepted_players:
            try:
                wallet = await cache.get_wallet(
                    player_id,
                    self._kv,
                    logger,
                )
                wallet_map[player_id] = wallet
                balance = wallet.value()
            except Exception as exc:
                logger.exception(
                    "Failed to load wallet for user %s during private start: %s",
                    player_id,
                    exc,
                )
                wallet_map[player_id] = None
                balance = 0

            if balance < minimum_required:
                cached_name = cache.get_username(player_id) or self._username_cache.get(
                    player_id
                )
                display_name = (
                    cached_name if cached_name else f"Player {player_id}"
                )
                insufficient_players.append((player_id, display_name, balance))

        # If any player lacks funds, reject start
        if insufficient_players:
            required_display = format(minimum_required, ",")
            error_lines = [
                self._translate(
                    "msg.private.error.insufficient_funds_header",
                    user_id=user_id,
                )
            ]

            for player_id, name, balance in insufficient_players:
                balance_display = format(balance, ",")
                error_lines.append(
                    self._translate(
                        "msg.private.error.insufficient_funds_line",
                        user_id=user_id,
                        player=name,
                        balance=balance_display,
                        required=required_display,
                    )
                )

            error_lines.append(
                self._translate(
                    "msg.private.error.insufficient_funds_footer",
                    user_id=user_id,
                    required=required_display,
                )
            )

            await self._send_response(
                update,
                "\n".join(error_lines),
                reply_to_message_id=reply_to_id,
            )

            # Clean up lobby immediately (don't wait for TTL)
            keys_deleted = self._kv.delete(lobby_key)

            for pid in accepted_players:
                user_game_key = ":".join(
                    ["user", str(pid), "private_game"]
                )
                keys_deleted += self._kv.delete(user_game_key)

            for pid in accepted_players:
                if pid != private_game.host_user_id:
                    invite_key = ":".join(
                        ["private_invite", str(pid), game_code]
                    )
                    keys_deleted += self._kv.delete(invite_key)

            logger.info(
                "Cleaned up failed lobby %s (%d keys deleted)",
                game_code,
                keys_deleted,
            )
            return _log_and_return()

        missing_wallet_ids = [
            pid for pid, wallet in wallet_map.items() if wallet is None
        ]

        if missing_wallet_ids:
            fetched_wallets = await asyncio.gather(
                *[
                    cache.get_wallet(pid, self._kv, logger)
                    for pid in missing_wallet_ids
                ]
            )

            for pid, wallet in zip(missing_wallet_ids, fetched_wallets):
                wallet_map[pid] = wallet

        # Re-fetch lobby to ensure no concurrent modifications
        current_json = self._kv.get(lobby_key)

        if isinstance(current_json, bytes):
            current_json = current_json.decode("utf-8")

        if current_json != game_json:
            logger.warning(
                "Lobby state changed during validation for game %s "
                "(players may have joined/left)",
                game_code,
            )
            await self._view.send_message(
                chat_id=chat_id,
                text=self._translate(
                    "msg.private.error.lobby_changed",
                    user_id=user_id,
                ),
            )
            return _log_and_return()

        # === STEP 2A: CREATE PLAYER OBJECTS ===

        players: List[Player] = []
        player_names: List[str] = []

        # Helper to resolve display names (reuse username cache)
        async def resolve_display_name(player_id: int) -> str:
            """Get cached username or fetch from Telegram."""

            cached_name = cache.get_username(player_id)
            if cached_name:
                return cached_name

            cached_name = self._username_cache.get(player_id)
            if cached_name:
                cache.cache_username(player_id, cached_name)
                return cached_name

            if player_id == user.id:
                name = (
                    getattr(user, "full_name", None)
                    or getattr(user, "username", None)
                    or str(player_id)
                )
                self._username_cache[player_id] = name
                cache.cache_username(player_id, name)
                return name

            # Fallback: Fetch from Telegram API
            try:
                member = await self._bot.get_chat_member(chat_id, player_id)
                member_user = getattr(member, "user", None)
                name = (
                    getattr(member_user, "full_name", None)
                    or getattr(member_user, "first_name", None)
                    or getattr(member_user, "username", None)
                    or str(player_id)
                )
                self._username_cache[player_id] = name
                cache.cache_username(player_id, name)
                return name
            except Exception as exc:
                logger.warning(
                    "Failed to resolve name for user %s: %s",
                    player_id,
                    exc,
                )
                name = str(player_id)
                cache.cache_username(player_id, name)
                return name

        # Load names in parallel and reuse cached wallets
        name_tasks = [
            resolve_display_name(user_id)
            for user_id in accepted_players
        ]

        player_names = await asyncio.gather(*name_tasks)

        wallets: List[Wallet] = []

        for user_id in accepted_players:
            wallet = wallet_map.get(user_id)

            if wallet is None:
                wallet = await cache.get_wallet(
                    user_id,
                    self._kv,
                    logger,
                )
                wallet_map[user_id] = wallet

            wallets.append(wallet)

        # Create Player objects for all accepted players
        for player_id, wallet, display_name in zip(
            accepted_players, wallets, player_names
        ):
            mention = "[{}](tg://user?id={})".format(
                escape_markdown(display_name, version=1),
                player_id,
            )

            players.append(
                Player(
                    user_id=player_id,
                    mention_markdown=mention,
                    wallet=wallet,
                    ready_message_id=None,  # No ready message in private games
                )
            )

        player_summaries = [
            f"{name} (ID={uid}, balance={player.wallet.value()})"
            for uid, name, player in zip(
                accepted_players,
                player_names,
                players,
            )
        ]

        logger.info(
            "Created %d player objects for game %s: %s",
            len(players),
            game_code,
            ", ".join(player_summaries),
        )

        # === STEP 2B: INITIALIZE GAME OBJECT ===

        # Create game instance
        game = Game()
        game.id = game_code
        game.mode = GameMode.PRIVATE
        game.players = players
        game.state = GameState.ROUND_PRE_FLOP
        game.current_player_index = 1 if len(players) > 1 else 0
        game.max_round_rate = 0
        game.ready_users = set(accepted_players)  # All players pre-ready
        game.stake_config = STAKE_PRESETS.get(private_game.stake_level)

        # Store game in context (runtime memory)
        context.chat_data[KEY_CHAT_DATA_GAME] = game
        context.chat_data[KEY_OLD_PLAYERS] = list(accepted_players)

        cache.cache_game(game.id, game)

        logger.info(
            "Initialized Game object for private game %s "
            "(mode=%s, players=%d)",
            game_code,
            game.mode.value,
            len(players),
        )

        # === STEP 2C: PERSIST GAME STATE TO REDIS ===

        # Create game snapshot for monitoring/recovery
        game_snapshot = {
            "id": game.id,
            "chat_id": chat_id,
            "mode": game.mode.value,
            "state": game.state.name,
            "players": accepted_players,
            "stake_level": private_game.stake_level,
            "small_blind": small_blind,
            "big_blind": big_blind,
            "game_code": game_code,
            "created_at": int(dt.utcnow().timestamp()),
        }

        snapshot_key = ":".join(["game", str(chat_id)])
        self._kv.set(
            snapshot_key,
            json.dumps(game_snapshot),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        await self._coordinator.register_webapp_game(game_code, chat_id, game)

        logger.info(
            "Persisted game snapshot to Redis (key=%s, ttl=%ss)",
            snapshot_key,
            self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # === STEP 2D: CLEANUP LOBBY STATE ===

        keys_deleted = 0

        # Delete lobby key (game has started, lobby no longer needed)
        keys_deleted += self._kv.delete(lobby_key)

        # Delete all player-to-game mappings
        for pid in accepted_players:
            user_game_key = ":".join(["user", str(pid), "private_game"])
            keys_deleted += self._kv.delete(user_game_key)

        # Delete all invitation keys
        for pid in accepted_players:
            if pid != private_game.host_user_id:  # Host has no invitation
                invite_key = ":".join(["private_invite", str(pid), game_code])
                keys_deleted += self._kv.delete(invite_key)

        # Expected deletions: lobby (1) + mappings (n) + invites (n-1) = 2n
        expected_keys = 2 * len(accepted_players)

        logger.info(
            "Cleaned up lobby state for game %s (%d/%d keys deleted)",
            game_code,
            keys_deleted,
            expected_keys,
        )

        # === STEP 3A: SEND GAME START NOTIFICATION ===

        intro_heading = UnicodeTextFormatter.make_bold("Game Starting!")
        players_heading = UnicodeTextFormatter.make_bold(
            f"Players ({len(players)})"
        )
        stakes_heading = UnicodeTextFormatter.make_bold("Stakes")
        blinds_heading = UnicodeTextFormatter.make_bold("Blinds")
        players_lines = "\n".join(f"• {name}" for name in player_names)
        if players_lines:
            players_block = f"{players_heading}:\n{players_lines}"
        else:
            players_block = f"{players_heading}:"

        start_message = (
            f"🎮 {intro_heading}\n\n"
            f"{players_block}\n\n"
            f"{stakes_heading}: {stake_config['name']}\n"
            f"{blinds_heading}: {small_blind}/{big_blind}\n\n"
            "Good luck! 🍀"
        )

        await self._view.send_message(
            chat_id=chat_id,
            text=start_message,
        )

        logger.info(
            "Sent game start notification for game %s to chat %s",
            game_code,
            chat_id,
        )

        # === STEP 3B: SEND INDIVIDUAL PLAYER NOTIFICATIONS ===

        for player in players:
            try:
                balance = player.wallet.value()
                personal_message = (
                    f"🎮 {UnicodeTextFormatter.make_bold('Game Started!')}\n\n"
                    f"💰 {UnicodeTextFormatter.make_bold('Your Balance')}: ${format(balance, ',.0f')}\n"
                    f"📊 {UnicodeTextFormatter.make_bold('Blinds')}: {small_blind}/{big_blind}\n\n"
                    "Your cards will be dealt shortly. Good luck! 🍀"
                )
                await self._view.send_message(
                    chat_id=player.user_id,
                    text=personal_message,
                )
                logger.debug(
                    "Sent start notification to player %s (balance: $%d)",
                    player.user_id,
                    balance,
                )
            except Exception as exc:
                # Log but don't fail the game if individual notification fails
                logger.warning(
                    "Failed to send start notification to player %s: %s",
                    player.user_id,
                    exc,
                )

        logger.info(
            "Sent individual start notifications to %d players for game %s",
            len(players),
            game_code,
        )

        # === STEP 3C: INITIALIZE GAME ENGINE ===

        from pokerapp.game_engine import GameEngine

        try:
            # Create engine instance
            engine = GameEngine(
                game_id=game_code,
                chat_id=chat_id,
                players=players,
                small_blind=small_blind,
                big_blind=big_blind,
                kv_store=self._kv,
                view=self._view,
            )

            # Start first hand (deals cards, applies blinds, sets first actor)
            await engine.start_new_hand()

            logger.info(
                "Game engine initialized and first hand started for game %s",
                game_code,
            )

        except Exception as exc:
            logger.error(
                "Failed to initialize game engine for game %s: %s",
                game_code,
                exc,
            )

            # Notify players of failure
            failure_heading = UnicodeTextFormatter.make_bold(
                "Game Failed to Start"
            )
            failure_message = (
                f"❌ {failure_heading}\n\n"
                "An error occurred while initializing the game. "
                "Please try again or contact support."
            )
            await self._view.send_message(
                chat_id=chat_id,
                text=failure_message,
            )

            # Clean up lobby
            await self.delete_private_game_lobby(chat_id, game_code)

            raise  # Re-raise for tracking

        # === STEP 3D: STATE CLEANUP ===

        # Mark game as PLAYING
        game_state_key = ":".join(
            ["private_game", str(chat_id), str(game_code), "state"]
        )
        await self._kv.set(game_state_key, "PLAYING")

        # Clear the lobby (no longer needed)
        await self.delete_private_game_lobby(chat_id, game_code)

        logger.info(
            "Private game %s fully initialized and in PLAYING state",
            game_code,
        )

        cache.log_stats("PrivateGameStart")

    async def delete_private_game_lobby(
        self,
        chat_id: int,
        game_code: Union[str, bytes],
    ) -> None:
        """
        Clean up all Redis keys associated with a private game lobby.

        This is an idempotent helper that removes:
        - Primary lobby key (private_game:{code})
        - User mapping keys (user:{id}:private_game)
        - Pending invite entries (user:{id}:pending_invites set)

        Safe to call multiple times. Handles missing data gracefully.
        Attempts to discover affected players from:
        1. Persisted JSON snapshot (if available)
        2. In-memory game state (fallback)

        Args:
            chat_id: Chat ID where game was hosted (for logging/fallback)
            game_code: 6-character game code (str or bytes)
        """

        # Normalize game_code to string
        if isinstance(game_code, bytes):
            game_code = game_code.decode("utf-8")

        lobby_key = "private_game:" + str(game_code)

        logger.info(
            "Cleaning up private game lobby: chat=%s, code=%s",
            chat_id,
            game_code,
        )

        # === STEP 1: DISCOVER PLAYER IDs ===

        player_ids: List[int] = []

        # Try to load from Redis snapshot
        try:
            lobby_json = self._kv.get(lobby_key)

            if isinstance(lobby_json, bytes):
                lobby_json = lobby_json.decode("utf-8")

            if lobby_json:
                lobby_data = json.loads(lobby_json)
                player_ids = lobby_data.get("players", [])

                # Ensure all IDs are integers
                player_ids = [
                    int(pid)
                    for pid in player_ids
                    if pid is not None
                ]

                logger.debug(
                    "Discovered %d players from lobby JSON: %s",
                    len(player_ids),
                    player_ids,
                )

        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "Failed to parse lobby JSON for %s: %s",
                game_code,
                exc,
            )
            # Continue with fallback discovery

        except Exception as exc:
            logger.warning(
                "Error reading lobby data for %s: %s",
                game_code,
                exc,
            )

        # Fallback: Check in-memory game state
        if not player_ids:
            try:
                chat_data = self._application.chat_data.get(chat_id, {})
                game = chat_data.get(KEY_CHAT_DATA_GAME)

                if game and hasattr(game, "players"):
                    player_ids = [
                        int(p.user_id)
                        for p in game.players
                        if hasattr(p, "user_id")
                    ]

                    logger.debug(
                        "Discovered %d players from in-memory game state",
                        len(player_ids),
                    )

            except Exception as exc:
                logger.warning(
                    "Failed to discover players from game state: %s",
                    exc,
                )

        # === STEP 2: DELETE PRIMARY LOBBY KEY ===

        keys_deleted = 0

        try:
            deleted = self._kv.delete(lobby_key)
            keys_deleted += deleted

            if deleted > 0:
                logger.debug("Deleted primary lobby key: %s", lobby_key)

        except Exception as exc:
            logger.error(
                "Failed to delete lobby key %s: %s",
                lobby_key,
                exc,
            )

        # === STEP 3: DELETE USER MAPPING KEYS ===

        for player_id in player_ids:
            user_game_key = "user:" + str(player_id) + ":private_game"

            try:
                deleted = self._kv.delete(user_game_key)
                keys_deleted += deleted

                if deleted > 0:
                    logger.debug(
                        "Deleted user mapping: %s",
                        user_game_key,
                    )

            except Exception as exc:
                logger.warning(
                    "Failed to delete user mapping %s: %s",
                    user_game_key,
                    exc,
                )
                # Continue cleanup for other players

        # === STEP 4: CLEAR PENDING INVITE SETS ===

        for player_id in player_ids:
            pending_key = "user:" + str(player_id) + ":pending_invites"

            try:
                removed = 0

                # Prefer set removal when available.
                backend = getattr(self._kv, "_backend", None)
                fallback = getattr(self._kv, "_fallback", None)

                srem_candidates = [
                    getattr(self._kv, "srem", None),
                    getattr(backend, "srem", None),
                    getattr(fallback, "srem", None),
                ]

                for candidate in srem_candidates:
                    if callable(candidate):
                        removed = candidate(pending_key, game_code)
                        break

                if removed and removed > 0:
                    logger.debug(
                        "Removed %s from pending invites set: %s",
                        game_code,
                        pending_key,
                    )
                else:
                    # Fallback: Delete entire key (for in-memory KV)
                    deleted = self._kv.delete(pending_key)

                    if deleted > 0:
                        logger.debug(
                            "Deleted pending invites key: %s",
                            pending_key,
                        )

            except Exception as exc:
                logger.warning(
                    "Failed to clear pending invites for user %s: %s",
                    player_id,
                    exc,
                )
                # Continue cleanup

        # === STEP 5: DELETE INDIVIDUAL INVITE KEYS ===

        for player_id in player_ids:
            invite_key = (
                "private_invite:" + str(player_id) + ":" + str(game_code)
            )

            try:
                deleted = self._kv.delete(invite_key)
                keys_deleted += deleted

                if deleted > 0:
                    logger.debug(
                        "Deleted invite key: %s",
                        invite_key,
                    )

            except Exception as exc:
                logger.warning(
                    "Failed to delete invite key %s: %s",
                    invite_key,
                    exc,
                )

        # === FINAL LOGGING ===

        logger.info(
            "Cleanup complete for game %s: "
            "%d keys deleted, %d players affected",
            game_code,
            keys_deleted,
            len(player_ids),
        )

    async def accept_invitation(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Player accepts game invitation via callback button."""

        self._apply_user_language(update)

        query = update.callback_query

        if query is None or not query.data:
            return

        user = query.from_user
        user_id = getattr(user, "id", None)
        self._track_user(user.id, getattr(user, "username", None))

        # Extract game code from callback data
        try:
            game_code = query.data.split(":", 1)[1]
        except IndexError:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.invite_invalid",
                    user_id=user_id,
                )
            )
            return

        # Validate invitation exists
        invite_key = "invite:" + str(game_code) + ":" + str(user.id)
        invite_json = self._kv.get(invite_key)

        if isinstance(invite_json, bytes):
            invite_json = invite_json.decode("utf-8")

        if not invite_json:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.invite_invalid",
                    user_id=user_id,
                )
            )
            return

        invite_data = json.loads(invite_json)
        status = invite_data.get("status", "pending")

        if status != "pending":
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.invite_already_responded",
                    user_id=user_id,
                    status=status,
                )
            )
            return

        # Check balance
        stake_level = invite_data.get("stake_level")

        try:
            stake_config = self._cfg.PRIVATE_STAKES[stake_level]
        except KeyError:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.game_misconfigured",
                    user_id=user_id,
                )
            )
            return

        wallet = self._get_wallet(user.id)
        min_buyin = int(stake_config["min_buyin"])

        if not await self._ensure_minimum_balance(
            update,
            user.id,
            wallet,
            min_buyin,
        ):
            required_chips = format(min_buyin, ",")
            balance_chips = format(wallet.value(), ",")
            await query.edit_message_text(
                self._translate(
                    "model.error.insufficient_chips",
                    user_id=user.id,
                    required=required_chips,
                    balance=balance_chips,
                )
            )
            return

        # Load game
        game_key = "private_game:" + str(game_code)
        game_json = self._kv.get(game_key)

        if isinstance(game_json, bytes):
            game_json = game_json.decode("utf-8")

        if not game_json:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.game_missing",
                    user_id=user_id,
                )
            )
            return

        from pokerapp.private_game import PrivateGame

        game = PrivateGame.from_json(game_json)

        # Check if game is full
        max_players = getattr(self._cfg, "PRIVATE_MAX_PLAYERS", 6)
        if len(game.players) >= max_players:
            await query.edit_message_text(
                self._translate(
                    "model.error.game_full",
                    user_id=user.id,
                    max=max_players,
                )
            )
            return

        # Add player to game
        if user.id not in game.players:
            game.players.append(user.id)
            self._kv.set(
                game_key,
                game.to_json(),
                ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
            )

        # Link user to game
        user_game_key = "user:" + str(user.id) + ":private_game"
        self._kv.set(
            user_game_key,
            game_code,
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Update invitation status
        invite_data["status"] = "accepted"
        self._kv.set(
            invite_key,
            json.dumps(invite_data),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Remove from pending
        pending_key = "user:" + str(user.id) + ":pending_invites"
        try:
            self._kv.srem(pending_key, game_code)
        except Exception:
            pass

        # Update player's message
        stake_name = stake_config.get("name") or str(stake_level).title()
        await query.edit_message_text(
            self._translate(
                "msg.private.invite.accepted",
                user_id=user_id,
                code=game_code,
                stake=stake_name,
            )
        )

        # Notify host
        try:
            host_id = invite_data["host_id"]
            player_handle = getattr(user, "username", None)
            if player_handle:
                player_display = f"@{player_handle}"
            else:
                player_display = getattr(user, "full_name", None) or getattr(
                    user, "first_name", None
                )
                if not player_display:
                    player_display = str(user.id)
            await self._bot.send_message(
                chat_id=host_id,
                text=self._translate(
                    "msg.private.invite.accepted_host",
                    user_id=host_id,
                    player=player_display,
                ),
            )
        except Exception:
            pass

    async def decline_invitation(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Player declines game invitation via callback button."""

        self._apply_user_language(update)

        query = update.callback_query

        if query is None or not query.data:
            return

        user = query.from_user
        user_id = getattr(user, "id", None)
        self._track_user(user.id, getattr(user, "username", None))

        # Extract game code
        try:
            game_code = query.data.split(":", 1)[1]
        except IndexError:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.invite_invalid",
                    user_id=user_id,
                )
            )
            return

        # Validate invitation
        invite_key = "invite:" + str(game_code) + ":" + str(user.id)
        invite_json = self._kv.get(invite_key)

        if isinstance(invite_json, bytes):
            invite_json = invite_json.decode("utf-8")

        if not invite_json:
            await query.edit_message_text(
                self._translate(
                    "msg.private.error.invite_invalid",
                    user_id=user_id,
                )
            )
            return

        invite_data = json.loads(invite_json)

        # Update status
        invite_data["status"] = "declined"
        self._kv.set(
            invite_key,
            json.dumps(invite_data),
            ex=self._cfg.PRIVATE_GAME_TTL_SECONDS,
        )

        # Remove from pending
        pending_key = "user:" + str(user.id) + ":pending_invites"
        try:
            self._kv.srem(pending_key, game_code)
        except Exception:
            pass

        # Update message
        await query.edit_message_text(
            self._translate(
                "msg.private.invite.declined",
                user_id=user_id,
            )
        )

        # Notify host
        try:
            player_handle = getattr(user, "username", None)
            if player_handle:
                player_display = f"@{player_handle}"
            else:
                player_display = getattr(user, "full_name", None) or getattr(
                    user, "first_name", None
                )
                if not player_display:
                    player_display = str(user.id)
            await self._bot.send_message(
                chat_id=invite_data["host_id"],
                text=self._translate(
                    "msg.private.invite.declined_host",
                    user_id=invite_data["host_id"],
                    player=player_display,
                ),
            )
        except Exception:
            pass

    async def leave_private_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle user leaving lobby."""

        self._apply_user_language(update)

        user = update.effective_user
        user_id = user.id
        message = update.effective_message
        reply_to_id = message.message_id if message is not None else None

        user_game_key = ":".join(["user", str(user_id), "private_game"])
        game_chat_id = self._kv.get(user_game_key)

        if isinstance(game_chat_id, bytes):
            game_chat_id = game_chat_id.decode("utf-8")

        if not game_chat_id:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.not_in_game",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return

        try:
            game_chat_id_int = int(game_chat_id)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid private game chat id stored for user %s: %s",
                user_id,
                game_chat_id,
            )
            self._kv.delete(user_game_key)
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.session_missing",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return

        chat_data = self._application.chat_data.get(game_chat_id_int, {})
        game = chat_data.get(KEY_CHAT_DATA_GAME)

        if game is None:
            logger.warning(
                "No game session found for chat %s when user %s "
                "tried to leave",
                game_chat_id_int,
                user_id,
            )
            self._kv.delete(user_game_key)
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.session_missing",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return

        if game.state != GameState.INITIAL:
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.cannot_leave_started",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return

        player_entry = next(
            (
                p
                for p in game.players
                if str(p.user_id) == str(user_id)
            ),
            None,
        )

        if player_entry is None:
            self._kv.delete(user_game_key)
            await self._send_response(
                update,
                self._translate(
                    "msg.private.error.not_in_game",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return

        player_mention = player_entry.mention_markdown
        is_host = (
            bool(game.players)
            and str(game.players[0].user_id) == str(user_id)
        )

        game.players = [
            p for p in game.players if str(p.user_id) != str(user_id)
        ]
        game.ready_users.discard(user_id)

        self._kv.delete(user_game_key)

        if not game.players:
            self._application.chat_data.pop(game_chat_id_int, None)
            game_key = ":".join(["game", str(game_chat_id_int)])
            self._kv.delete(game_key)

            game_code = getattr(game, "code", None)
            if game_code:
                private_game_key = ":".join(["private_game", game_code])
                self._kv.delete(private_game_key)

            logger.info(
                "User %s left and game %s is now empty. Game deleted.",
                user_id,
                game_chat_id_int,
            )

            await self._send_response(
                update,
                self._translate(
                    "msg.private.lobby.leave_confirmation",
                    user_id=user_id,
                ),
                reply_to_message_id=reply_to_id,
            )
            return

        if is_host:
            new_host = game.players[0]
            logger.info(
                "Host %s left private game %s, new host is %s",
                user_id,
                game_chat_id_int,
                new_host.user_id,
            )
        else:
            new_host = None

        self._save_game(game_chat_id_int, game)

        await self._send_response(
            update,
            self._translate(
                "msg.private.lobby.leave_confirmation",
                user_id=user_id,
            ),
            reply_to_message_id=reply_to_id,
        )

        lobby_lines = [
            self._translate(
                "msg.private.lobby.player_left",
                user_id=user_id,
                player=player_mention,
            )
        ]

        if new_host is not None:
            lobby_lines.append(
                self._translate(
                    "msg.private.lobby.new_host",
                    user_id=user_id,
                    player=new_host.mention_markdown,
                )
            )

        lobby_lines.append("")
        lobby_lines.append(
            self._translate(
                "msg.private.lobby.current_players",
                user_id=user_id,
                current=len(game.players),
                max=MAX_PLAYERS,
            )
        )

        host_marker = self._translate(
            "msg.private.lobby.host_marker",
            user_id=user_id,
        )

        for idx, player in enumerate(game.players, 1):
            marker = host_marker if idx == 1 else ""
            lobby_lines.append(
                self._translate(
                    "msg.private.lobby.player_entry",
                    user_id=user_id,
                    index=idx,
                    player=player.mention_markdown,
                    host_marker=marker,
                )
            )

        lobby_text = "\n".join(lobby_lines)

        try:
            await self._view.send_message(
                chat_id=game_chat_id_int,
                text=lobby_text,
            )
        except Exception as exc:
            logger.warning(
                "Failed to notify lobby %s about player %s leaving: %s",
                game_chat_id_int,
                user_id,
                exc,
            )

    async def show_private_game_status(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Show current lobby for caller."""

        self._apply_user_language(update)

        chat = update.effective_chat
        message = update.effective_message
        user = update.effective_user

        if chat is None or message is None or user is None:
            return

        user_id = user.id
        reply_to_id = message.message_id

        user_game_key = ":".join(["user", str(user_id), "private_game"])
        game_code = self._kv.get(user_game_key)

        if isinstance(game_code, bytes):
            game_code = game_code.decode("utf-8")

        if not game_code:
            await self._view.send_message_reply(
                chat_id=chat.id,
                message_id=reply_to_id,
                text="⚠️ You do not have an active private game.",
            )
            return

        game_key = ":".join(["private_game", str(game_code)])
        game_json = self._kv.get(game_key)

        if isinstance(game_json, bytes):
            game_json = game_json.decode("utf-8")

        if not game_json:
            await self._view.send_message_reply(
                chat_id=chat.id,
                message_id=reply_to_id,
                text=self._translate(
                    "msg.private.error.status_unavailable",
                    user_id=user_id,
                ),
            )
            return

        from pokerapp.private_game import PrivateGame

        try:
            private_game = PrivateGame.from_json(game_json)
        except Exception as exc:  # pragma: no cover - defensive parsing
            logger.warning(
                "Failed to parse private game %s: %s",
                game_code,
                exc,
            )
            await self._view.send_message_reply(
                chat_id=chat.id,
                message_id=reply_to_id,
                text=self._translate(
                    "msg.private.error.status_failed",
                    user_id=user_id,
                ),
            )
            return

        stake_config = self._cfg.PRIVATE_STAKES.get(private_game.stake_level, {})
        stake_name = stake_config.get("name") or private_game.stake_level.title()
        max_players = getattr(self._cfg, "PRIVATE_MAX_PLAYERS", 6)
        min_players = getattr(self._cfg, "PRIVATE_MIN_PLAYERS", 2)

        async def resolve_name(player_id: int) -> str:
            if player_id == user_id:
                return (
                    getattr(user, "full_name", None)
                    or getattr(user, "username", None)
                    or str(player_id)
                )

            cached_name = self._username_cache.get(player_id)
            if cached_name:
                return cached_name

            try:
                member = await self._bot.get_chat(player_id)
                name = (
                    getattr(member, "full_name", None)
                    or getattr(member, "first_name", None)
                    or getattr(member, "username", None)
                    or str(player_id)
                )
            except Exception:
                name = str(player_id)

            if name:
                self._username_cache[player_id] = name

            return name

        player_ids = private_game.players or []
        player_names: List[str] = []

        for player_id in player_ids:
            player_names.append(await resolve_name(player_id))

        if private_game.host_user_id == user_id:
            host_name = (
                getattr(user, "full_name", None)
                or getattr(user, "username", None)
                or str(user_id)
            )
        else:
            host_name = self._username_cache.get(private_game.host_user_id)
            if not host_name:
                host_name = await resolve_name(private_game.host_user_id)

        host_name = host_name or str(private_game.host_user_id)

        if not player_names:
            player_names.append(host_name)

        can_start = len(player_ids) >= min_players

        await self._view.send_private_game_status(
            chat_id=chat.id,
            host_name=host_name,
            stake_name=stake_name,
            game_code=str(game_code),
            current_players=len(player_ids),
            max_players=max_players,
            min_players=min_players,
            player_names=player_names,
            can_start=can_start,
        )

    async def send_pending_invites_summary(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Send a summary of pending private game invitations to the caller."""

        self._apply_user_language(update)

        chat = update.effective_chat
        message = update.effective_message
        user = update.effective_user

        if chat is None or message is None or user is None:
            return

        user_id = user.id
        reply_to_id = message.message_id
        pending_key = ":".join(["user", str(user_id), "pending_invites"])

        codes: set[str] = set()

        try:
            if hasattr(self._kv, "smembers"):
                raw_codes = self._kv.smembers(pending_key) or []
                for raw_code in raw_codes:
                    if isinstance(raw_code, bytes):
                        raw_code = raw_code.decode("utf-8")
                    if raw_code:
                        codes.add(str(raw_code))
        except Exception:
            pass

        if not codes:
            fallback_code = self._kv.get(pending_key)
            if isinstance(fallback_code, bytes):
                fallback_code = fallback_code.decode("utf-8")
            if fallback_code:
                codes.add(str(fallback_code))

        if not codes:
            await self._view.send_message_reply(
                chat_id=chat.id,
                message_id=reply_to_id,
                text=self._translate(
                    "msg.private.invite.summary.empty",
                    user_id=user_id,
                ),
            )
            return

        lines = [
            self._translate(
                "msg.private.invite.summary.header",
                user_id=user_id,
            )
        ]

        for code in sorted(codes):
            invite_key = ":".join(["invite", str(code), str(user_id)])
            invite_data = self._kv.get(invite_key)

            if isinstance(invite_data, bytes):
                invite_data = invite_data.decode("utf-8")

            host_name = ""
            stake_label = ""

            if invite_data:
                try:
                    parsed = json.loads(invite_data)
                    host_name = str(parsed.get("host_name", "")).strip()
                    stake_level = parsed.get("stake_level")
                    stake_config = self._cfg.PRIVATE_STAKES.get(stake_level, {})
                    stake_label = stake_config.get("name") or str(stake_level).title()
                except Exception:
                    host_name = ""
                    stake_label = ""

            details: List[str] = []
            if stake_label:
                details.append(escape_markdown(stake_label, version=1))
            if host_name:
                details.append(escape_markdown(host_name, version=1))

            if details:
                joined = " · ".join(details)
                lines.append(
                    self._translate(
                        "msg.private.invite.summary.entry_with_details",
                        user_id=user_id,
                        code=code,
                        details=joined,
                    )
                )
            else:
                lines.append(
                    self._translate(
                        "msg.private.invite.summary.entry_simple",
                        user_id=user_id,
                        code=code,
                    )
                )

        lines.append(
            self._translate(
                "msg.private.invite.summary.footer",
                user_id=user_id,
            )
        )

        await self._view.send_message_reply(
            chat_id=chat.id,
            message_id=reply_to_id,
            text="\n".join(lines),
        )

    async def prepare_player_action(
        self,
        user_id: int,
        chat_id: int,
        action_type: str,
        raise_amount: Optional[int] = None,
        message_version: Optional[int] = None,
        cache: Optional[RequestCache] = None,
    ) -> PlayerActionValidation:
        """Validate that a player can take an action before processing it."""

        cache = cache or self._get_or_create_cache()

        user_id_str = str(user_id)
        chat_id_str = str(chat_id)

        try:
            chat_id_int = int(chat_id)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid chat_id provided for action buttons: %s",
                chat_id,
            )
            return PlayerActionValidation(
                success=False,
                message=self._translate(
                    "model.error.chat_context_missing",
                    user_id=user_id,
                ),
            )

        chat_data = self._application.chat_data.get(chat_id_int, {})
        game = chat_data.get(KEY_CHAT_DATA_GAME)

        if game:
            cached_game = cache.get_game(game.id)
            if cached_game is not None:
                game = cached_game
            else:
                cache.cache_game(game.id, game)

        if not game:
            logger.warning(
                "No active game found in chat %s for user action",
                chat_id_str,
            )
            return PlayerActionValidation(
                success=False,
                message=self._translate(
                    "model.error.no_active_game_chat",
                    user_id=user_id,
                ),
            )

        if message_version is not None:
            current_version = game.get_live_message_version()
            if message_version != current_version:
                logger.info(
                    "Stale action rejected: user=%s chat=%s "
                    "got=%s expected=%s",
                    user_id_str,
                    chat_id_str,
                    message_version,
                    current_version,
                )
                return PlayerActionValidation(
                    success=False,
                    message=self._translate(
                        "model.error.action_expired",
                        user_id=user_id,
                    ),
                )

        if not (0 <= game.current_player_index < len(game.players)):
            logger.error(
                "❌ Invalid player index | index=%s, player_count=%s | game_id=%s",
                game.current_player_index,
                len(game.players),
                getattr(game, "id", "unknown"),
            )
            raise ValueError(
                f"Player index {game.current_player_index} out of range "
                f"(0-{len(game.players) - 1})"
            )

        current_player = game.players[game.current_player_index]
        current_player_id = current_player.user_id
        current_player_id_str = str(current_player_id)

        if current_player_id_str != user_id_str:
            logger.warning(
                "User %s tried to act but it's %s's turn",
                user_id_str,
                current_player_id_str,
            )

            try:
                error_user_id = int(user_id_str)
            except (TypeError, ValueError):
                error_user_id = None

            error_message = self._translate(
                "model.error.not_your_turn",
                user_id=error_user_id,
            )

            if current_player:
                player_name = self._get_player_name(current_player)
                if not player_name:
                    player_name = f"Player {current_player.user_id}"
                detail_message = self._translate(
                    "model.error.not_your_turn_with_player",
                    user_id=error_user_id,
                    player=player_name,
                )
                if detail_message:
                    error_message = "\n".join([error_message, detail_message])

            return PlayerActionValidation(
                success=False,
                message=error_message,
            )

        try:
            wallet = await cache.get_wallet(
                current_player_id, self._kv, self._logger
            )
            current_player.wallet = wallet
        except Exception as exc:
            logger.error(
                "Failed to refresh wallet for user %s during validation: %s",
                current_player_id_str,
                exc,
            )

        active_states = {
            GameState.ROUND_PRE_FLOP,
            GameState.ROUND_FLOP,
            GameState.ROUND_TURN,
            GameState.ROUND_RIVER,
        }

        if game.state not in active_states:
            logger.warning(
                "User %s tried to act while game in state %s",
                user_id_str,
                game.state,
            )
            return PlayerActionValidation(
                success=False,
                message=self._translate(
                    "model.error.game_inactive",
                    user_id=user_id,
                ),
            )

        if current_player.state not in (
            PlayerState.ACTIVE,
            PlayerState.ALL_IN,
        ):
            logger.warning(
                "User %s in invalid state %s for action",
                user_id_str,
                current_player.state,
            )
            if current_player.state == PlayerState.FOLD:
                message = self._translate(
                    "model.error.already_folded",
                    user_id=user_id,
                )
            else:
                message = self._translate(
                    "model.error.cannot_act_now",
                    user_id=user_id,
                )
            return PlayerActionValidation(
                success=False,
                message=message,
            )

        if action_type == "check":
            if game.max_round_rate > current_player.round_rate:
                logger.warning(
                    "User %s tried to check but must call %d",
                    user_id_str,
                    game.max_round_rate - current_player.round_rate,
                )
                return PlayerActionValidation(
                    success=False,
                    message=self._translate(
                        "model.error.call_or_raise",
                        user_id=user_id,
                    ),
                )

        elif action_type == "raise":
            if raise_amount is None or raise_amount <= 0:
                logger.warning(
                    "User %s tried to raise without valid amount",
                    user_id_str,
                )
                return PlayerActionValidation(
                    success=False,
                    message=self._translate(
                        "model.error.invalid_raise_amount",
                        user_id=user_id,
                    ),
                )

            min_raise = game.max_round_rate * 2

            if raise_amount < min_raise:
                logger.warning(
                    "User %s raise %d below minimum %d",
                    user_id_str,
                    raise_amount,
                    min_raise,
                )
                return PlayerActionValidation(
                    success=False,
                    message=self._translate(
                        "model.error.min_raise",
                        user_id=user_id,
                        amount=min_raise,
                    ),
                )

        elif action_type in {"fold", "call", "all_in"}:
            pass
        else:
            logger.warning("Unknown action_type: %s", action_type)
            return PlayerActionValidation(
                success=False,
                message=self._translate(
                    "model.error.unknown_action",
                    user_id=user_id,
                ),
            )

        prepared = PreparedPlayerAction(
            chat_id=chat_id_int,
            chat_id_str=chat_id_str,
            user_id=user_id,
            user_id_str=user_id_str,
            action_type=action_type,
            raise_amount=raise_amount,
            game=game,
            current_player=current_player,
        )

        return PlayerActionValidation(success=True, prepared_action=prepared)

    async def execute_player_action(
        self,
        prepared: PreparedPlayerAction,
        cache: Optional[RequestCache] = None,
    ) -> bool:
        """Execute a previously validated player action."""

        cache = cache or self._get_or_create_cache()

        cached_game = cache.get_game(prepared.game.id)
        if cached_game is not None:
            game = cached_game
        else:
            game = prepared.game
            cache.cache_game(game.id, game)

        current_player = prepared.current_player

        try:
            previous_wallet = current_player.wallet
            wallet = await cache.get_wallet(
                prepared.user_id,
                self._kv,
                self._logger,
            )
            if wallet is not previous_wallet:
                logger.debug(
                    "Refreshed wallet for user %s (balance changed during request)",
                    prepared.user_id_str,
                )
            current_player.wallet = wallet
        except Exception as exc:
            logger.error(
                "Failed to refresh wallet for user %s during execution: %s",
                prepared.user_id_str,
                exc,
            )

        player_name = self._get_player_name(current_player)
        action_type = prepared.action_type
        user_id_str = prepared.user_id_str

        try:
            if action_type == "fold":
                current_player.state = PlayerState.FOLD
                action_text = self._translate(
                    "msg.player_folded",
                    user_id=current_player.user_id,
                    player=player_name,
                )

            elif action_type == "check":
                action_text = self._translate(
                    "msg.player_checked",
                    user_id=current_player.user_id,
                    player=player_name,
                )

            elif action_type == "call":
                call_amount = self._coordinator.player_call_or_check(
                    game, current_player
                )
                action_text = self._translate(
                    "msg.player_called",
                    user_id=current_player.user_id,
                    player=player_name,
                    amount=call_amount,
                )

            elif action_type == "raise":
                raise_amount = prepared.raise_amount
                if raise_amount is None:
                    logger.warning(
                        "Validated raise lost amount for user %s",
                        user_id_str,
                    )
                    return False

                self._coordinator.player_raise_bet(
                    game,
                    current_player,
                    raise_amount,
                )
                target_amount = current_player.round_rate
                action_text = self._translate(
                    "msg.player_raised",
                    user_id=current_player.user_id,
                    player=player_name,
                    amount=target_amount,
                )

            elif action_type == "all_in":
                all_in_amount = self._coordinator.player_all_in(
                    game, current_player
                )
                current_player.state = PlayerState.ALL_IN
                action_text = self._translate(
                    "msg.player_all_in",
                    user_id=current_player.user_id,
                    player=player_name,
                    amount=all_in_amount,
                )

            else:
                logger.warning(
                    "Unknown action_type during execution: %s",
                    action_type,
                )
                return False

            game.add_action(action_text)

            self._save_game(prepared.chat_id, game)

            self._coordinator.engine.advance_after_action(game)

            if hasattr(self._view, "invalidate_render_cache"):
                self._view.invalidate_render_cache(game)

            turn_result, next_player = self._coordinator.process_game_turn(
                game
            )

            await self._handle_turn_result(
                game,
                prepared.chat_id,
                turn_result,
                next_player,
            )

            if turn_result == TurnResult.CONTINUE_ROUND:
                await self._coordinator._send_or_update_game_state(
                    game=game,
                    chat_id=prepared.chat_id,
                )

            return True

        except Exception as exc:
            logger.error(
                "Error processing action %s for user %s: %s",
                action_type,
                user_id_str,
                exc,
                exc_info=True,
            )
            return False

    async def handle_player_action(
        self,
        user_id: int,
        chat_id: int,
        action_type: str,
        raise_amount: Optional[int] = None,
    ) -> bool:
        """Backwards-compatible wrapper for controller-driven actions."""

        cache = self._get_or_create_cache()

        try:
            validation = await self.prepare_player_action(
                user_id=user_id,
                chat_id=chat_id,
                action_type=action_type,
                raise_amount=raise_amount,
                cache=cache,
            )

            if not validation.success or validation.prepared_action is None:
                return False

            return await self.execute_player_action(
                validation.prepared_action,
                cache=cache,
            )
        finally:
            self._clear_request_cache()


class WalletManagerModel(Wallet):
    def __init__(self, user_id: UserId, kv: Optional[redis.Redis]):
        self.user_id = user_id
        self._kv = ensure_kv(kv)

        key = self._prefix(self.user_id)
        if self._kv.get(key) is None:
            self._kv.set(key, DEFAULT_MONEY)

    @classmethod
    async def load(
        cls,
        user_id: UserId,
        kv: Optional[redis.Redis],
        logger: logging.Logger,
    ) -> "WalletManagerModel":
        try:
            return await asyncio.to_thread(cls, user_id, kv)
        except Exception as exc:
            logger.exception(
                "Failed to load wallet for user %s: %s",
                user_id,
                exc,
            )
            raise

    @staticmethod
    def _prefix(id: int, suffix: str = ""):
        return "pokerbot:" + str(id) + suffix

    def _current_date(self) -> str:
        return dt.utcnow().strftime("%d/%m/%y")

    def _key_daily(self) -> str:
        return self._prefix(self.user_id, ":daily")

    def has_daily_bonus(self) -> bool:
        current_date = self._current_date()
        last_date = self._kv.get(self._key_daily())

        return last_date is not None and \
            last_date.decode("utf-8") == current_date

    def add_daily(self, amount: Money) -> Money:
        if self.has_daily_bonus():
            raise UserException(
                translation_manager.t(
                    "msg.bonus.already_claimed_plain",
                    user_id=self.user_id,
                    amount=self.value(),
                )
            )

        key = self._prefix(self.user_id)
        self._kv.set(self._key_daily(), self._current_date())

        return self._kv.incrby(key, amount)

    def inc(self, amount: Money = 0) -> None:
        """ Increase count of money in the wallet.
            Decrease authorized money.
        """
        wallet = int(self._kv.get(self._prefix(self.user_id)))

        if wallet + amount < 0:
            raise UserException(
                translation_manager.t(
                    "msg.error.wallet_not_enough",
                    user_id=self.user_id,
                )
            )

        self._kv.incrby(self._prefix(self.user_id), amount)

    def inc_authorized_money(
        self,
        game_id: str,
        amount: Money
    ) -> None:
        key_authorized_money = self._prefix(self.user_id, ":" + game_id)
        self._kv.incrby(key_authorized_money, amount)

    def authorized_money(self, game_id: str) -> Money:
        key_authorized_money = self._prefix(self.user_id, ":" + game_id)
        return int(self._kv.get(key_authorized_money) or 0)

    def authorize(self, game_id: str, amount: Money) -> None:
        """ Decrease count of money. """
        self.inc_authorized_money(game_id, amount)

        return self.inc(-amount)

    def authorize_all(self, game_id: str) -> Money:
        """ Decrease all money of player. """
        money = int(self._kv.get(self._prefix(self.user_id)))
        self.inc_authorized_money(game_id, money)

        self._kv.set(self._prefix(self.user_id), 0)
        return money

    def value(self) -> Money:
        """ Get count of money in the wallet. """
        return int(self._kv.get(self._prefix(self.user_id)))

    def approve(self, game_id: str) -> None:
        key_authorized_money = self._prefix(self.user_id, ":" + game_id)
        self._kv.delete(key_authorized_money)
