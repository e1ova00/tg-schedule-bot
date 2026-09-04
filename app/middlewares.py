"""Middleware диспетчера: доступ к боту и общее соединение с базой.

Обе вешаются на уровень `Update`, поэтому работают сразу для всех типов апдейтов —
и для команд, и для нажатий на инлайн-кнопки, и для геопозиции.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import aiosqlite
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject, Update, User

from app import texts
from app.routing.base import Router

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AccessMiddleware(BaseMiddleware):
    """Пускает дальше только пользователей из ALLOWED_USER_IDS.

    Пустой список — бот открыт для всех: так удобно разрабатывать, пока свой Telegram ID
    ещё не вписан в .env.
    """

    def __init__(self, allowed_user_ids: Sequence[int]) -> None:
        self._allowed = frozenset(allowed_user_ids)

    @property
    def is_open(self) -> bool:
        """True, если список пуст и бот никого не отсекает."""
        return not self._allowed

    def is_allowed(self, user_id: int | None) -> bool:
        return self.is_open or (user_id is not None and user_id in self._allowed)

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        if self.is_open:
            return await handler(event, data)

        user: User | None = data.get("event_from_user")
        if self.is_allowed(user.id if user else None):
            return await handler(event, data)

        # Логируем id и username: по ним владелец решит, добавлять человека или нет.
        logger.warning(
            "Отказано в доступе: telegram_id=%s username=%s",
            user.id if user else "неизвестен",
            user.username if user else "неизвестен",
        )
        await self._reply_denied(event)
        return None

    @staticmethod
    async def _reply_denied(event: TelegramObject) -> None:
        """Вежливый отказ без подробностей. Сетевой сбой здесь ничего не должен ломать."""
        try:
            if isinstance(event, Update):
                if event.message is not None:
                    await event.message.answer(texts.ACCESS_DENIED)
                elif event.callback_query is not None:
                    await event.callback_query.answer(texts.ACCESS_DENIED, show_alert=True)
        except TelegramAPIError:
            logger.warning("Не удалось ответить на запрещённый апдейт", exc_info=True)


class DatabaseMiddleware(BaseMiddleware):
    """Кладёт общее соединение с SQLite в data под ключом `db`.

    Пользователей два-три, поэтому одного соединения на весь процесс достаточно:
    aiosqlite сам выстраивает запросы в очередь.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        data["db"] = self._connection
        return await handler(event, data)


class RouterMiddleware(BaseMiddleware):
    """Кладёт маршрутизатор в data под ключом `travel_router`.

    Ключ намеренно не «router»: у aiogram есть свой Router, и путаница в именах
    дорого обходится. Объект создаётся один раз при старте бота — внутри он держит
    HTTP-сессию, открывать её на каждый запрос было бы расточительно.
    """

    def __init__(self, travel_router: Router) -> None:
        self._router = travel_router

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        data["travel_router"] = self._router
        return await handler(event, data)


__all__ = ["AccessMiddleware", "DatabaseMiddleware", "RouterMiddleware"]
