#!/usr/bin/env python3

import asyncio
import datetime
import logging
from typing import Awaitable, Callable, List, Optional

import redis
from telegram import Bot, ReplyKeyboardMarkup, Update
from telegram.ext import Application, ContextTypes

from pokerapp.config import Config
from pokerapp.privatechatmodel import UserPrivateChatModel
from pokerapp.entities import (
    Game,
    GameState,
    Player,
    ChatId,
    UserId,
    UserException,
    Money,
    PlayerAction,
    PlayerState,
    Wallet,
    STAKE_PRESETS,
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

        # NEW: Replace old logic with coordinator
        self._coordinator = GameCoordinator()
        self._stake_config = STAKE_PRESETS[self._cfg.DEFAULT_STAKE_LEVEL]

        self._readyMessages = {}

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
        user_id = update.effective_message.from_user.id

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
        )

        dealer = 2 % len(game.players)
        game.trading_end_user_id = game.players[dealer].user_id

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

    async def _divide_cards(self, game: Game, chat_id: ChatId) -> None:
        for player in game.players:
            player.cards = [
                game.remain_cards.pop(),
                game.remain_cards.pop(),
            ]

        await self._send_cards_batch(game.players, chat_id)

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
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Begin flow for creating a private game."""

        user = update.effective_user

        await self._view.send_stake_selection(
            chat_id=update.effective_chat.id,
            user_id=user.id,
        )

    async def join_private_game(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Join a private game by using a game code."""

        await self._view.send_private_game_status(
            chat_id=update.effective_chat.id,
            host_name="Host TBD",
            stake_name="Stake TBD",
            game_code="CODE123",
            current_players=1,
            max_players=5,
            min_players=2,
            player_names=["PlaceholderUser"],
            can_start=False,
        )

    async def invite_player(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Send invitation to another player."""

        inviter = update.effective_user

        await self._view.send_player_invite(
            inviter_id=inviter.id,
            inviter_name=inviter.full_name,
            invitee_id=inviter.id,
            game_code="CODE123",
            stake_name="Medium (25/50)",
        )

    async def leave_private_game(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle user leaving lobby."""

        await update.effective_message.reply_text("🚪 You left the private lobby.")

    async def start_private_game(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Start private game if conditions met."""

        await update.effective_message.reply_text("🎰 Starting private game…")

    async def show_private_game_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
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
