"""Команда /route: сколько ехать прямо сейчас до корпуса ближайшей очной пары.

Хендлер тонкий: достал пользователя и ближайшую очную пару, спросил у маршрутизатора
время в пути, отдал готовый текст из `views`. Вся арифметика — в `app/routing`,
вся вёрстка — в `app/views.py`.
"""

from __future__ import annotations

import logging

import aiosqlite
from aiogram import Router as AiogramRouter
from aiogram.filters import Command
from aiogram.types import Message

from app import buildings, clock, texts, users, views
from app.routing.base import Router, RouterError
from app.schedule import ScheduleError, upcoming_offline_lesson

logger = logging.getLogger(__name__)

router = AiogramRouter(name="route")

# Смотрим сегодня и завтра. Дальше заглядывать нет смысла: /route отвечает на вопрос
# «ехать ли мне в ближайшее время», а не «когда следующая пара» — для этого есть /today.
LOOKAHEAD_DAYS = 2


@router.message(Command("route"))
async def handle_route(
    message: Message, db: aiosqlite.Connection, travel_router: Router
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    user = await users.get_user(db, user_id)
    if not users.is_onboarded(user) or user is None:
        await message.answer(texts.ROUTE_NOT_READY)
        return

    now = clock.now()
    try:
        found = upcoming_offline_lesson(now, days=LOOKAHEAD_DAYS)
    except ScheduleError:
        logger.exception("Не удалось загрузить расписание для /route")
        await message.answer(texts.SCHEDULE_ERROR)
        return

    if found is None:
        await message.answer(texts.ROUTE_NO_TRIPS)
        return

    day, lesson = found
    # Корпус берём у самой пары: это тот же адрес, что вернул бы building_for_date,
    # но без риска промахнуться, если первая пара дня уже началась и мы едем на вторую.
    destination = await _building_point(db, lesson.building)
    if destination is None:
        logger.error("Нет координат корпуса «%s» ни в базе, ни в константах", lesson.building)
        await message.answer(texts.ROUTE_FAILED)
        return

    origin = user.coordinates
    if origin is None:  # is_onboarded это уже проверил, но пусть тип будет честным
        await message.answer(texts.ROUTE_NOT_READY)
        return

    try:
        travel = await travel_router.travel_time(
            origin, destination, user.transport_mode or "", now
        )
    except RouterError:
        # Сервис маршрутов упал — молчать нельзя, техническими деталями грузить незачем.
        logger.warning("Маршрут посчитать не удалось", exc_info=True)
        await message.answer(texts.ROUTE_FAILED)
        return
    except Exception:  # noqa: BLE001 — /route не должен ронять бота ни при каких данных
        logger.exception("Неожиданная ошибка при расчёте маршрута")
        await message.answer(texts.ROUTE_FAILED)
        return

    await message.answer(
        views.format_route(day, lesson, travel, user.transport_mode, now.date())
    )


async def _building_point(
    db: aiosqlite.Connection, address: str
) -> tuple[float, float] | None:
    """Координаты корпуса: сначала из базы (кэш геокодинга), потом из констант."""
    try:
        saved = await buildings.get_building(db, address)
    except Exception:  # noqa: BLE001 — база не должна ломать ответ, есть запасной путь
        logger.warning("Не удалось прочитать корпус «%s» из базы", address, exc_info=True)
        saved = None

    if saved is not None:
        return (saved.latitude, saved.longitude)
    return buildings.building_coordinates(address)
