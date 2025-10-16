#!/usr/bin/env python3

import logging
import redis

from telegram.ext import Application, AIORateLimiter

from pokerapp.config import Config
from pokerapp.pokerbotcontrol import PokerBotCotroller
from pokerapp.pokerbotmodel import PokerBotModel
from pokerapp.pokerbotview import PokerBotViewer


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


class PokerBot:
    def __init__(
        self,
        token: str,
        cfg: Config,
    ):
        self._application: Application = (
            Application.builder()
            .token(token)
            .rate_limiter(AIORateLimiter())
            .build()
        )

        kv = redis.Redis(
            host=cfg.REDIS_HOST,
            port=cfg.REDIS_PORT,
            db=cfg.REDIS_DB,
            password=cfg.REDIS_PASS if cfg.REDIS_PASS != "" else None
        )

        self._view = PokerBotViewer(bot=self._application.bot)
        self._model = PokerBotModel(
            view=self._view,
            bot=self._application.bot,
            kv=kv,
            cfg=cfg,
            application=self._application,
        )
        self._controller = PokerBotCotroller(self._model, self._application)

    def run(self) -> None:
        self._application.run_polling()
