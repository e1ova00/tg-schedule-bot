"""Команда /preview: во сколько бот разбудит и почему.

Нужна для отладки и для спокойствия: будильник считается ровно теми же функциями,
что и настоящий, но по запросу, без отправки и без записи в журнал.

`/preview` — про завтра, `/preview 2026-09-15` — про любую дату. Второй вариант
позволяет руками проверить оба дня недели при обеих чётностях, не дожидаясь их
в календаре.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from html import escape

import aiosqlite
from aiogram import Router as AiogramRouter
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import alarm, clock, texts, users, views
from app.config import DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES, Config
from app.routing.base import Router
from app.schedule import ScheduleError

logger = logging.getLogger(__name__)

router = AiogramRouter(name="preview")

# Сколько символов непонятного аргумента показать в ответе об ошибке формата.
MAX_SHOWN_ARG = 50


def parse_preview_date(raw: str | None, today: date) -> date | None:
    """«2026-09-15» -> дата. Пусто — завтра. Непонятный текст — None (ошибка формата).

    Чистая функция: её можно проверить без Telegram.
    """
    value = (raw or "").strip()
    if not value:
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.message(Command("preview"))
async def handle_preview(
    message: Message,
    command: CommandObject,
    db: aiosqlite.Connection,
    travel_router: Router,
    config: Config | None = None,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    now = clock.now()
    today = now.date()

    day = parse_preview_date(command.args, today)
    if day is None:
        # Текст пользователя возвращается в HTML-сообщении, поэтому экранируем его
        # и обрезаем: иначе «/preview <b>» сломал бы разметку ответа.
        shown = escape((command.args or "").strip()[:MAX_SHOWN_ARG])
        await message.answer(texts.PREVIEW_BAD_DATE.format(value=shown))
        return

    user = await users.get_user(db, user_id)
    if not users.is_onboarded(user) or user is None:
        await message.answer(texts.PREVIEW_NOT_READY)
        return

    fallback_minutes = (
        config.alarm_fallback_travel_minutes
        if config is not None
        else DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES
    )

    try:
        plan = await alarm.build_alarm_plan(
            db,
            user,
            day,
            travel_router,
            at=now,
            fallback_travel_minutes=fallback_minutes,
        )
    except ScheduleError:
        logger.exception("Не удалось загрузить расписание для /preview на %s", day)
        await message.answer(texts.SCHEDULE_ERROR)
        return
    except Exception:  # noqa: BLE001 — предпросмотр не должен ронять бота
        logger.exception("Неожиданная ошибка в /preview на %s", day)
        await message.answer(texts.SCHEDULE_ERROR)
        return

    await message.answer(views.format_preview(plan, user, today))
