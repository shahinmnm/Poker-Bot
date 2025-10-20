#!/usr/bin/env python3

import asyncio
import datetime
import json
import logging
import secrets
from typing import Awaitable, Callable, List, Optional, Union

import redis
from telegram import Bot, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackContext, ContextTypes
from telegram.helpers import escape_markdown

from pokerapp.config import Config, STAKE_PRESETS
from pokerapp.cards import get_shuffled_deck
from pokerapp.privatechatmodel import UserPrivateChatModel
from pokerapp.desk import KEY_CHAT_DATA_GAME, KEY_OLD_PLAYERS
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
)
from pokerapp.game_coordinator import GameCoordinator
from pokerapp.game_engine import TurnResult
from pokerapp.pokerbotview import PokerBotViewer
from pokerapp.kvstore import ensure_kv


logger = logging.getLogger(__name__)


DICE_MULT = 10
DICE_DELAY_SEC = 5
BONUSES = (5, 20, 40, 80, 160, 320)
DICES = "⚀⚁⚂⚃⚄⚅"

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

        # NEW: Replace old logic with coordinator
        self._coordinator = GameCoordinator()
        self._stake_config = STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL]

        self._readyMessages = {}

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

    def _save_game(self, chat_id: ChatId, game: Game) -> None:
        chat_data = self._application.chat_data.setdefault(chat_id, {})
        chat_data[KEY_CHAT_DATA_GAME] = game

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
        await self._view.send_photo(chat_id=chat_id)

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
        if datetime.datetime.today().weekday() == SATURDAY:
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

                    message = await self._view.send_desk_cards_img(
                        chat_id=private_chat_id,
                        cards=player.cards,
                        caption="Your cards",
                        disable_notification=False,
                    )

                    try:
                        rm_msg_id = private_chat.pop_message()
                        while rm_msg_id is not None:
                            try:
                                rm_msg_id_str = (
                                    rm_msg_id
                                    if isinstance(rm_msg_id, str)
                                    else rm_msg_id.decode('utf-8')
                                )
                                await self._view.remove_message(
                                    chat_id=private_chat_id,
                                    message_id=rm_msg_id_str,
                                )
                            except Exception as exc:
                                logger.debug(
                                    "Failed to remove old card message "
                                    "for %s: %s",
                                    player.user_id,
                                    exc,
                                )
                            rm_msg_id = private_chat.pop_message()

                        private_chat.push_message(
                            message_id=message.message_id
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to update private chat cache "
                            "for %s: %s",
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

        if hasattr(destination, "bot"):
            context = destination

            for player in game.players:
                try:
                    card_image = self._view.generate_hand_image(player.cards)
                    await context.bot.send_photo(
                        chat_id=player.user_id,
                        photo=card_image,
                        caption=(
                            "🃏 **Your Cards** 🃏\n\n"
                            "Game starting in group chat!\n"
                            f"Table stake: {game.table_stake}"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to send cards to %s: %s",
                        player.user_id,
                        exc,
                    )

            return

        await self._send_cards_batch(game.players, destination)

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
                game.last_turn_time = datetime.datetime.now()
                await self._view.send_turn_actions(
                    chat_id=chat_id,
                    game=game,
                    player=next_player,
                    money=next_player.wallet.value(),
                )
                return

    async def add_cards_to_table(
        self,
        count: int,
        game: Game,
        chat_id: ChatId,
    ) -> None:
        for _ in range(count):
            game.cards_table.append(game.remain_cards.pop())

        await self._view.send_desk_cards_img(
            chat_id=chat_id,
            cards=game.cards_table,
            caption=f"Current pot: {game.pot}$",
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

        diff = datetime.datetime.now() - game.last_turn_time
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

        player.state = PlayerState.FOLD

        await self._view.send_message(
            chat_id=update.effective_message.chat_id,
            text=f"{player.mention_markdown} {PlayerAction.FOLD.value}"
        )

        await self._start_betting_round(game, update.effective_message.chat_id)

    async def call_check(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)

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

    async def raise_rate_bet(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        raise_bet_rate: PlayerAction
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)

        try:
            action = PlayerAction.RAISE_RATE
            if player.round_rate == game.max_round_rate:
                action = PlayerAction.BET

            if player.wallet.value() < raise_bet_rate.value:
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
                amount=raise_bet_rate.value,
            )
        except UserException as e:
            await self._view.send_message(chat_id=chat_id, text=str(e))
            return

        await self._start_betting_round(game, chat_id)

    async def all_in(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        game = self._game_from_context(context)
        chat_id = update.effective_message.chat_id
        player = self._current_turn_player(game)
        mention = player.mention_markdown
        amount = self._coordinator.player_all_in(game, player)
        await self._view.send_message(
            chat_id=chat_id,
            text=f"{mention} {PlayerAction.ALL_IN.value} {amount}$"
        )
        player.state = PlayerState.ALL_IN
        await self._start_betting_round(game, chat_id)

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
            ex=3600  # Expire after 1 hour
        )

        # Link user to game
        user_game_key = ":".join(["user", str(user.id), "private_game"])
        self._kv.set(user_game_key, game_code, ex=3600)

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
            game_key,
            private_game.to_json(),
            ex=3600
        )

        # Link user to game
        user_game_key = ":".join(["user", str(user.id), "private_game"])
        self._kv.set(user_game_key, game_code, ex=3600)

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
            ex=3600
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
        """
        Get user's current balance from Redis.

        Args:
            user_id: Telegram user ID

        Returns:
            User balance (defaults to initial balance if not set)
        """
        balance_key = ":".join(["user", str(user_id), "balance"])
        balance = self._kv.get(balance_key)

        if balance is None:
            return getattr(self._cfg, "INITIAL_MONEY", 1000)

        return int(balance)

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
            "invited_at": datetime.datetime.now().isoformat(),
            "status": "pending",
        }
        self._kv.set(invite_key, json.dumps(invite_data), ex=3600)

        # Track pending invite
        pending_key = "user:" + str(target_user_id) + ":pending_invites"
        try:
            self._kv.sadd(pending_key, game_code)
            self._kv.expire(pending_key, 3600)
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
        game_key = ":".join(["private_game", game_code])
        game_data = self._kv.get(game_key)

        if not game_data:
            await self._send_response(
                update,
                f"❌ Game {game_code} not found or expired.",
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        if isinstance(game_data, bytes):
            game_data = game_data.decode("utf-8")

        private_game = PrivateGame.from_json(game_data)

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
                f"❌ Need at least {self._cfg.PRIVATE_MIN_PLAYERS} players to start!\n\n"
                f"Current: {len(accepted_players)} player(s)",
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
        min_buyin = int(stake_config["min_buyin"])

        # Validate ALL players have sufficient balance
        insufficient_players = []

        for player_id in accepted_players:
            balance = await self._get_user_balance(player_id)

            # Use coordinator's validation logic
            if not self._coordinator.can_player_join(balance, small_blind):
                # Try to resolve player name for error message
                username = self._lookup_user_by_username(player_id)
                display_name = username if username else f"Player {player_id}"
                insufficient_players.append((player_id, display_name, balance))

        # If any player lacks funds, reject start
        if insufficient_players:
            error_lines = ["❌ Cannot start game - insufficient funds:\n"]

            for player_id, name, balance in insufficient_players:
                error_lines.append(
                    f"• {name}: {balance}$ (need {min_buyin}$)"
                )

            error_lines.append(
                f"\nAll players need at least {min_buyin}$ to play."
            )

            await self._send_response(
                update,
                "\n".join(error_lines),
                reply_to_message_id=update.effective_message.message_id,
            )
            return

        # === STEP 2A: CREATE PLAYER OBJECTS ===

        chat_id = update.effective_chat.id
        players: List[Player] = []
        player_names: List[str] = []

        # Helper to resolve display names (reuse username cache)
        async def resolve_display_name(player_id: int) -> str:
            """Get cached username or fetch from Telegram."""

            # Try username cache first (populated during invitations)
            username = self._lookup_user_by_username(player_id)
            if username:
                return username

            # Fallback: Fetch from Telegram API
            try:
                if player_id == user_id:
                    # Host is the callback initiator
                    return (
                        getattr(user, "full_name", None)
                        or getattr(user, "username", None)
                        or str(player_id)
                    )

                chat = await self._bot.get_chat(player_id)
                return (
                    getattr(chat, "full_name", None)
                    or getattr(chat, "username", None)
                    or str(player_id)
                )
            except Exception as exc:
                logger.warning(
                    "Failed to resolve name for user %s: %s",
                    player_id,
                    exc,
                )
                return str(player_id)

        # Create Player objects for all accepted players
        for player_id in accepted_players:
            display_name = await resolve_display_name(player_id)

            # Create markdown mention for chat messages
            mention = "[{}](tg://user?id={})".format(
                escape_markdown(display_name, version=1),
                player_id,
            )

            # Create Player object with wallet manager
            players.append(
                Player(
                    user_id=player_id,
                    mention_markdown=mention,
                    wallet=WalletManagerModel(player_id, self._kv),
                    ready_message_id=None,  # No ready message in private games
                )
            )
            player_names.append(display_name)

        logger.info(
            "Created %d player objects for private game %s",
            len(players),
            game_code,
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
            "Initialized Game object for private game %s (mode=%s, players=%d)",
            game_code,
            game.mode.value,
            len(players),
        )

        # === TODO: STEP 2C - PERSIST GAME STATE TO REDIS ===

        # TODO: Game engine initialization (Step 2)
        # TODO: State cleanup (Step 3)

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
            self._kv.set(game_key, game.to_json(), ex=3600)

        # Link user to game
        user_game_key = "user:" + str(user.id) + ":private_game"
        self._kv.set(user_game_key, game_code, ex=3600)

        # Update invitation status
        invite_data["status"] = "accepted"
        self._kv.set(invite_key, json.dumps(invite_data), ex=3600)

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
        self._kv.set(invite_key, json.dumps(invite_data), ex=3600)

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
            self._kv.delete(":".join(["game", str(game_chat_id_int)]))

            game_code = getattr(game, "code", None)
            if game_code:
                self._kv.delete(":".join(["private_game", game_code]))

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


class WalletManagerModel(Wallet):
    def __init__(self, user_id: UserId, kv: Optional[redis.Redis]):
        self.user_id = user_id
        self._kv = ensure_kv(kv)

        key = self._prefix(self.user_id)
        if self._kv.get(key) is None:
            self._kv.set(key, DEFAULT_MONEY)

    @staticmethod
    def _prefix(id: int, suffix: str = ""):
        return "pokerbot:" + str(id) + suffix

    def _current_date(self) -> str:
        return datetime.datetime.utcnow().strftime("%d/%m/%y")

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
