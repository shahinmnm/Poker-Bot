#!/usr/bin/env python3

import asyncio
import datetime
from datetime import datetime as dt
import html
import json
import logging
import secrets
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List, Optional, Tuple, Union

import redis
from telegram import Bot, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackContext, ContextTypes
from telegram.helpers import escape_markdown
from telegram.constants import ParseMode

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
from pokerapp.game_engine import TurnResult
from pokerapp.pokerbotview import PokerBotViewer
from pokerapp.kvstore import ensure_kv
from pokerapp.winnerdetermination import get_combination_name


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
        self._coordinator = GameCoordinator(view=view)
        self._stake_config = STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL]

        self._readyMessages = {}
        self._username_cache: Dict[int, str] = {}
        self._table_messages: Dict[int, int] = {}

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

    def _validate_game_code(self, code: Optional[str]) -> tuple[bool, str]:
        """
        Validate game code format (6 alphanumeric characters).

        Returns:
            (is_valid, error_message)
        """
        if not code:
            return False, "❌ Please provide a game code: /join <code>"

        if len(code) != 6 or not code.isalnum():
            return False, (
                f"❌ Invalid code format: '{code}'\n\n"
                "Game codes must be exactly 6 alphanumeric characters."
            )

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
        parse_mode: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> None:
        """
        Centralized message sending with consistent interface.
        """
        effective_message = update.effective_message

        if effective_message is not None:
            await effective_message.reply_text(
                message,
                parse_mode=parse_mode,
                reply_to_message_id=reply_to_message_id,
            )
            return

        await self._bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            parse_mode=parse_mode,
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
        chat_data = self._application.chat_data.setdefault(chat_id, {})

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
            chat_data = self._application.chat_data.setdefault(
                int(chat_id),
                {},
            )
            chat_data[KEY_CHAT_DATA_GAME] = game
            logger.debug("Saved game %s to chat %s", game.id, chat_id)
            return

        raise ValueError("Invalid _save_game arguments")

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
                    parse_mode=ParseMode.HTML,
                )
                return

            lines: List[str] = ["🏆 <b>Game Results:</b>\n"]

            for rank, score in enumerate(sorted_scores, start=1):
                players_with_score = normalized_results[score]
                hand_name = get_combination_name(score)

                if rank == 1:
                    winner_heading = (
                        f"🥇 <b>Winner(s) - {html.escape(hand_name)}</b>"
                    )
                    lines.append(winner_heading)
                else:
                    lines.append(f"\n{rank}. {html.escape(hand_name)}")

                for player, hand_cards, winnings in players_with_score:
                    mention_raw = getattr(
                        player,
                        "mention_markdown",
                        f"Player {player.user_id}",
                    )
                    mention_raw = mention_raw.strip("`")
                    if mention_raw.startswith("[") and "](" in mention_raw:
                        label, link = mention_raw[1:].split("](", 1)
                        link = link.rstrip(")")
                        mention = (
                            f'<a href="{html.escape(link, quote=True)}">'
                            f"{html.escape(label)}</a>"
                        )
                    else:
                        mention = html.escape(mention_raw)

                    cards_str = " ".join(
                        html.escape(str(card)) for card in hand_cards[:5]
                    )

                    lines.append(f" • {mention}")
                    lines.append(f" Cards: <code>{cards_str}</code>")
                    if winnings is not None:
                        winnings_text = html.escape(str(winnings))
                        lines.append(f" Winnings: <b>{winnings_text}$</b>")

            lines.append(f"\n💰 Total Pot: {html.escape(str(game.pot))}")

            result_text = "\n".join(lines)

            await self._bot.send_message(
                chat_id=int(chat_id),
                text=result_text,
                parse_mode=ParseMode.HTML,
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
                parse_mode=ParseMode.HTML,
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

    async def ready(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id

        if game.state != GameState.INITIAL:
            await self._view.send_message_reply(
                chat_id=chat_id,
                message_id=update.effective_message.message_id,
                text="The game is already started. Wait!",
            )
            return

        if len(game.players) > MAX_PLAYERS:
            await self._view.send_message_reply(
                chat_id=chat_id,
                text="The room is full",
                message_id=update.effective_message.message_id,
            )
            return

        user = update.effective_message.from_user

        if user.id in game.ready_users:
            await self._view.send_message_reply(
                chat_id=chat_id,
                message_id=update.effective_message.message_id,
                text="You are already ready",
            )
            return

        player = Player(
            user_id=user.id,
            mention_markdown=user.mention_markdown(),
            wallet=WalletManagerModel(user.id, self._kv),
            ready_message_id=update.effective_message.message_id,
        )

        if not BalanceValidator.can_afford_table(
            balance=player.wallet.value(),
            stake_config=STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL]
        ):
            await self._view.send_message_reply(
                chat_id=chat_id,
                message_id=update.effective_message.message_id,
                text="You don't have enough money for this table",
            )
            return

        game.ready_users.add(user.id)

        game.players.append(player)

        members_count = await self._bot.get_chat_member_count(chat_id)
        players_active = len(game.players)
        # One is the bot.
        if (
            players_active == members_count - 1
            and players_active >= self._min_players
        ):
            await self._start_game(context=context, game=game, chat_id=chat_id)

    async def stop(self, user_id: UserId) -> None:
        UserPrivateChatModel(user_id=user_id, kv=self._kv).delete()

    async def start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        user = update.effective_user
        self._track_user(user.id, user.username)
        user_id = user.id

        if game.state not in (GameState.INITIAL, GameState.FINISHED):
            await self._view.send_message(
                chat_id=chat_id,
                text="The game is already in progress"
            )
            return

        # One is the bot.
        members_count = (await self._bot.get_chat_member_count(chat_id)) - 1
        if members_count == 1:
            await self.show_help(update, context)

            if update.effective_chat.type == 'private':
                UserPrivateChatModel(user_id=user_id, kv=self._kv) \
                    .set_chat_id(chat_id=chat_id)

            return

        players_active = len(game.players)
        if players_active >= self._min_players:
            await self._start_game(context=context, game=game, chat_id=chat_id)
        else:
            await self._view.send_message(
                chat_id=chat_id,
                text="Not enough player"
            )
        return

    async def show_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_message.chat_id
        try:
            with open(DESCRIPTION_FILE, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            text = (
                "Welcome to Poker Bot!\n"
                "Use /ready to join the next game and "
                "/money to claim your daily bonus."
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

        await self._view.send_message(
            chat_id=chat_id,
            text='The game is started! 🃏',
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
        await self._divide_cards(game=game, chat_id=chat_id)

        game.current_player_index = 1
        self._coordinator.apply_pre_flop_blinds(
            game=game,
            small_blind=self._stake_config.small_blind,
            big_blind=self._stake_config.big_blind,
        )

        await self._start_betting_round(game, chat_id)

        context.chat_data[KEY_OLD_PLAYERS] = list(
            map(lambda p: p.user_id, game.players),
        )

    async def bonus(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        wallet = WalletManagerModel(
            update.effective_message.from_user.id, self._kv)
        money = wallet.value()

        chat_id = update.effective_message.chat_id
        message_id = update.effective_message.message_id

        if wallet.has_daily_bonus():
            await self._view.send_message_reply(
                chat_id=chat_id,
                message_id=message_id,
                text=f"Your money: *{money}$*\n",
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
                text=(
                    f"Bonus: *{bonus_amount}$* {icon}\n"
                    f"Your money: *{money}$*\n"
                ),
            )

        self._application.create_task(print_bonus())

    async def send_cards_to_user(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        game = self._game_from_context(context)

        current_player: Optional[Player] = None
        for player in game.players:
            if player.user_id == update.effective_user.id:
                current_player = player
                break

        if current_player is None or not current_player.cards:
            return

        await self._view.send_cards(
            chat_id=update.effective_message.chat_id,
            cards=current_player.cards,
            mention_markdown=current_player.mention_markdown,
            ready_message_id=update.effective_message.message_id,
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
            try:
                private_chat = UserPrivateChatModel(
                    user_id=player.user_id,
                    kv=self._kv,
                )
                private_chat_id = private_chat.get_chat_id()

                if private_chat_id:
                    if isinstance(private_chat_id, bytes):
                        private_chat_id = private_chat_id.decode('utf-8')

                    existing_message_id_raw = private_chat.pop_message()
                    existing_message_id: Optional[int] = None

                    if existing_message_id_raw is not None:
                        try:
                            if isinstance(existing_message_id_raw, bytes):
                                existing_message_id_raw = (
                                    existing_message_id_raw.decode('utf-8')
                                )
                            existing_message_id = int(existing_message_id_raw)
                        except (TypeError, ValueError):
                            existing_message_id = None

                    new_message_id = await self._view.send_or_update_private_hand(
                        chat_id=private_chat_id,
                        cards=player.cards,
                        mention_markdown=player.mention_markdown,
                        table_cards=None,
                        message_id=existing_message_id,
                        disable_notification=False,
                    )

                    if new_message_id is not None:
                        private_chat.push_message(new_message_id)
                    elif existing_message_id is not None:
                        try:
                            await self._view.remove_message(
                                chat_id=private_chat_id,
                                message_id=existing_message_id,
                            )
                        except Exception as exc:
                            logger.debug(
                                "Failed to remove stale private hand message for %s: %s",
                                player.user_id,
                                exc,
                            )
                    return
            except Exception as exc:
                logger.warning(
                    "Failed to send cards privately to %s: %s",
                    player.user_id,
                    exc,
                )

            await self._view.send_cards(
                chat_id=chat_id,
                cards=player.cards,
                mention_markdown=player.mention_markdown,
                ready_message_id=player.ready_message_id,
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
            # Destination is a chat_id - fall back to legacy reply keyboard flow.
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

    async def _start_betting_round(self, game: Game, chat_id: int) -> None:
        """
        Start new betting round using coordinator.
        Replaces legacy _process_playing loop.
        """

        while True:
            result, next_player = self._coordinator.process_game_turn(game)

            if result == TurnResult.END_GAME:
                await self._finish_game(game, chat_id)
                return

            if result == TurnResult.END_ROUND:
                self._coordinator.commit_round_bets(game)

                if game.state == GameState.ROUND_RIVER:
                    await self._finish_game(game, chat_id)
                    return

                new_state, cards_count = (
                    self._coordinator.advance_game_street(game)
                )

                if cards_count > 0:
                    await self.add_cards_to_table(cards_count, game, chat_id)

                if new_state == GameState.FINISHED:
                    await self._finish_game(game, chat_id)
                    return

                continue

            if result == TurnResult.CONTINUE_ROUND and next_player:
                game.last_turn_time = dt.now()
                await self._view.send_turn_actions(
                    chat_id=chat_id,
                    game=game,
                    player=next_player,
                    money=next_player.wallet.value(),
                )
                return

    async def _cleanup_player_turn_ui(
        self,
        chat_id: int,
        player_user_id: int,
        game: Game,
    ) -> None:
        """Clean up UI elements after player completes their action.

        Removes the player's Reply Keyboard and updates the live table message.

        Args:
            chat_id: Group chat ID
            player_user_id: User ID of player who just acted
            game: Current game state
        """

        if hasattr(self._view, "remove_player_keyboard"):
            try:
                await self._view.remove_player_keyboard(
                    chat_id=chat_id,
                    player_user_id=player_user_id,
                )
            except Exception as e:
                self._logger.warning(
                    "Failed to remove keyboard for %s: %s",
                    player_user_id,
                    e,
                )

        if game.has_group_message():
            current_player = None
            if 0 <= game.current_player_index < len(game.players):
                current_player = game.players[game.current_player_index]

            try:
                await self._view.update_game_state(
                    chat_id=chat_id,
                    message_id=game.group_message_id,
                    game=game,
                    current_player=current_player,
                    action_prompt="",
                )
            except Exception as e:
                self._logger.warning(
                    "Failed to update game state message: %s",
                    e,
                )

    async def add_cards_to_table(
        self,
        count: int,
        game: Game,
        chat_id: ChatId,
    ) -> None:
        for _ in range(count):
            if not game.remain_cards:
                logger.debug(
                    "No more cards remaining when attempting to deal to table"
                )
                break

            card = game.remain_cards.pop()
            game.cards_table.append(card)
            logger.debug("Dealt community card %s", card)

        if self._view is not None:
            try:
                try:
                    chat_key = int(chat_id)
                except (TypeError, ValueError):
                    chat_key = chat_id

                existing_message_id = self._table_messages.get(chat_key)

                new_message_id = await self._view.send_or_update_table_cards(
                    chat_id=chat_id,
                    cards=game.cards_table,
                    pot=game.pot,
                    message_id=existing_message_id,
                )

                if new_message_id is not None:
                    self._table_messages[chat_key] = new_message_id
            except Exception as exc:
                logger.debug(
                    "Failed to update table cards for chat %s: %s",
                    chat_id,
                    exc,
                )

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
            if current_user_id != current_player.user_id:
                return

            await fn(update, context)
            await self._view.remove_markup(
                chat_id=update.effective_message.chat_id,
                message_id=update.effective_message.message_id,
            )

        return m

    async def ban_player(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
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
        game = self._game_from_context(context)
        player = self._current_turn_player(game)
        user_id = player.user_id

        player.state = PlayerState.FOLD

        await self._view.send_message(
            chat_id=update.effective_message.chat_id,
            text=f"{player.mention_markdown} {PlayerAction.FOLD.value}"
        )

        chat_id = update.effective_message.chat_id
        await self._start_betting_round(game, chat_id)
        await self._cleanup_player_turn_ui(
            chat_id=chat_id,
            player_user_id=user_id,
            game=game,
        )

    async def call_check(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        user_id = player.user_id

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

            self._coordinator.player_call_or_check(game, player)
        except UserException as e:
            await self._view.send_message(chat_id=chat_id, text=str(e))
            return

        await self._start_betting_round(game, chat_id)
        await self._cleanup_player_turn_ui(
            chat_id=chat_id,
            player_user_id=user_id,
            game=game,
        )

    async def raise_rate_bet(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        raise_bet_rate: PlayerAction
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        user_id = player.user_id

        try:
            action = PlayerAction.RAISE_RATE
            if player.round_rate == game.max_round_rate:
                action = PlayerAction.BET

            call_amount = max(game.max_round_rate - player.round_rate, 0)
            total_required = call_amount + raise_bet_rate.value

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
                amount=game.max_round_rate + raise_bet_rate.value,
            )
        except UserException as e:
            await self._view.send_message(chat_id=chat_id, text=str(e))
            return

        await self._start_betting_round(game, chat_id)
        await self._cleanup_player_turn_ui(
            chat_id=chat_id,
            player_user_id=user_id,
            game=game,
        )

    async def all_in(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        user_id = player.user_id
        mention = player.mention_markdown
        amount = self._coordinator.player_all_in(game, player)
        await self._view.send_message(
            chat_id=chat_id,
            text=f"{mention} {PlayerAction.ALL_IN.value} {amount}$"
        )
        player.state = PlayerState.ALL_IN
        await self._start_betting_round(game, chat_id)
        await self._cleanup_player_turn_ui(
            chat_id=chat_id,
            player_user_id=user_id,
            game=game,
        )

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
        user = update.effective_message.from_user
        chat_id = update.effective_chat.id

        self._track_user(user.id, getattr(user, "username", None))

        # Check if user already has an active private game
        existing_game_key = ":".join(["user", str(user.id), "private_game"])
        if self._kv.exists(existing_game_key):
            await self._send_response(
                update,
                (
                    "❌ You already have an active private game!\n"
                    "Use /leave to exit your current game first."
                ),
            )
            return

        # Show stake selection menu
        await self._view.send_stake_selection(
            chat_id=chat_id,
            user_name=user.full_name
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
        from pokerapp.private_game import PrivateGame, PrivateGameState

        query = update.callback_query
        user = query.from_user
        self._track_user(user.id, user.username)
        # Validate stake level
        stake_config = self._cfg.PRIVATE_STAKES.get(stake_level)
        if not stake_config:
            await query.edit_message_text(
                f"❌ Invalid stake level: {stake_level}"
            )
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
        message = (
            "✅ Private game created!\n\n"
            f"🎯 **Game Code**: {game_code}\n"
            f"💰 **Stakes**: {stake_config['name']}\n"
            f"💵 **Buy-in**: {min_buyin} - {stake_config['max_buyin']}\n\n"
            "📨 Share this code with friends:\n"
            f"/join {game_code}\n\n"
            f"👥 **Players**: 1/{self._cfg.PRIVATE_MAX_PLAYERS}\n"
            f"• {user.full_name} (Host)\n\n"
            "⏳ Waiting for players to join..."
        )
        await query.edit_message_text(
            text=message,
            parse_mode='Markdown'
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
        from pokerapp.private_game import PrivateGame, PrivateGameState

        query = update.callback_query
        user = query.from_user

        self._track_user(user.id, getattr(user, "username", None))

        # Load game from Redis
        lobby_key = ":".join(["private_game", game_code])
        game_data = self._kv.get(lobby_key)

        if not game_data:
            await query.answer("❌ Game not found!", show_alert=True)
            return

        if isinstance(game_data, bytes):
            game_data = game_data.decode('utf-8')

        private_game = PrivateGame.from_json(game_data)

        # Check if user is invited
        if user.id not in private_game.invited_players:
            await query.edit_message_text(
                "❌ You are not invited to this game."
            )
            return

        # Check if already accepted
        invite = private_game.invited_players[user.id]
        if invite.accepted:
            await query.edit_message_text(
                "✅ You already accepted this invitation!"
            )
            return

        # Check if game is still accepting players
        if private_game.state != PrivateGameState.LOBBY:
            await query.edit_message_text(
                "❌ This game has already started or finished."
            )
            return

        # Check user balance
        stake_config = self._cfg.PRIVATE_STAKES.get(private_game.stake_level)
        if not stake_config:
            await query.edit_message_text(
                "❌ Stake configuration missing for this game."
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
        message = (
            "✅ Invitation accepted!\n\n"
            f"🎯 **Game Code**: {game_code}\n"
            f"💰 **Stakes**: {stake_config['name']}\n\n"
            "You have joined the game lobby.\n\n"
            "Waiting for host to start the game..."
        )
        await query.edit_message_text(
            text=message,
            parse_mode='Markdown'
        )

        # Notify host
        await context.bot.send_message(
            chat_id=private_game.host_user_id,
            text=f"✅ {user.full_name} accepted your invitation!"
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
        from pokerapp.private_game import PrivateGame

        query = update.callback_query
        user = query.from_user

        self._track_user(user.id, getattr(user, "username", None))

        # Load game from Redis
        game_key = ":".join(["private_game", game_code])
        game_data = self._kv.get(game_key)

        if not game_data:
            await query.answer("❌ Game not found!", show_alert=True)
            return

        if isinstance(game_data, bytes):
            game_data = game_data.decode('utf-8')

        private_game = PrivateGame.from_json(game_data)

        # Check if user is invited
        if user.id not in private_game.invited_players:
            await query.edit_message_text(
                "❌ You are not invited to this game."
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
        message = (
            "❌ Invitation declined.\n\n"
            f"You can still join later with /join {game_code}"
        )
        await query.edit_message_text(
            text=message,
            parse_mode='Markdown'
        )

        # Notify host
        await context.bot.send_message(
            chat_id=private_game.host_user_id,
            text=f"❌ {user.full_name} declined your invitation."
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

        user = update.effective_user
        self._track_user(user.id, user.username)
        user_id = user.id
        chat_id = update.effective_message.chat_id

        if not context.args or len(context.args) != 1:
            await self._send_response(
                update,
                (
                    "❌ Invalid command format\n\n"
                    "Usage: /join <code>\n"
                    "Example: /join ABC123"
                ),
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        game_code = context.args[0].upper()

        is_valid, error_msg = self._validate_game_code(game_code)
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
                text=(
                    f"❌ Game ‘{game_code}’ not found\n\n"
                    "The game may have ended or the code is incorrect"
                ),
                message_id=update.effective_message.message_id,
            )
            return

        try:
            game_chat_id = int(game_chat_id)
        except (TypeError, ValueError):
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=(
                    f"❌ Game ‘{game_code}’ not found\n\n"
                    "The game may have ended or the code is incorrect"
                ),
                message_id=update.effective_message.message_id,
            )
            return

        game = self._game(game_chat_id)

        if game.state != GameState.INITIAL:
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=(
                    f"❌ Game ‘{game_code}’ has already started\n\n"
                    "You cannot join a game in progress"
                ),
                message_id=update.effective_message.message_id,
            )
            return

        if any(p.user_id == user_id for p in game.players):
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=f"✅ You’re already in game ‘{game_code}’!",
                message_id=update.effective_message.message_id,
            )
            return

        if not self._has_available_seat(game):
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=(
                    f"❌ Game ‘{game_code}’ is full\n\n"
                    f"Maximum {MAX_PLAYERS} players allowed"
                ),
                message_id=update.effective_message.message_id,
            )
            return

        stake_config = game.stake_config

        if stake_config is None:
            await self._view.send_message_reply(
                chat_id=chat_id,
                text=(
                    f"❌ Game ‘{game_code}’ is not accepting players right now"
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
            text=(
                f"✅ Successfully joined game ‘{game_code}’!\n\n"
                f"🎰 Stake: {stake_config.name}\n"
                f"👥 Players: {len(game.players)}/{MAX_PLAYERS}\n\n"
                "Waiting for host to start the game…"
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

        user = update.effective_user
        self._track_user(user.id, getattr(user, "username", None))

        # Check if user has active private game
        user_game_key = "user:" + str(user.id) + ":private_game"
        game_code = self._kv.get(user_game_key)

        if isinstance(game_code, bytes):
            game_code = game_code.decode("utf-8")

        if not game_code:
            await self._send_response(
                update,
                (
                    "❌ You don't have an active private game.\n\n"
                    "Create one with /private"
                ),
            )
            return

        # Parse target username
        if not context.args:
            await self._send_response(
                update,
                "❌ Please specify a player:\n\n/invite @username",
            )
            return

        target_username = context.args[0].lstrip("@")

        if not target_username:
            await self._send_response(
                update,
                "❌ Please specify a player:\n\n/invite @username",
            )
            return

        target_user_id = self._lookup_user_by_username(target_username)

        if not target_user_id:
            await self._send_response(
                update,
                (
                    f"❌ User @{target_username} not found.\n\n"
                    "Players must have used the bot before being invited."
                ),
            )
            return

        # Check if already in game
        target_game_key = "user:" + str(target_user_id) + ":private_game"
        if self._kv.get(target_game_key):
            await self._send_response(
                update,
                f"❌ @{target_username} is already in a game.",
            )
            return

        # Load game data
        game_key = "private_game:" + str(game_code)
        game_json = self._kv.get(game_key)

        if isinstance(game_json, bytes):
            game_json = game_json.decode("utf-8")

        if not game_json:
            await self._send_response(update, "❌ Game not found.")
            return

        game_data = json.loads(game_json)
        stake_level = game_data.get("stake_level")

        try:
            stake_config = self._cfg.PRIVATE_STAKES[stake_level]
        except KeyError:
            await self._send_response(
                update,
                "❌ Game stakes are misconfigured.",
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
            await self._bot.send_message(
                chat_id=target_user_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            await self._send_response(
                update,
                f"✅ Invitation sent to @{target_username}!",
            )
        except Exception as exc:
            logger.error("Failed to send invitation: %s", exc)
            await self._send_response(
                update,
                (
                    f"❌ Couldn't send invitation to @{target_username}.\n"
                    "They may have blocked the bot."
                ),
            )

    async def start_private_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Start a private game after validating lobby requirements."""

        from pokerapp.private_game import PrivateGame, PrivateGameState

        user = update.effective_user
        chat_id = update.effective_chat.id

        # Get game code from user's active game
        user_game_key = ":".join(["user", str(user.id), "private_game"])
        game_code = self._kv.get(user_game_key)

        if not game_code:
            await self._send_response(
                update,
                "❌ You don't have an active private game.\n\n"
                "Create one with /private",
                reply_to_message_id=update.effective_message.message_id,
            )
            return

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
                f"❌ Game {game_code} not found or expired.",
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        private_game = PrivateGame.from_json(game_json)

        # Validate caller is host
        if user.id != private_game.host_user_id:
            await self._send_response(
                update,
                "❌ Only the host can start the game!",
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        # Validate game state
        if private_game.state != PrivateGameState.LOBBY:
            await self._send_response(
                update,
                "❌ Game has already started or finished!",
                reply_to_message_id=update.effective_message.message_id,
            )
            return

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
                (
                    "❌ Need at least "
                    f"{self._cfg.PRIVATE_MIN_PLAYERS} players to start!\n\n"
                    f"Current: {len(accepted_players)} player(s)"
                ),
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        # Validate maximum players (prevent overflow)
        if len(accepted_players) > self._cfg.PRIVATE_MAX_PLAYERS:
            await self._send_response(
                update,
                (
                    f"❌ Too many players to start!\n\n"
                    f"Maximum: {self._cfg.PRIVATE_MAX_PLAYERS} players\n"
                    f"Current: {len(accepted_players)} players\n\n"
                    "Some players must /leave before starting."
                ),
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        # Get stake configuration
        stake_config = self._cfg.PRIVATE_STAKES.get(private_game.stake_level)

        if not stake_config:
            await self._send_response(
                update,
                "❌ Stake configuration missing for this game!",
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        small_blind = int(stake_config["small_blind"])
        big_blind = int(stake_config["big_blind"])
        min_buyin = int(stake_config["min_buyin"])
        minimum_required = max(
            min_buyin,
            big_blind * self._cfg.MINIMUM_BALANCE_MULTIPLIER,
        )

        # Validate ALL players have sufficient balance
        insufficient_players = []

        for player_id in accepted_players:
            balance = await self._get_user_balance(player_id)

            if balance < minimum_required:
                cached_name = self._username_cache.get(player_id)
                display_name = (
                    cached_name if cached_name else f"Player {player_id}"
                )
                insufficient_players.append((player_id, display_name, balance))

        # If any player lacks funds, reject start
        if insufficient_players:
            error_lines = ["❌ Cannot start game - insufficient funds:\n"]

            for player_id, name, balance in insufficient_players:
                error_lines.append(
                    f"• {name}: {balance}$ (need {minimum_required}$)"
                )

            error_lines.append(
                f"\nAll players need at least {minimum_required}$ to play."
            )

            await self._send_response(
                update,
                "\n".join(error_lines),
                reply_to_message_id=update.effective_message.message_id,
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
            return

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
                text=(
                    "⚠️ Lobby state changed during game start. "
                    "Please try /start again."
                ),
            )
            return

        # === STEP 2A: CREATE PLAYER OBJECTS ===

        players: List[Player] = []
        player_names: List[str] = []

        # Helper to resolve display names (reuse username cache)
        async def resolve_display_name(player_id: int) -> str:
            """Get cached username or fetch from Telegram."""

            cached_name = self._username_cache.get(player_id)
            if cached_name:
                return cached_name

            if player_id == user.id:
                name = (
                    getattr(user, "full_name", None)
                    or getattr(user, "username", None)
                    or str(player_id)
                )
                self._username_cache[player_id] = name
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
                return name
            except Exception as exc:
                logger.warning(
                    "Failed to resolve name for user %s: %s",
                    player_id,
                    exc,
                )
                return str(player_id)

        # Load wallets and names in parallel
        wallet_tasks = [
            WalletManagerModel.load(user_id, self._kv, logger)
            for user_id in accepted_players
        ]
        name_tasks = [
            resolve_display_name(user_id)
            for user_id in accepted_players
        ]

        wallets = await asyncio.gather(*wallet_tasks)
        player_names = await asyncio.gather(*name_tasks)

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

        await self._view.send_message(
            chat_id=chat_id,
            text=(
                f"🎮 **Game Starting!**\n\n"
                f"**Players ({len(players)})**: \n"
                + "\n".join(f"• {name}" for name in player_names)
                + f"\n\n**Stakes**: {stake_config['name']}\n"
                f"**Blinds**: {small_blind}/{big_blind}\n\n"
                "Good luck! 🍀"
            ),
            parse_mode="Markdown",
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
                await self._view.send_message(
                    chat_id=player.user_id,
                    text=(
                        f"🎮 **Game Started!**\n\n"
                        f"💰 **Your Balance**: ${format(balance, ',.0f')}\n"
                        f"📊 **Blinds**: ${small_blind}/{big_blind}\n\n"
                        "Your cards will be dealt shortly. Good luck! 🍀"
                    ),
                    parse_mode="Markdown",
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
            await self._view.send_message(
                chat_id=chat_id,
                text=(
                    "❌ **Game Failed to Start**\n\n"
                    "An error occurred while initializing the game. "
                    "Please try again or contact support."
                ),
                parse_mode="Markdown",
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

    async def accept_invitation(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Player accepts game invitation via callback button."""

        query = update.callback_query

        if query is None or not query.data:
            return

        user = query.from_user
        self._track_user(user.id, getattr(user, "username", None))

        # Extract game code from callback data
        try:
            game_code = query.data.split(":", 1)[1]
        except IndexError:
            await query.edit_message_text(
                "❌ This invitation has expired or is invalid.",
            )
            return

        # Validate invitation exists
        invite_key = "invite:" + str(game_code) + ":" + str(user.id)
        invite_json = self._kv.get(invite_key)

        if isinstance(invite_json, bytes):
            invite_json = invite_json.decode("utf-8")

        if not invite_json:
            await query.edit_message_text(
                "❌ This invitation has expired or is invalid.",
            )
            return

        invite_data = json.loads(invite_json)
        status = invite_data.get("status", "pending")

        if status != "pending":
            await query.edit_message_text(
                f"❌ You already {status} this invitation.",
            )
            return

        # Check balance
        stake_level = invite_data.get("stake_level")

        try:
            stake_config = self._cfg.PRIVATE_STAKES[stake_level]
        except KeyError:
            await query.edit_message_text("❌ Game stakes are misconfigured.")
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
                "❌ Insufficient balance!\n\n",
                f"Required: {required_chips} chips\n",
                f"Your balance: {balance_chips} chips",
            )
            return

        # Load game
        game_key = "private_game:" + str(game_code)
        game_json = self._kv.get(game_key)

        if isinstance(game_json, bytes):
            game_json = game_json.decode("utf-8")

        if not game_json:
            await query.edit_message_text("❌ Game no longer exists.")
            return

        from pokerapp.private_game import PrivateGame

        game = PrivateGame.from_json(game_json)

        # Check if game is full
        max_players = getattr(self._cfg, "PRIVATE_MAX_PLAYERS", 6)
        if len(game.players) >= max_players:
            await query.edit_message_text(
                f"❌ Game is full (max {max_players} players).",
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
        await query.edit_message_text(
            "✅ Joined Game!\n\n"
            f"Game Code: {game_code}\n"
            f"Stakes: {stake_config['name']}\n\n"
            "Use /leave to exit the lobby.",
        )

        # Notify host
        try:
            await self._bot.send_message(
                chat_id=invite_data["host_id"],
                text=(
                    f"✅ @{user.username or user.first_name} "
                    "accepted your invitation!"
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

        query = update.callback_query

        if query is None or not query.data:
            return

        user = query.from_user
        self._track_user(user.id, getattr(user, "username", None))

        # Extract game code
        try:
            game_code = query.data.split(":", 1)[1]
        except IndexError:
            await query.edit_message_text(
                "❌ This invitation has expired or is invalid.",
            )
            return

        # Validate invitation
        invite_key = "invite:" + str(game_code) + ":" + str(user.id)
        invite_json = self._kv.get(invite_key)

        if isinstance(invite_json, bytes):
            invite_json = invite_json.decode("utf-8")

        if not invite_json:
            await query.edit_message_text(
                "❌ This invitation has expired or is invalid.",
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
            "❌ Invitation Declined\n\n"
            "You declined the game invitation.",
        )

        # Notify host
        try:
            await self._bot.send_message(
                chat_id=invite_data["host_id"],
                text=(
                    f"❌ @{user.username or user.first_name} "
                    "declined your invitation."
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
                "❌ You're not in any private game.",
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
                "❌ Game session not found.",
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
                "❌ Game session not found.",
                reply_to_message_id=reply_to_id,
            )
            return

        if game.state != GameState.INITIAL:
            await self._send_response(
                update,
                "❌ Cannot leave started game. Use buttons to fold.",
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
                "❌ You're not in any private game.",
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
                "\n".join(
                    [
                        "🚪 You left the private game.",
                        "Your seat is now available for others.",
                    ]
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
            "\n".join(
                [
                    "🚪 You left the private game.",
                    "Your seat is now available for others.",
                ]
            ),
            reply_to_message_id=reply_to_id,
        )

        lobby_text = f"👋 {player_mention} left the game.\n"

        if new_host is not None:
            lobby_text += f"👑 {new_host.mention_markdown} is now the host!\n\n"
        else:
            lobby_text += "\n"

        lobby_text += (
            f"👥 Current players ({len(game.players)}/{MAX_PLAYERS})"
            ":\n"
        )

        for idx, player in enumerate(game.players, 1):
            lobby_text += f"{idx}. {player.mention_markdown}"
            if idx == 1:
                lobby_text += " 👑 (Host)"
            lobby_text += "\n"

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

        await self._view.send_private_game_status(
            chat_id=update.effective_chat.id,
            host_name="You",
            stake_name="Medium (25/50)",
            game_code="CODE123",
            current_players=3,
            max_players=6,
            min_players=2,
            player_names=["Alice", "Bob", "You"],
            can_start=False,
        )

    async def handle_player_action(
        self,
        user_id: int,
        chat_id: int,
        action_type: str,
        raise_amount: Optional[int] = None,
    ) -> bool:
        """
        Handle player action from inline button callback.

        This is the modern unified action handler used by Phase 6
        single living message UI.

        Args:
            user_id: Telegram user ID (int)
            chat_id: Chat ID where game is happening (int)
            action_type: Action as string
                ("fold", "call", "check", "raise", "all_in")
            raise_amount: Amount for raise actions (optional)

        Returns:
            True if action was processed successfully, False otherwise
        """

        user_id_str = str(user_id)
        chat_id_str = str(chat_id)

        try:
            chat_id_int = int(chat_id)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid chat_id provided for action buttons: %s",
                chat_id,
            )
            return False

        chat_data = self._application.chat_data.get(chat_id_int, {})
        game = chat_data.get(KEY_CHAT_DATA_GAME)

        if not game:
            logger.warning(
                "No active game found in chat %s for user action",
                chat_id_str,
            )
            return False

        if game.current_player_index >= len(game.players):
            logger.error("Invalid current_player_index in game %s", game.id)
            return False

        current_player = game.players[game.current_player_index]

        if current_player.user_id != user_id_str:
            logger.warning(
                "User %s tried to act but it's %s's turn",
                user_id_str,
                current_player.user_id,
            )
            return False

        if game.state != GameState.PLAYING:
            logger.warning(
                "User %s tried to act in non-playing game (state: %s)",
                user_id_str,
                game.state,
            )
            return False

        if current_player.state not in (
            PlayerState.ACTIVE,
            PlayerState.ALL_IN,
        ):
            logger.warning(
                "User %s in invalid state %s for action",
                user_id_str,
                current_player.state,
            )
            return False

        try:
            if action_type == "fold":
                current_player.state = PlayerState.FOLD
                action_text = f"{current_player.name} folded"

            elif action_type == "check":
                if game.max_round_rate > current_player.round_rate:
                    logger.warning(
                        "User %s tried to check but must call %d",
                        user_id_str,
                        game.max_round_rate - current_player.round_rate,
                    )
                    return False

                action_text = f"{current_player.name} checked"

            elif action_type == "call":
                call_amount = self._coordinator.player_call_or_check(
                    game, current_player
                )
                action_text = (
                    f"{current_player.name} called ${call_amount}"
                )

            elif action_type == "raise":
                if raise_amount is None or raise_amount <= 0:
                    logger.warning(
                        "User %s tried to raise without valid amount",
                        user_id_str,
                    )
                    return False

                min_raise = game.max_round_rate * 2

                if raise_amount < min_raise:
                    logger.warning(
                        "User %s raise %d below minimum %d",
                        user_id_str,
                        raise_amount,
                        min_raise,
                    )
                    return False

                self._coordinator.player_raise_bet(
                    game,
                    current_player,
                    raise_amount,
                )
                action_text = (
                    f"{current_player.name} raised to ${raise_amount}"
                )

            elif action_type == "all_in":
                all_in_amount = self._coordinator.player_all_in(
                    game, current_player
                )
                current_player.state = PlayerState.ALL_IN
                action_text = (
                    f"{current_player.name} went all-in for ${all_in_amount}"
                )

            else:
                logger.warning("Unknown action_type: %s", action_type)
                return False

            game.add_action(action_text)

            self._save_game(chat_id_int, game)

            turn_result, next_player = self._coordinator.process_game_turn(
                game
            )

            if turn_result == TurnResult.CONTINUE_ROUND:
                await self._coordinator._send_or_update_game_state(
                    game=game,
                    chat_id=chat_id_int,
                )

            elif turn_result == TurnResult.END_ROUND:
                (
                    new_state,
                    cards_to_deal,
                ) = self._coordinator.advance_game_street(game)

                game.state = new_state

                if cards_to_deal > 0:
                    for _ in range(cards_to_deal):
                        if not game.remain_cards:
                            logger.debug(
                                "Attempted to deal community card but deck "
                                "empty",
                            )
                            break

                        card = game.remain_cards.pop()
                        game.cards_table.append(card)
                        logger.debug("Dealt community card %s", card)

                self._coordinator.commit_round_bets(game)
                self._save_game(chat_id_int, game)

                await self._coordinator._send_or_update_game_state(
                    game=game,
                    chat_id=chat_id_int,
                )

            elif turn_result == TurnResult.END_GAME:
                winners_results = self._coordinator.finish_game_with_winners(
                    game
                )

                game.state = GameState.FINISHED
                self._save_game(chat_id_int, game)

                await self._show_game_results(
                    chat_id=chat_id_str,
                    game=game,
                    winners_results=winners_results,
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
                "You have already received the bonus today\n"
                f"Your money: {self.value()}$"
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
            raise UserException("not enough money")

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
