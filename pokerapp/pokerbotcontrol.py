#!/usr/bin/env python3

import inspect
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from telegram import (
    BotCommand,
    CallbackQuery,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
)

from pokerapp.entities import Game, Player, PlayerAction
from pokerapp.notify_utils import LoggerHelper, NotificationManager
from pokerapp.i18n import translation_manager
from pokerapp.kvstore import ensure_kv
from pokerapp.pokerbotmodel import (
    PokerBotModel,
    PlayerActionValidation,
    PreparedPlayerAction,
)
from pokerapp.request_cache import RequestCache
from pokerapp.middleware import PokerBotMiddleware
from pokerapp.menu_state import MenuLocation, MenuState, MENU_HIERARCHY
from pokerapp.live_message import UnicodeTextFormatter

if TYPE_CHECKING:
    from pokerapp.live_message import LiveMessageManager
    from pokerapp.pokerbotview import PokerBotViewer

logger = logging.getLogger(__name__)
log_helper = LoggerHelper.for_logger(logger)


class ControllerTextKeys:
    FOLD_CONFIRM_PROMPT = "popup.toast.fold_prompt"
    LOBBY_LEFT = "controller.lobby.left"
    INVITE_ACCEPT_HELP = "controller.invite.accept_help"
    INVITE_DECLINE_HELP = "controller.invite.decline_help"
    NAVIGATION_ALREADY_MAIN = "navigation.already_main"
    GROUP_ADMIN_BAN = "controller.group_admin.ban"
    GROUP_ADMIN_STOP = "controller.group_admin.stop"
    GROUP_ADMIN_LANGUAGE = "controller.group_admin.language"
    FOLD_EXPIRED = "controller.fold.expired"
    FOLD_SUCCESS = "controller.fold.success"
    FOLD_FAILURE = "controller.fold.failure"
    FOLD_CANCELLED = "controller.fold.cancelled"
    ACTION_SUBMITTED = "controller.action.submitted"
    ACTION_CHECK = "controller.action.check"
    ACTION_CALL = "controller.action.call"
    ACTION_CALL_CHECK = "controller.action.call_check"
    ACTION_FOLD = "controller.action.fold"
    ACTION_RAISE_TO = "controller.action.raise_to"
    ACTION_RAISE = "controller.action.raise"
    ACTION_ALL_IN = "controller.action.all_in"
    ACTION_BET = "controller.action.bet"
    ACTION_CONFIRMED = "controller.action.confirmed"
    ACTION_INVALID_FORMAT = "controller.action.invalid_format"
    ACTION_INVALID_RAISE_FORMAT = "controller.action.invalid_raise_format"
    ACTION_INVALID_RAISE_AMOUNT = "controller.action.invalid_raise_amount"
    ACTION_INVALID_VERSION = "controller.action.invalid_version"
    ACTION_MISSING_CONTEXT = "controller.action.missing_context"
    ACTION_UNKNOWN = "controller.action.unknown"
    ACTION_FAILED_STATE = "controller.action.failed_state"
    ACTION_FAILED_GENERIC = "controller.action.failed_generic"
    ACTION_HANDLER_UNAVAILABLE = "controller.action.handler_unavailable"
    RAISE_ERROR_CONTEXT = "controller.raise.error_context"
    RAISE_ERROR_USER = "controller.raise.error_user"
    RAISE_ERROR_NO_GAME = "controller.raise.error_no_game"
    RAISE_ERROR_UNAVAILABLE = "controller.raise.error_unavailable"
    RAISE_ERROR_INVALID = "controller.raise.error_invalid"
    RAISE_ERROR_SELECTION = "controller.raise.error_selection"
    RAISE_ERROR_EXPIRED = "controller.raise.error_expired"
    RAISE_ERROR_CHOOSE_AMOUNT = "controller.raise.error_choose_amount"
    RAISE_ERROR_UNKNOWN = "controller.raise.error_unknown"
    RAISE_PICKER_UNAVAILABLE = "controller.raise.picker_unavailable"
    RAISE_PICK_AMOUNT = "controller.raise.pick_amount"


class ControllerCommandKeys:
    START = "controller.commands.start"
    READY = "controller.commands.ready"
    PRIVATE = "controller.commands.private"
    JOIN = "controller.commands.join"
    INVITE = "controller.commands.invite"
    ACCEPT = "controller.commands.accept"
    DECLINE = "controller.commands.decline"
    LEAVE = "controller.commands.leave"
    MONEY = "controller.commands.money"
    CARDS = "controller.commands.cards"
    BAN = "controller.commands.ban"
    STOP = "controller.commands.stop"
    HELP = "controller.commands.help"


