"""Команды расписания: /today и /tomorrow.

Хендлеры тонкие: узнали дату по Москве, спросили логику, отправили текст.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import clock, texts
from app.schedule import ScheduleError, format_day, lessons_on

logger = logging.getLogger(__name__)

router = Router(name="schedule")


async def _answer_day(message: Message, target: date, today: date) -> None:
    try:
        day_lessons = lessons_on(target)
    except ScheduleError:
        # Файл расписания сломан — молчать нельзя, но и техническими деталями грузить незачем.
        logger.exception("Не удалось загрузить расписание для %s", target)
        await message.answer(texts.SCHEDULE_ERROR)
        return

    await message.answer(format_day(target, day_lessons, today=today))


@router.message(Command("today"))
async def handle_today(message: Message) -> None:
    today = clock.today()
    await _answer_day(message, today, today)


@router.message(Command("tomorrow"))
async def handle_tomorrow(message: Message) -> None:
    today = clock.today()
    await _answer_day(message, today + timedelta(days=1), today)