class PokerBotController:
    """Controller for handling Telegram updates and routing to model."""

    _STALE_QUERY_MESSAGE = (
        "Query is too old and response timeout expired or query id is invalid"
    )

    def __init__(
        self,
        model: PokerBotModel,
        application: Application,
        *,
        kv=None,
    ) -> None:
        """
        Initialize controller with handlers.

        Args:
            model: PokerBotModel instance
            application: PTB Application instance
        """
        self._model = model
        self._application = application
        self._view: "PokerBotViewer" = model._view
        self._kv = ensure_kv(kv if kv is not None else getattr(model, "_kv", None))
        translation_manager.attach_kvstore(self._kv)
        self._pending_fold_confirmations: dict[Tuple[int, str], PreparedPlayerAction] = {}
        self._middleware = PokerBotMiddleware(self._model, self._kv)

        application.add_handler(CommandHandler("ready", self._handle_ready))
        application.add_handler(CommandHandler("start", self._handle_start))
        application.add_handler(CommandHandler("menu", self._handle_menu))
        application.add_handler(CommandHandler("stop", self._handle_stop))
        application.add_handler(CommandHandler("money", self._handle_money))
        application.add_handler(CommandHandler("ban", self._handle_ban))
        application.add_handler(CommandHandler("cards", self._handle_cards))
        application.add_handler(CommandHandler("help", self._handle_help))
        application.add_handler(
            CommandHandler("private", self._handle_private)
        )
        application.add_handler(
            CommandHandler("join", self._handle_join_private)
        )
        application.add_handler(
            CommandHandler("invite", self._handle_invite)
        )
        application.add_handler(
            CommandHandler("accept", self._handle_accept_invite)
        )
        application.add_handler(
            CommandHandler("decline", self._handle_decline_invite)
        )
        application.add_handler(
            CommandHandler("leave", self._handle_leave_private)
        )
        application.add_handler(
            CommandHandler("language", self._handle_language)
        )
        # Register callback query handlers before the fallback handler
        application.add_handler(
            CallbackQueryHandler(
                self._handle_stake_selection,
                pattern=r"^stake:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_invite_accept_callback,
                pattern=r"^invite_accept:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_invite_decline_callback,
                pattern=r"^invite_decline:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_private_start_callback,
                pattern=r"^private_start:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_private_leave_callback,
                pattern=r"^private_leave:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_language_selection,
                pattern=r"^lang:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_action_button,
                pattern=r"^action:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_menu_callback,
                pattern=(
                    r"^(?:"
                    r"private_(?:view_game|manage|create)"
                    r"|group_(?:view_game|join|leave|start|admin)"
                    r"|view_invites|settings|help)"
                    r"$"
                ),
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_lobby_sit,
                pattern=r"^lobby_sit$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_lobby_leave,
                pattern=r"^lobby_leave$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_lobby_start,
                pattern=r"^lobby_start$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_nav_back,
                pattern="^nav_back$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_nav_home,
                pattern="^nav_home$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._handle_callback_query)
        )

        application.post_init = self._post_init

        log_helper.info("ControllerInit", "Handlers registered")

    @property
    def middleware(self) -> PokerBotMiddleware:
        """Expose middleware helpers for composing menu context."""

        return self._middleware

    @property
    def view(self) -> "PokerBotViewer":
        """Return the associated view implementation."""

        return self._view

    def _translate(
        self,
        key: str,
        *,
        update: Optional[Update] = None,
        query=None,
        user_id: Optional[int] = None,
        language_code: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Translate *key* using the best available language context."""

        if update is not None:
            user = update.effective_user
            if user is None and update.callback_query is not None:
                user = update.callback_query.from_user
            if user is not None:
                user_id = getattr(user, "id", user_id)
                language_code = getattr(user, "language_code", language_code)

        if query is not None and user_id is None:
            query_user = getattr(query, "from_user", None)
            if query_user is not None:
                user_id = getattr(query_user, "id", user_id)
                language_code = getattr(query_user, "language_code", language_code)

        resolved_language: Optional[str] = None
        if user_id is not None:
            resolved_language = translation_manager.get_user_language_or_detect(
                user_id,
                telegram_language_code=language_code,
            )

        return translation_manager.t(
            key,
            user_id=user_id,
            lang=resolved_language or language_code,
            **kwargs,
        )

    def _get_live_manager(self) -> Optional["LiveMessageManager"]:
        """Safely retrieve :class:`LiveMessageManager` from the view.

        Returns:
            LiveMessageManager if available, otherwise ``None``.

        Notes:
            The controller defensively guards access to the view to prevent
            attribute errors if initialization order changes or the view has
            not finished constructing its live message helpers.
        """

        try:
            view = getattr(self, "_view", None)
            if view is None:
                log_helper.warn(
                    "ControllerViewMissing",
                    "Controller has no view reference; cannot access LiveMessageManager",
                )
                return None

            live_manager = getattr(view, "_live_manager", None)
            if live_manager is None:
                log_helper.debug(
                    "LiveManagerMissing",
                    "View exists but LiveMessageManager not initialized",
                )

            return live_manager

        except Exception as exc:  # pragma: no cover - defensive logging
            log_helper.error(
                "LiveManagerAccessError",
                f"Failed to retrieve LiveMessageManager: {exc}",
                exc_info=True,
            )
            return None

    @staticmethod
    def _find_player(game: Game, user_id: int) -> Optional[Player]:
        """Return the player in *game* that matches *user_id*, if any."""

        for player in getattr(game, "players", []):
            if str(getattr(player, "user_id", "")) == str(user_id):
                return player

        return None

    @staticmethod
    def _should_confirm_fold(game: Game, player: Player) -> bool:
        """Return ``True`` when folding should prompt for confirmation."""

        pot_size = getattr(game, "pot", 0)
        current_bet = getattr(game, "max_round_rate", 0)
        player_invested = getattr(player, "round_rate", 0)

        is_big_pot = pot_size > (5 * current_bet)
        has_stake = player_invested > (0.1 * pot_size)

        return is_big_pot and has_stake

    @staticmethod
    def _resolve_game_identifier(game: Game | None) -> str:
        """Derive a stable identifier for *game* suitable for callback routing."""

        if game is None:
            return "unknown"

        game_id = getattr(game, "id", None)
        if not game_id:
            return f"gid_{id(game)}"

        return str(game_id)

    def _store_pending_fold(
        self,
        user_id: int,
        prepared_action: PreparedPlayerAction,
    ) -> str:
        """Persist *prepared_action* and return its confirmation identifier."""

        game_identifier = self._resolve_game_identifier(prepared_action.game)
        self._pending_fold_confirmations[(user_id, game_identifier)] = prepared_action
        return game_identifier

    def _get_pending_fold(
        self,
        user_id: int,
        game_identifier: str | None = None,
    ) -> Optional[PreparedPlayerAction]:
        """Retrieve a queued fold confirmation for *user_id*.

        Args:
            user_id: Telegram identifier for the player.
            game_identifier: Specific game identifier, if available.
        """

        if game_identifier is not None:
            return self._pending_fold_confirmations.get((user_id, game_identifier))

        for (pending_user_id, _), action in self._pending_fold_confirmations.items():
            if pending_user_id == user_id:
                return action

        return None

    def _clear_pending_fold(
        self,
        user_id: int,
        game_identifier: str | None = None,
    ) -> None:
        """Remove cached fold confirmation(s) for *user_id*."""

        if game_identifier is not None:
            self._pending_fold_confirmations.pop((user_id, game_identifier), None)
            return

        keys_to_remove = [
            key for key in self._pending_fold_confirmations if key[0] == user_id
        ]
        for key in keys_to_remove:
            self._pending_fold_confirmations.pop(key, None)

    async def handle_fold(
        self,
        user_id: int,
        game: Game,
        confirmed: bool = False,
        *,
        prepared_action: Optional[PreparedPlayerAction] = None,
        query=None,
    ) -> Optional[bool]:
        """Process a fold request, optionally prompting for confirmation.

        Args:
            user_id: Telegram identifier of the acting user.
            game: Active game context for the fold.
            confirmed: ``True`` when the user has already confirmed.
            prepared_action: Pre-validated action metadata, if available.
            query: Callback query instance used for popups/toasts.

        Returns:
            ``True`` when the fold action executed successfully,
            ``False`` on failure, and ``None`` if awaiting confirmation.
        """

        player = (
            prepared_action.current_player
            if prepared_action is not None
            else self._find_player(game, user_id)
        )

        if player is None:
            log_helper.warn(
                "FoldPlayerMissing",
                "Unable to locate player for fold action",
                user_id=user_id,
                game_id=getattr(game, "id", None),
            )
            return False

        game_identifier = self._resolve_game_identifier(
            prepared_action.game if prepared_action is not None else game
        )

        if not confirmed:
            if self._should_confirm_fold(game, player):
                if prepared_action is None:
                    log_helper.warn(
                        "FoldActionMissing",
                        "Prepared action required for confirmation but missing",
                        user_id=user_id,
                        game_id=game_identifier,
                    )
                    return False

                confirmation_key = self._store_pending_fold(
                    user_id, prepared_action
                )
                await self._view.show_fold_confirmation(
                    chat_id=player.user_id,
                    pot_size=getattr(game, "pot", 0),
                    player_invested=player.round_rate,
                    confirmation_key=confirmation_key,
                    user_id=player.user_id,
                )
                if query is not None:
                    await NotificationManager.toast(
                        query,
                        text=self._translate(
                            ControllerTextKeys.FOLD_CONFIRM_PROMPT,
                            query=query,
                        ),
                        event="FoldConfirmPrompt",
                    )
                return None

            self._clear_pending_fold(user_id, game_identifier)

        action_to_execute = prepared_action
        if action_to_execute is None:
            action_to_execute = self._get_pending_fold(
                user_id, game_identifier
            )

        self._clear_pending_fold(user_id, game_identifier)

        if action_to_execute is None:
            log_helper.warn(
                "FoldActionMissing",
                "No prepared fold action available",
                user_id=user_id,
                game_id=getattr(game, "id", None),
            )
            return False

        success = await self._model.execute_player_action(action_to_execute)
        return success

    @classmethod
    def _is_stale_callback_query_error(cls, error: BadRequest) -> bool:
        """Return True when Telegram reports an expired callback query."""

        return cls._STALE_QUERY_MESSAGE in str(error)

    async def _respond_to_query(
        self,
        query,
        text: str | None = None,
        *,
        show_alert: bool = False,
        event: str = "ControllerPopup",
        context: CallbackContext | None = None,
        fallback_chat_id: int | None = None,
    ) -> bool:
        """Centralized callback responder with optional fallback messaging."""

        if (
            text is None
            or not show_alert
            or not (context and fallback_chat_id)
        ):
            return await NotificationManager.popup(
                query,
                text=text,
                show_alert=show_alert,
                event=event,
            )

        return await NotificationManager.popup_with_fallback(
            query,
            text=text,
            bot=context.bot if context else None,
            fallback_chat_id=fallback_chat_id,
            show_alert=show_alert,
            event=event,
        )

    async def _safe_query_answer(
        self,
        query: Optional[CallbackQuery],
        *,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """Answer callback queries while gracefully handling stale references."""

        if query is None:
            return False

        try:
            await query.answer(text=text, show_alert=show_alert)
            return True
        except BadRequest as error:
            if self._is_stale_callback_query_error(error):
                log_helper.debug(
                    "CallbackAnswerStale",
                    "Callback query expired before answer",
                    text=text,
                )
                return False
            log_helper.warn(
                "CallbackAnswerFailed",
                "Failed to answer callback query",
                error=str(error),
                text=text,
            )
        except Exception as exc:  # pragma: no cover - defensive
            log_helper.error(
                "CallbackAnswerError",
                "Unexpected error while answering callback",
                error=str(exc),
                text=text,
            )
        return False

    async def _post_init(self, application: Application) -> None:
        """Set up bot command descriptions in Telegram UI."""
        default_lang = translation_manager.DEFAULT_LANGUAGE

        def _command_text(key: str) -> str:
            return translation_manager.t(key, lang=default_lang)

        commands = [
            BotCommand("start", _command_text(ControllerCommandKeys.START)),
            BotCommand("ready", _command_text(ControllerCommandKeys.READY)),
            BotCommand("private", _command_text(ControllerCommandKeys.PRIVATE)),
            BotCommand("join", _command_text(ControllerCommandKeys.JOIN)),
            BotCommand("invite", _command_text(ControllerCommandKeys.INVITE)),
            BotCommand("accept", _command_text(ControllerCommandKeys.ACCEPT)),
            BotCommand("decline", _command_text(ControllerCommandKeys.DECLINE)),
            BotCommand("leave", _command_text(ControllerCommandKeys.LEAVE)),
            BotCommand("money", _command_text(ControllerCommandKeys.MONEY)),
            BotCommand("cards", _command_text(ControllerCommandKeys.CARDS)),
            BotCommand("ban", _command_text(ControllerCommandKeys.BAN)),
            BotCommand("stop", _command_text(ControllerCommandKeys.STOP)),
            BotCommand("help", _command_text(ControllerCommandKeys.HELP)),
        ]

        try:
            await application.bot.set_my_commands(commands)
            log_helper.info(
                "CommandSetup",
                "Bot commands registered in Telegram UI",
            )
        except Exception as exc:  # pragma: no cover - Telegram API
            log_helper.error(
                "CommandSetup",
                "Failed to register commands",
                error=str(exc),
            )

    async def _handle_ready(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /ready command."""
        await self._model.ready(update, context)

    async def _persist_menu_state(
        self,
        *,
        user_id: Optional[int],
        chat_id: Optional[int],
        location: MenuLocation,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist the active menu location for navigation features."""

        if user_id is None or chat_id is None:
            return

        state = MenuState(
            chat_id=chat_id,
            location=(
                location.value if isinstance(location, MenuLocation) else str(location)
            ),
            context_data=context_data or {},
            timestamp=time.time(),
        )

        await self.middleware.menu_state.set_state(state)

    async def _handle_start(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /start command."""
        user = update.effective_user
        if user is not None:
            translation_manager.get_user_language_or_detect(
                user.id,
                telegram_language_code=getattr(user, "language_code", None),
            )
        await self._model.start(update, context)

        chat = update.effective_chat
        message = update.effective_message
        if user is None or chat is None or message is None:
            return

        await self._persist_menu_state(
            user_id=user.id,
            chat_id=chat.id,
            location=MenuLocation.MAIN_MENU,
        )

        menu_context = await self.middleware.build_menu_context(
            chat_id=chat.id,
            chat_type=chat.type,
            user_id=user.id,
            language_code=getattr(user, "language_code", None),
            chat=chat,
        )

        welcome_text = translation_manager.t(
            "msg.welcome",
            lang=menu_context.language_code,
        )
        plain_welcome = UnicodeTextFormatter.strip_all_html(welcome_text)
        await message.reply_text(plain_welcome)

        await self.view._send_menu(chat.id, menu_context)

    async def _handle_menu(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Display context-aware menu for /menu command."""

        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return

        await self._persist_menu_state(
            user_id=user.id,
            chat_id=chat.id,
            location=MenuLocation.MAIN_MENU,
        )

        menu_context = await self.middleware.build_menu_context(
            chat_id=chat.id,
            chat_type=chat.type,
            user_id=user.id,
            language_code=getattr(user, "language_code", None),
            chat=chat,
        )
        await self.view._send_menu(chat.id, menu_context)

    async def _safe_navigation_update(
        self,
        query: CallbackQuery,
        target_location: MenuLocation,
        error_context: str,
    ) -> bool:
        """Safely update menu state with comprehensive error handling."""

        message = query.message
        if message is None:
            return False

        chat = message.chat
        chat_id = chat.id

        try:
            new_state = MenuState(
                chat_id=chat_id,
                location=target_location.value,
                context_data={},
                timestamp=time.time(),
            )

            await self._middleware.menu_state.set_state(new_state)

            user = query.from_user
            if user is None:
                raise ValueError("CallbackQuery missing from_user")

            menu_context = await self._middleware.build_menu_context(
                chat_id=chat_id,
                chat_type=chat.type,
                user_id=user.id,
                language_code=(user.language_code or "en"),
                chat=chat,
            )

            if menu_context.is_private_chat():
                await self.view._send_private_menu(
                    chat_id=chat_id,
                    context=menu_context,
                )
            else:
                await self.view._send_group_menu(
                    chat_id=chat_id,
                    context=menu_context,
                )

            await query.answer()
            return True

        except Exception as exc:  # pragma: no cover - defensive
            self._logger.error(
                "Navigation error during %s for chat %d: %s",
                error_context,
                chat_id,
                exc,
                exc_info=True,
            )

            try:
                user = query.from_user
                language = user.language_code if user else None
                translator = translation_manager.get_translator(language)
                error_msg = translator("error.navigation_failed")
                await query.answer(error_msg, show_alert=True)
            except Exception:
                user_id = getattr(user, "id", None) if 'user' in locals() else None
                language_code = (
                    getattr(user, "language_code", None)
                    if 'user' in locals()
                    else None
                )
                await query.answer(
                    translation_manager.t(
                        "error.navigation_failed",
                        user_id=user_id,
                        lang=language_code,
                    ),
                    show_alert=True,
                )

            return False

    async def _handle_nav_back(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle back button navigation."""

        query = update.callback_query
        if not query:
            return

        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        try:
            current_state = await self.middleware.menu_state.get_state(chat.id)

            if current_state is None:
                target_location = MenuLocation.MAIN
            else:
                try:
                    current_location = MenuLocation(current_state.location)
                except ValueError:
                    self._logger.warning(
                        "Invalid menu location '%s' for chat %d, falling back to MAIN",
                        current_state.location,
                        chat.id,
                    )
                    current_location = MenuLocation.MAIN

                parent_location_value = MENU_HIERARCHY.get(current_location)
                if parent_location_value is None:
                    await query.answer(
                        self._translate(
                            ControllerTextKeys.NAVIGATION_ALREADY_MAIN,
                            query=query,
                        )
                    )
                    return

                if isinstance(parent_location_value, MenuLocation):
                    target_location = parent_location_value
                else:
                    try:
                        target_location = MenuLocation(parent_location_value)
                    except ValueError:
                        self._logger.error(
                            "Invalid parent location '%s' in hierarchy",
                            parent_location_value,
                        )
                        target_location = MenuLocation.MAIN

            self._middleware._metrics.record_navigation("back")

            await self._safe_navigation_update(
                query,
                target_location,
                "back navigation",
            )

        except Exception as exc:  # pragma: no cover - defensive
            self._logger.error(
                "Unexpected error in _handle_nav_back: %s",
                exc,
                exc_info=True,
            )
            await query.answer(
                self._translate(
                    "error.navigation_failed",
                    query=query,
                ),
                show_alert=True,
            )

    async def _handle_nav_home(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle home button navigation."""

        query = update.callback_query
        if not query:
            return

        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        try:
            self._middleware._metrics.record_navigation("home")

            await self._safe_navigation_update(
                query,
                MenuLocation.MAIN,
                "home navigation",
            )

        except Exception as exc:  # pragma: no cover - defensive
            self._logger.error(
                "Unexpected error in _handle_nav_home: %s",
                exc,
                exc_info=True,
            )
            await query.answer(
                self._translate(
                    "error.navigation_failed",
                    query=query,
                ),
                show_alert=True,
            )

    async def _handle_stop(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /stop command."""
        await self._model.stop(
            user_id=update.effective_message.from_user.id
        )

    async def _handle_cards(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /cards command."""
        await self._model.send_cards_to_user(update, context)

    async def _handle_ban(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /ban command."""
        await self._model.ban_player(update, context)

    async def _handle_money(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /money command."""
        await self._model.bonus(update, context)

    async def _handle_help(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /help command with game rules."""
        help_text = self._translate(
            "help.full_text",
            update=update,
        )
        plain_help = UnicodeTextFormatter.strip_all_html(help_text)
        await update.effective_message.reply_text(plain_help)

    async def _handle_language(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /language command - show language selection menu."""

        user = update.effective_user
        message = update.effective_message
        chat = update.effective_chat

        if not user or not message or not chat:
            return

        stored_user_lang = self._kv.get_user_language(user.id)
        user_lang = translation_manager.resolve_language(
            user_id=user.id,
            lang=stored_user_lang,
        )
        self._kv.set_user_language(user.id, user_lang)

        active_lang = user_lang
        origin = "private_settings"
        if chat.type in ("group", "supergroup"):
            chat_lang = self._kv.get_chat_language(chat.id)
            if chat_lang:
                active_lang = translation_manager.resolve_language(lang=chat_lang)
            else:
                active_lang = translation_manager.DEFAULT_LANGUAGE
                self._kv.set_chat_language(chat.id, active_lang)
            origin = "group_settings"

        self._view.set_language_context(active_lang, user_id=user.id)

        await self._view.send_language_menu(
            chat_id=chat.id,
            language_code=active_lang,
            reply_to_message_id=message.message_id,
            origin=origin,
        )

    async def _handle_language_selection(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle language selection callback."""

        query = update.callback_query
        if not query or not query.data:
            return

        user = query.from_user
        if not user:
            return

        parts = query.data.split(":", 3)
        if len(parts) < 2:
            return

        command = parts[1]
        origin = parts[2] if len(parts) > 2 else None

        if command == "back":
            await self._view.answer_callback_query(query_id=query.id)
            message = query.message
            chat = message.chat if message else None

            if origin == "stake" and chat and message:
                if chat.type in ("group", "supergroup"):
                    lang_for_menu = (
                        self._kv.get_chat_language(chat.id)
                        or translation_manager.DEFAULT_LANGUAGE
                    )
                else:
                    lang_for_menu = (
                        self._kv.get_user_language(user.id)
                        or translation_manager.DEFAULT_LANGUAGE
                    )
                lang_for_menu = translation_manager.resolve_language(
                    user_id=user.id,
                    lang=lang_for_menu,
                )
                user_name = (
                    getattr(user, "full_name", None)
                    or getattr(user, "first_name", None)
                    or getattr(user, "username", "")
                )
                await self._view.send_stake_selection(
                    chat_id=chat.id,
                    user_name=user_name,
                    message_id=message.message_id,
                    language_code=lang_for_menu,
                )
            return

        if command == "open":
            chat = query.message.chat if query.message else update.effective_chat
            if not chat or not user:
                await self._respond_to_query(query)
                return

            target = parts[2] if len(parts) > 2 else None

            if target in {"group_settings", "group_menu"}:
                language_seed = (
                    self._kv.get_chat_language(chat.id)
                    or translation_manager.DEFAULT_LANGUAGE
                )
            else:
                language_seed = (
                    self._kv.get_user_language(user.id)
                    or translation_manager.DEFAULT_LANGUAGE
                )

            language_seed = translation_manager.resolve_language(
                user_id=user.id,
                lang=language_seed,
            )

            await self._view.send_language_menu(
                chat_id=chat.id,
                language_code=language_seed,
                message_id=query.message.message_id if query.message else None,
                origin=target,
            )

            await self._respond_to_query(query)
            return

        if command == "set":
            if len(parts) < 3:
                return
            lang_code = parts[2]
            origin = parts[3] if len(parts) > 3 else origin
        else:
            lang_code = command

        resolved_lang = translation_manager.resolve_language(
            user_id=user.id,
            lang=lang_code,
        )

        apply_to_chat = origin in {"group_settings", "group_menu"}
        apply_to_user = origin not in {"group_settings", "group_menu"}

        if apply_to_user:
            self._kv.set_user_language(user.id, resolved_lang)

            if hasattr(self, "_view"):
                self._view.set_language_context(resolved_lang, user_id=user.id)

        message = query.message
        chat = message.chat if message else update.effective_chat

        if chat and chat.type in ("group", "supergroup") and apply_to_chat:
            self._kv.set_chat_language(chat.id, resolved_lang)

        confirmation_key = (
            "settings.group_language_changed"
            if apply_to_chat
            else "settings.language_changed"
        )

        confirmation = translation_manager.t(
            confirmation_key,
            user_id=user.id,
            lang=resolved_lang,
        )

        await self._view.answer_callback_query(
            query_id=query.id,
            text=confirmation,
            show_alert=True,
        )

        # Re-render any active UI (live games, menus, etc.) that depend on the
        # translation context before showing the refreshed language picker.
        if hasattr(self, "_model") and hasattr(self._model, "refresh_language_for_user"):
            await self._model.refresh_language_for_user(user.id)

        if chat and message:
            await self._view.send_language_menu(
                chat_id=chat.id,
                language_code=resolved_lang,
                message_id=message.message_id,
                origin=origin,
            )

        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.MAIN_MENU,
                context_data={},
            )

            menu_context = await self.middleware.build_menu_context(
                chat_id=chat.id,
                chat_type=chat.type,
                user_id=user.id,
                language_code=getattr(user, "language_code", None),
                chat=chat,
            )
            await self.view._send_menu(chat.id, menu_context)

    async def _handle_button_clicked(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle inline button clicks for player actions."""
        query_data = update.callback_query.data

        if query_data == PlayerAction.CHECK.value:
            await self._model.call_or_check(update, context)
        elif query_data == PlayerAction.CALL.value:
            await self._model.call_or_check(update, context)
        elif query_data == PlayerAction.FOLD.value:
            await self._model.fold(update, context)
        elif query_data == str(PlayerAction.SMALL.value):
            await self._model.raise_rate_bet(
                update, context, PlayerAction.SMALL
            )
        elif query_data == str(PlayerAction.NORMAL.value):
            await self._model.raise_rate_bet(
                update, context, PlayerAction.NORMAL
            )
        elif query_data == str(PlayerAction.BIG.value):
            await self._model.raise_rate_bet(
                update, context, PlayerAction.BIG
            )
        elif query_data == PlayerAction.ALL_IN.value:
            await self._model.all_in(update, context)

    async def _handle_lobby_sit(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Handle lobby "Sit at Table" button."""

        query = update.callback_query
        if query is None:
            return

        await self._respond_to_query(query)
        await self._model.ready(update, context)

    async def _handle_lobby_leave(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Handle lobby "Leave Table" button."""

        query = update.callback_query
        if query is None:
            return

        chat = update.effective_chat
        user = query.from_user

        if chat is None or user is None:
            await self._respond_to_query(query)
            return

        await self._model.remove_lobby_player(
            context=context,
            chat_id=chat.id,
            user_id=user.id,
        )
        await self._respond_to_query(
            query,
            text=self._translate(
                ControllerTextKeys.LOBBY_LEFT,
                query=query,
            ),
        )

    async def _handle_lobby_start(
        self, update: Update, context: CallbackContext
    ) -> None:
        """Handle lobby "Start Game" button."""

        query = update.callback_query
        if query is None:
            return

        await self._respond_to_query(query)
        await self._model.start(update, context)

    async def _handle_private(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /private command to create private game."""

        await self._model.create_private_game(update, context)

    async def _handle_join_private(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /join command to join private game by code."""

        await self._model.join_private_game(update, context)

    async def _handle_invite(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /invite command to invite player to private game."""

        await self._model.invite_player(update, context)

    async def _handle_accept_invite(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /accept command (manual acceptance without button)."""

        accept_text = self._translate(
            ControllerTextKeys.INVITE_ACCEPT_HELP, update=update
        )
        plain_accept = UnicodeTextFormatter.strip_all_html(accept_text)
        await update.effective_message.reply_text(plain_accept)

    async def _handle_decline_invite(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /decline command (manual decline without button)."""

        decline_text = self._translate(
            ControllerTextKeys.INVITE_DECLINE_HELP, update=update
        )
        plain_decline = UnicodeTextFormatter.strip_all_html(decline_text)
        await update.effective_message.reply_text(plain_decline)

    async def _handle_leave_private(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle /leave command to leave private game lobby."""

        await self._model.leave_private_game(update, context)

    async def _handle_invite_accept_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle invitation acceptance via inline button."""

        query = update.callback_query

        if not query or not query.data:
            return

        query_user = getattr(query, "from_user", None)
        if query_user is not None and getattr(query_user, "id", None) is not None:
            translation_manager.get_user_language_or_detect(
                query_user.id,
                telegram_language_code=getattr(query_user, "language_code", None),
            )

        user_id = getattr(query_user, "id", None)
        user_language_code = getattr(query_user, "language_code", None)
        resolved_language = translation_manager.resolve_language(
            user_id=user_id,
            lang=user_language_code,
        )

        def _translate_for_query(key: str, **kwargs: Any) -> str:
            return self._translate(
                key,
                query=query,
                user_id=user_id,
                language_code=user_language_code,
                **kwargs,
            )

        def _format_amount(amount: Optional[int]) -> str:
            return translation_manager.format_currency(
                amount or 0,
                language=resolved_language,
            )

        await self._respond_to_query(query)
        await self._model.accept_invitation(update, context)

    async def _handle_invite_decline_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle invitation decline via inline button."""

        query = update.callback_query

        if not query or not query.data:
            return
        query_user = getattr(query, "from_user", None)
        if query_user is not None and getattr(query_user, "id", None) is not None:
            translation_manager.get_user_language_or_detect(
                query_user.id,
                telegram_language_code=getattr(query_user, "language_code", None),
            )

        await self._respond_to_query(query)
        await self._model.decline_invitation(update, context)

    async def _handle_private_start_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle private game start button callback."""

        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)
        await self._model.start_private_game(update, context)

    async def _handle_private_leave_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle private game leave button callback."""

        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)
        await self._model.leave_private_game(update, context)

    async def _handle_menu_callback(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Dispatch context-aware menu button callbacks to handlers."""

        query = update.callback_query

        if query is None or not query.data:
            return

        action = query.data

        handlers = {
            "private_view_game": self._handle_menu_private_view_game,
            "private_manage": self._handle_menu_private_manage,
            "private_create": self._handle_menu_private_create,
            "view_invites": self._handle_menu_view_invites,
            "settings": self._handle_menu_settings,
            "help": self._handle_menu_help,
            "group_view_game": self._handle_menu_group_view_game,
            "group_join": self._handle_menu_group_join,
            "group_leave": self._handle_menu_group_leave,
            "group_start": self._handle_menu_group_start,
            "group_admin": self._handle_menu_group_admin,
        }

        handler = handlers.get(action)

        if handler is None:
            await self._respond_to_query(query)
            log_helper.warn("MenuCallbackUnknown", callback_data=action)
            return

        await handler(update, context)

    async def _handle_menu_private_view_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is not None:
            await self._respond_to_query(query)
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.PRIVATE_GAME_MANAGEMENT,
                context_data={},
            )
        await self._model.show_private_game_status(update, context)

    async def _handle_menu_private_manage(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is not None:
            await self._respond_to_query(query)
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.PRIVATE_GAME_MANAGEMENT,
                context_data={},
            )
        await self._model.show_private_game_status(update, context)

    async def _handle_menu_private_create(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is not None:
            await self._respond_to_query(query)
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.MAIN_MENU,
                context_data={},
            )
        await self._handle_private(update, context)

    async def _handle_menu_view_invites(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is not None:
            await self._respond_to_query(query)
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.INVITATIONS,
                context_data={},
            )
        await self._model.send_pending_invites_summary(update, context)

    async def _handle_menu_settings(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is not None:
            await self._respond_to_query(query)
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.SETTINGS,
                context_data={},
            )

            menu_context = await self.middleware.build_menu_context(
                chat_id=chat.id,
                chat_type=chat.type,
                user_id=user.id,
                language_code=getattr(user, "language_code", None),
                chat=chat,
            )

            await self.view.send_settings_menu(
                chat_id=chat.id,
                context=menu_context,
            )

    async def _handle_menu_help(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is not None:
            await self._respond_to_query(query)
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.HELP,
                context_data={},
            )
        await self._handle_help(update, context)

    async def _handle_menu_group_join(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.GROUP_LOBBY,
                context_data={},
            )
        await self._handle_lobby_sit(update, context)

    async def _handle_menu_group_leave(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.MAIN_MENU,
                context_data={},
            )
        await self._handle_lobby_leave(update, context)

    async def _handle_menu_group_start(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.GROUP_LOBBY,
                context_data={},
            )
        await self._handle_lobby_start(update, context)

    async def _handle_menu_group_view_game(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is None:
            return

        chat = update.effective_chat
        user = update.effective_user

        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.ACTIVE_GAME,
                context_data={},
            )

        if chat is None:
            await self._respond_to_query(query)
            return

        try:
            game = self._model._game(chat.id)
        except Exception as exc:  # pragma: no cover - defensive
            log_helper.warn("MenuGroupViewGameLookupFailed", error=str(exc))
            await self._respond_to_query(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_NO_GAME,
                    query=query,
                ),
            )
            return

        players = getattr(game, "players", []) or []
        if not players:
            await self._respond_to_query(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_NO_GAME,
                    query=query,
                ),
            )
            return

        current_player = None
        index = getattr(game, "current_player_index", -1)
        if 0 <= index < len(players):
            current_player = players[index]

        try:
            await self._model._coordinator._send_or_update_game_state(
                game=game,
                current_player=current_player,
                chat_id=chat.id,
            )
            await self._respond_to_query(query)
        except Exception as exc:  # pragma: no cover - network side-effects
            log_helper.warn("MenuGroupViewGameFailed", error=str(exc))
            await self._respond_to_query(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_NO_GAME,
                    query=query,
                ),
            )

    async def _handle_menu_group_admin(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        query = update.callback_query
        if query is None:
            return

        await self._respond_to_query(query)

        user = update.effective_user
        chat = update.effective_chat
        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.ADMIN_PANEL,
                context_data={},
            )

        title = self._translate("menu.group.admin_panel", query=query)
        help_lines = [
            title,
            "",
            self._translate(ControllerTextKeys.GROUP_ADMIN_BAN, query=query),
            self._translate(ControllerTextKeys.GROUP_ADMIN_STOP, query=query),
            self._translate(ControllerTextKeys.GROUP_ADMIN_LANGUAGE, query=query),
        ]

        target_chat = update.effective_chat
        message = query.message

        chat_id = None
        if message and message.chat:
            chat_id = message.chat.id
        elif target_chat:
            chat_id = target_chat.id

        if chat_id is not None:
            await self._view.send_message(chat_id=chat_id, text="\n".join(help_lines))

    async def _handle_callback_query(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle legacy callback data fallback."""
        query = update.callback_query

        if not query or not query.data:
            return

        query_user = getattr(query, "from_user", None)
        if query_user is not None and getattr(query_user, "id", None) is not None:
            translation_manager.get_user_language_or_detect(
                query_user.id,
                telegram_language_code=getattr(query_user, "language_code", None),
            )

        callback_data = query.data
        user_id = getattr(getattr(query, "from_user", None), "id", None)

        if callback_data.startswith("confirm_fold"):
            _, _, confirmation_key = callback_data.partition(":")
            if not confirmation_key:
                confirmation_key = None

            if user_id is None:
                await self._respond_to_query(
                    query,
                    self._translate(
                        ControllerTextKeys.FOLD_EXPIRED,
                        query=query,
                    ),
                    event="FoldConfirm",
                )
                return

            pending = self._get_pending_fold(user_id, confirmation_key)

            if pending is None:
                await self._respond_to_query(
                    query,
                    self._translate(
                        ControllerTextKeys.FOLD_EXPIRED,
                        query=query,
                    ),
                    event="FoldConfirm",
                )
                return

            result = await self.handle_fold(
                user_id=user_id,
                game=pending.game,
                confirmed=True,
                prepared_action=pending,
            )

            message = query.message
            if message is not None:
                try:
                    await self._view.remove_markup(
                        chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    log_helper.warn(
                        "FoldConfirmCleanupFailed",
                        "Failed to clear confirmation keyboard",
                        error=str(exc),
                    )

            if result:
                await NotificationManager.toast(
                    query,
                    text=self._translate(
                        ControllerTextKeys.FOLD_SUCCESS,
                        query=query,
                    ),
                    event="ActionToast",
                )
            else:
                await self._respond_to_query(
                    query,
                    self._translate(
                        ControllerTextKeys.FOLD_FAILURE,
                        query=query,
                    ),
                    event="FoldConfirm",
                )
            return

        if callback_data.startswith("cancel_fold"):
            _, _, confirmation_key = callback_data.partition(":")
            if not confirmation_key:
                confirmation_key = None

            if user_id is not None:
                self._clear_pending_fold(user_id, confirmation_key)

            message = query.message
            if message is not None:
                try:
                    await self._view.remove_markup(
                        chat_id=message.chat_id,
                        message_id=message.message_id,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    log_helper.warn(
                        "FoldCancelCleanupFailed",
                        "Failed to clear confirmation keyboard",
                        error=str(exc),
                    )

            query_id = getattr(query, "id", None)
            if query_id is not None:
                await self._view.answer_callback_query(
                    query_id,
                    self._translate(
                        ControllerTextKeys.FOLD_CANCELLED,
                        query=query,
                    ),
                )
            else:
                await self._respond_to_query(
                    query,
                    self._translate(
                        ControllerTextKeys.FOLD_CANCELLED,
                        query=query,
                    ),
                    event="FoldConfirmCancel",
                )
            return

        # Acknowledge the callback immediately for legacy handlers
        await self._respond_to_query(query)

        # Legacy fallback for old button format
        player_action_callbacks = {
            PlayerAction.CHECK.value,
            PlayerAction.CALL.value,
            PlayerAction.FOLD.value,
            PlayerAction.ALL_IN.value,
            str(PlayerAction.SMALL.value),
            str(PlayerAction.NORMAL.value),
            str(PlayerAction.BIG.value),
        }

        if callback_data in player_action_callbacks:
            await self._model.middleware_user_turn(
                self._handle_button_clicked
            )(update, context)
        else:
            # Unknown callback - ignore silently
            log_helper.warn(
                "CallbackUnknown",
                callback_data=callback_data,
            )

    def _build_action_toast(
        self,
        action_type: str,
        validation: PlayerActionValidation,
    ) -> str:
        """Return a short toast message describing the applied action."""

        prepared = validation.prepared_action
        if prepared is None:
            return self._translate(ControllerTextKeys.ACTION_SUBMITTED)

        user_id = prepared.user_id
        language = translation_manager.resolve_language(user_id=user_id)

        def _translate(key: str, **kwargs: Any) -> str:
            return self._translate(
                key,
                user_id=user_id,
                language_code=language,
                **kwargs,
            )

        game = prepared.game
        player = prepared.current_player

        if action_type == "check":
            return _translate(ControllerTextKeys.ACTION_CHECK)

        if action_type == "call":
            call_amount = max(game.max_round_rate - player.round_rate, 0)
            if call_amount > 0:
                amount_display = translation_manager.format_currency(
                    call_amount,
                    language=language,
                )
                return _translate(
                    ControllerTextKeys.ACTION_CALL,
                    amount=amount_display,
                )
            return _translate(ControllerTextKeys.ACTION_CALL_CHECK)

        if action_type == "fold":
            return _translate(ControllerTextKeys.ACTION_FOLD)

        if action_type == "raise":
            if prepared.raise_amount:
                amount_display = translation_manager.format_currency(
                    prepared.raise_amount,
                    language=language,
                )
                return _translate(
                    ControllerTextKeys.ACTION_RAISE_TO,
                    amount=amount_display,
                )
            return _translate(ControllerTextKeys.ACTION_RAISE)

        if action_type == "all_in":
            return _translate(ControllerTextKeys.ACTION_ALL_IN)

        return self._translate(ControllerTextKeys.ACTION_SUBMITTED)

    async def _handle_raise_callback(
        self,
        query,
        context: CallbackContext,
    ) -> dict:
        """Handle raise_* callbacks that manage the amount selector."""

        data = getattr(query, "data", "") or ""
        result: dict = {"handled": False}

        if not data.startswith("action:raise_"):
            return result

        message = query.message
        if message is None:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_CONTEXT,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        chat_id = message.chat_id
        user_id = getattr(query.from_user, "id", None)
        if user_id is None:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_USER,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        game = self._model._game(chat_id)
        if game is None:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_NO_GAME,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        game_identifier = str(getattr(game, "id", ""))
        live_manager = self._get_live_manager()
        if live_manager is None:
            log_helper.warn(
                "RaiseLiveUpdateFailed",
                "Cannot update live message for raise flow (LiveManager unavailable)",
                user_id=user_id,
                chat_id=chat_id,
            )
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_UNAVAILABLE,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        current_player = None
        index = getattr(game, "current_player_index", -1)
        if 0 <= index < len(game.players):
            current_player = game.players[index]

        parts = data.split(":")
        if len(parts) < 3:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_INVALID,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            result["handled"] = True
            return result

        if parts[0] != "action":
            return result

        action = parts[1]

        def _parse_version_and_game(raw_parts: List[str], start_index: int) -> Tuple[Optional[int], Optional[str]]:
            version_val: Optional[int] = None
            game_val: Optional[str] = None
            idx = start_index
            if idx < len(raw_parts):
                try:
                    version_val = int(raw_parts[idx])
                    idx += 1
                except ValueError:
                    version_val = None
            if idx < len(raw_parts):
                game_val = raw_parts[idx]
            return version_val, game_val

        if action == "raise_amt":
            if len(parts) < 4:
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_SELECTION,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            selection_key = parts[2]
            message_version, game_id = _parse_version_and_game(parts, 3)
            if game_id is None or game_id != game_identifier:
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_EXPIRED,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            if (
                message_version is not None
                and message_version != game.get_live_message_version()
            ):
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_EXPIRED,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            success = await live_manager.present_raise_selector(
                chat_id=chat_id,
                game=game,
                current_player=current_player,
                user_id=user_id,
                message_id=message.message_id,
                message_version=message_version,
                selection_key=selection_key,
            )

            if not success:
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_UNAVAILABLE,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            selection_key, selected_option = live_manager.get_raise_selection(
                chat_id,
                user_id,
            )
            if selected_option is not None:
                selection_display: str = selection_key or "?"
                if selected_option.kind == "all_in":
                    selection_display = "ALL-IN"
                elif selected_option.amount is not None:
                    selection_display = translation_manager.format_currency(
                        selected_option.amount,
                        language=translation_manager.resolve_language(user_id=user_id),
                    )
                logger.info("🎯 Raise selected %s by user %s", selection_display, user_id)

            await NotificationManager.popup(
                query,
                text=None,
                show_alert=False,
                event="RaiseSelectAck",
            )

            result["handled"] = True
            return result

        if action == "raise_back":
            message_version, game_id = _parse_version_and_game(parts, 2)
            if game_id is None or game_id != game_identifier:
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_EXPIRED,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            await live_manager.restore_action_keyboard(
                chat_id=chat_id,
                game=game,
                current_player=current_player,
                message_id=message.message_id,
            )
            live_manager.clear_raise_selection(chat_id, user_id)

            await NotificationManager.popup(
                query,
                text=None,
                show_alert=False,
                event="RaiseSelectAck",
            )

            result["handled"] = True
            return result

        if action == "raise_confirm":
            message_version, game_id = _parse_version_and_game(parts, 2)
            if game_id is None or game_id != game_identifier:
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_EXPIRED,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            if (
                message_version is not None
                and message_version != game.get_live_message_version()
            ):
                await NotificationManager.popup(
                    query,
                    text=self._translate(
                        ControllerTextKeys.RAISE_ERROR_EXPIRED,
                        query=query,
                    ),
                    show_alert=False,
                    event="RaiseSelectError",
                )
                result["handled"] = True
                return result

            selection_key, option = live_manager.get_raise_selection(
                chat_id,
                user_id,
            )
            if selection_key is None or option is None:
                logger.info("💬 Raise confirm popup issued to %s", user_id)
                await self._safe_query_answer(
                    query,
                    text="💬 Please choose a raise amount first!",
                    show_alert=False,
                )
                result["handled"] = True
                return result

            action_type = "raise"
            raise_amount = option.amount
            if option.kind == "all_in":
                action_type = "all_in"
                raise_amount = None

            await live_manager.restore_action_keyboard(
                chat_id=chat_id,
                game=game,
                current_player=current_player,
                message_id=message.message_id,
            )
            live_manager.clear_raise_selection(chat_id, user_id)

            result.update(
                {
                    "handled": False,
                    "action": {
                        "action_type": action_type,
                        "raise_amount": raise_amount,
                        "message_version": message_version,
                        "game_id": game_id,
                    },
                }
            )
            return result

        await NotificationManager.popup(
            query,
            text=self._translate(
                ControllerTextKeys.RAISE_ERROR_UNKNOWN,
                query=query,
            ),
            show_alert=False,
            event="RaiseSelectError",
        )
        result["handled"] = True
        return result

    async def _start_raise_selection(
        self,
        query,
        context: CallbackContext,
        *,
        game_id: Optional[str],
        message_version: Optional[int],
    ) -> None:
        """Handle the initial action:raise:start callback."""

        message = query.message
        if message is None:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_CONTEXT,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        chat_id = message.chat_id
        user_id = getattr(query.from_user, "id", None)
        if user_id is None:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_USER,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        game = self._model._game(chat_id)
        if game is None or game_id != getattr(game, "id", None):
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_EXPIRED,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        if (
            message_version is not None
            and message_version != game.get_live_message_version()
        ):
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_EXPIRED,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        live_manager = self._get_live_manager()
        if live_manager is None:
            log_helper.warn(
                "RaiseLiveUpdateFailed",
                "Cannot update live message for raise flow (LiveManager unavailable)",
                user_id=user_id,
                chat_id=chat_id,
            )
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_ERROR_UNAVAILABLE,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        current_player = None
        index = getattr(game, "current_player_index", -1)
        if 0 <= index < len(game.players):
            current_player = game.players[index]

        success = await live_manager.present_raise_selector(
            chat_id=chat_id,
            game=game,
            current_player=current_player,
            user_id=user_id,
            message_id=message.message_id,
            message_version=message_version,
            selection_key=None,
        )

        if not success:
            await NotificationManager.popup(
                query,
                text=self._translate(
                    ControllerTextKeys.RAISE_PICKER_UNAVAILABLE,
                    query=query,
                ),
                show_alert=False,
                event="RaiseSelectError",
            )
            return

        await NotificationManager.popup(
            query,
            text=self._translate(
                ControllerTextKeys.RAISE_PICK_AMOUNT,
                query=query,
            ),
            show_alert=False,
            event="RaiseSelectAck",
        )

    async def _handle_action_button(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle new action button format from inline keyboards.

        Supported formats include versioned actions and the dynamic raise flow.
        """

        query = update.callback_query

        if not query or not query.data:
            return

        query_user = getattr(query, "from_user", None)
        if query_user is not None and getattr(query_user, "id", None) is not None:
            translation_manager.get_user_language_or_detect(
                query_user.id,
                telegram_language_code=getattr(query_user, "language_code", None),
            )

        async def show_popup(
            message: str,
            is_alert: bool = True,
            *,
            fallback_chat_id: int | None = None,
        ) -> bool:
            """Show popups and fall back to chat messaging when needed."""

            return await self._respond_to_query(
                query,
                message,
                show_alert=is_alert,
                event="ActionPopup",
                context=context if fallback_chat_id else None,
                fallback_chat_id=fallback_chat_id,
            )

        try:
            data = query.data

            raise_result = await self._handle_raise_callback(query, context)
            if raise_result.get("handled"):
                return

            override = raise_result.get("action") if raise_result else None
            raise_amount: Optional[int] = None
            message_version: Optional[int] = None
            game_id: Optional[str] = None

            if override:
                action_type = override.get("action_type")
                raise_amount = override.get("raise_amount")
                message_version = override.get("message_version")
                game_id = override.get("game_id")
            else:
                parts = data.split(":")

                if len(parts) < 3:
                    log_helper.warn(
                        "ActionDataInvalid",
                        query_data=query.data,
                        reason="too_few_parts",
                    )
                    await show_popup(
                        _translate_for_query(
                            ControllerTextKeys.ACTION_INVALID_FORMAT
                        ),
                        is_alert=False,
                    )
                    return

                action_type = parts[1]

                if action_type == "raise" and len(parts) >= 3 and parts[2] == "start":
                    idx = 3
                    msg_version: Optional[int] = None
                    if idx < len(parts) - 1:
                        try:
                            msg_version = int(parts[idx])
                            idx += 1
                        except ValueError:
                            msg_version = None

                    if idx >= len(parts):
                        log_helper.warn(
                            "ActionDataInvalid",
                            query_data=query.data,
                            reason="missing_game_id",
                        )
                        await show_popup(
                            _translate_for_query(
                                ControllerTextKeys.ACTION_INVALID_FORMAT
                            ),
                            is_alert=False,
                        )
                        return

                    await self._start_raise_selection(
                        query,
                        context,
                        game_id=parts[idx],
                        message_version=msg_version,
                    )
                    return

                if action_type == "raise":
                    if len(parts) < 4:
                        log_helper.warn(
                            "ActionDataInvalid",
                            query_data=query.data,
                            reason="missing_raise_params",
                        )
                        await self._respond_to_query(
                            query,
                            _translate_for_query(
                                ControllerTextKeys.ACTION_INVALID_RAISE_FORMAT
                            ),
                            event="ActionPopup",
                        )
                        return

                    try:
                        raise_amount = int(parts[2])
                    except (ValueError, IndexError):
                        log_helper.warn(
                            "ActionDataInvalid",
                            query_data=query.data,
                            reason="invalid_raise_amount",
                        )
                        await self._respond_to_query(
                            query,
                            _translate_for_query(
                                ControllerTextKeys.ACTION_INVALID_RAISE_AMOUNT
                            ),
                            event="ActionPopup",
                        )
                        return

                    if len(parts) == 4:
                        game_id = parts[3]
                    else:
                        try:
                            message_version = int(parts[3])
                        except (ValueError, IndexError):
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="invalid_version",
                            )
                            await self._respond_to_query(
                                query,
                                _translate_for_query(
                                    ControllerTextKeys.ACTION_INVALID_VERSION
                                ),
                                event="ActionPopup",
                            )
                            return
                        try:
                            game_id = parts[4]
                        except IndexError:
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="missing_game_id",
                            )
                            await self._respond_to_query(
                                query,
                                _translate_for_query(
                                    ControllerTextKeys.ACTION_INVALID_FORMAT
                                ),
                                event="ActionPopup",
                            )
                            return
                else:
                    if len(parts) == 3:
                        game_id = parts[2]
                    else:
                        try:
                            message_version = int(parts[2])
                        except (ValueError, IndexError):
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="invalid_version",
                            )
                            await self._respond_to_query(
                                query,
                                _translate_for_query(
                                    ControllerTextKeys.ACTION_INVALID_VERSION
                                ),
                                event="ActionPopup",
                            )
                            return
                        try:
                            game_id = parts[3]
                        except IndexError:
                            log_helper.warn(
                                "ActionDataInvalid",
                                query_data=query.data,
                                reason="missing_game_id",
                            )
                            await self._respond_to_query(
                                query,
                                _translate_for_query(
                                    ControllerTextKeys.ACTION_INVALID_FORMAT
                                ),
                                event="ActionPopup",
                            )
                            return

            if not game_id:
                await show_popup(
                    _translate_for_query(ControllerTextKeys.ACTION_INVALID_FORMAT),
                    is_alert=False,
                )
                return

            user_id = query.from_user.id
            chat_id = query.message.chat_id if query.message else None

            if not chat_id:
                await self._respond_to_query(
                    query,
                    _translate_for_query(
                        ControllerTextKeys.ACTION_MISSING_CONTEXT
                    ),
                    event="ActionPopup",
                )
                return

            handle_action = getattr(self._model, "handle_player_action", None)

            if handle_action is None:
                log_helper.error(
                    "ActionDispatch",
                    "Model missing handle_player_action method",
                )
                await self._respond_to_query(
                    query,
                    _translate_for_query(
                        ControllerTextKeys.ACTION_HANDLER_UNAVAILABLE
                    ),
                    event="ActionPopup",
                )
                return

            signature = inspect.signature(handle_action)

            if "action_type" in signature.parameters and hasattr(
                self._model, "prepare_player_action"
            ) and hasattr(self._model, "execute_player_action"):
                cache = RequestCache()

                try:
                    validation: PlayerActionValidation = (
                        await self._model.prepare_player_action(
                            user_id=user_id,
                            chat_id=chat_id,
                            action_type=action_type,
                            raise_amount=raise_amount,
                            message_version=message_version,
                            cache=cache,
                        )
                    )

                    if (
                        not validation.success
                        or validation.prepared_action is None
                    ):
                        error_message = (
                            validation.message
                            or _translate_for_query(
                                ControllerTextKeys.ACTION_FAILED_GENERIC
                            )
                        )
                        await show_popup(
                            error_message,
                            is_alert=True,
                            fallback_chat_id=chat_id,
                        )
                        return

                    prepared_action = validation.prepared_action

                    toast_message = self._build_action_toast(
                        action_type,
                        validation,
                    )

                    if action_type == "fold" and prepared_action is not None:
                        fold_result = await self.handle_fold(
                            user_id=user_id,
                            game=prepared_action.game,
                            prepared_action=prepared_action,
                            query=query,
                        )

                        if fold_result is None:
                            return

                        success = fold_result
                    else:
                        success = await self._model.execute_player_action(
                            prepared_action,
                            cache=cache,
                        )
                finally:
                    cache.log_stats("ActionDispatch")

                if not success:
                    log_helper.warn(
                        "ActionExecution",
                        "Execution of player action failed after validation",
                        action_type=action_type,
                    )
                    await show_popup(
                        _translate_for_query(
                            ControllerTextKeys.ACTION_FAILED_STATE
                        ),
                        is_alert=True,
                        fallback_chat_id=chat_id,
                    )
                    return

                # Show instant feedback using toast system
                await NotificationManager.toast(
                    query,
                    text=toast_message,
                    event="ActionToast",
                )
                return

            if "action_type" in signature.parameters:
                # ✅ Toast feedback: instant confirmation for user
                if action_type == "fold":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_FOLD
                    )
                elif action_type == "check":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_CHECK
                    )
                elif action_type == "call":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_CALL,
                        amount=_format_amount(raise_amount),
                    )
                elif action_type == "raise":
                    if raise_amount:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_RAISE_TO,
                            amount=_format_amount(raise_amount),
                        )
                    else:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_RAISE
                        )
                elif action_type == "bet":
                    if raise_amount:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_BET,
                            amount=_format_amount(raise_amount),
                        )
                    else:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_CONFIRMED
                        )
                elif action_type == "all_in":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_ALL_IN
                    )
                else:
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_CONFIRMED
                    )

                # Send toast (non-blocking, clears button spinner)
                await NotificationManager.toast(
                    query,
                    text=toast_text,
                    event="ActionToast",
                )

                success = await handle_action(
                    user_id=user_id,
                    chat_id=chat_id,
                    action_type=action_type,
                    raise_amount=raise_amount,
                )
            else:
                legacy_map = {
                    "check": PlayerAction.CHECK,
                    "call": PlayerAction.CALL,
                    "fold": PlayerAction.FOLD,
                    "raise": PlayerAction.RAISE_RATE,
                    "all_in": PlayerAction.ALL_IN,
                }

                player_action = legacy_map.get(action_type)

                if player_action is None:
                    await self._respond_to_query(
                        query,
                        _translate_for_query(ControllerTextKeys.ACTION_UNKNOWN),
                    )
                    return

                legacy_amount = raise_amount if raise_amount is not None else 0

                # ✅ Toast feedback: instant confirmation for user
                if action_type == "fold":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_FOLD
                    )
                elif action_type == "check":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_CHECK
                    )
                elif action_type == "call":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_CALL,
                        amount=_format_amount(legacy_amount),
                    )
                elif action_type == "raise":
                    if legacy_amount:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_RAISE_TO,
                            amount=_format_amount(legacy_amount),
                        )
                    else:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_RAISE
                        )
                elif action_type == "bet":
                    if legacy_amount:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_BET,
                            amount=_format_amount(legacy_amount),
                        )
                    else:
                        toast_text = _translate_for_query(
                            ControllerTextKeys.ACTION_CONFIRMED
                        )
                elif action_type == "all_in":
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_ALL_IN
                    )
                else:
                    toast_text = _translate_for_query(
                        ControllerTextKeys.ACTION_CONFIRMED
                    )

                # Send toast (non-blocking, clears button spinner)
                await NotificationManager.toast(
                    query,
                    text=toast_text,
                    event="ActionToast",
                )

                success = await handle_action(
                    user_id=str(user_id),
                    chat_id=str(chat_id),
                    game_id=game_id,
                    action=player_action,
                    amount=legacy_amount,
                )

            if not success:
                await self._respond_to_query(
                    query,
                    _translate_for_query(
                        ControllerTextKeys.ACTION_FAILED_GENERIC
                    ),
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            log_helper.error(
                "ActionHandler",
                "Error handling action button",
                error=str(exc),
                exc_info=True,
            )
            await show_popup(
                translation_manager.t(
                    "msg.error.generic",
                    user_id=user_id,
                    lang=user_language_code,
                ),
                is_alert=True,
                fallback_chat_id=(
                    query.message.chat_id if query and query.message else None
                ),
            )
        finally:
            view = getattr(self, "_view", None)
            if view and hasattr(view, "get_render_cache_stats"):
                stats = view.get_render_cache_stats()
                total = stats.get("total", 0)
                if total:
                    logger.info(
                        "Render cache stats: %d hits / %d total (%.1f%% hit rate)",
                        stats.get("hits", 0),
                        total,
                        stats.get("hit_rate", 0.0),
                    )

    async def _handle_stake_selection(
        self,
        update: Update,
        context: CallbackContext,
    ) -> None:
        """Handle stake level selection from inline keyboard."""
        query = update.callback_query

        if not query or not query.data:
            return

        await self._respond_to_query(query)

        # Parse stake level from callback data (e.g., "stake:low" → "low")
        stake_level = query.data.split(":", 1)[1]

        user = query.from_user
        message = query.message
        chat = message.chat if message else None

        if stake_level == "cancel":
            cancel_text = self._translate(
                "msg.private.stake_menu.cancelled",
                query=query,
                user_id=getattr(user, "id", None),
            )
            await query.edit_message_text(cancel_text)
            return

        if stake_level == "language" and message and chat:
            if chat.type in ("group", "supergroup"):
                active_lang = (
                    self._kv.get_chat_language(chat.id)
                    or translation_manager.DEFAULT_LANGUAGE
                )
            else:
                active_lang = (
                    self._kv.get_user_language(user.id)
                    or translation_manager.DEFAULT_LANGUAGE
                )
            active_lang = translation_manager.resolve_language(
                user_id=user.id,
                lang=active_lang,
            )
            await self._view.send_language_menu(
                chat_id=chat.id,
                language_code=active_lang,
                message_id=message.message_id,
                origin="stake",
            )
            return

        if user and chat:
            await self._persist_menu_state(
                user_id=user.id,
                chat_id=chat.id,
                location=MenuLocation.PRIVATE_GAME_CREATION,
                context_data={"stake_level": stake_level},
            )

        # Call model to create game with selected stake
        await self._model.create_private_game_with_stake(
            update, context, stake_level
        )
