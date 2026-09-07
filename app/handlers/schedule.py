"""Команды расписания: /today, /tomorrow и /day на произвольную дату.

Хендлеры тонкие: узнали дату по Москве, спросили логику, отправили текст. Все три
команды печатают день одной и той же функцией `_answer_day` — форматирование живёт
в `app/schedule.py` и дублироваться не должно.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app import clock, texts
from app.handlers import common
from app.schedule import ScheduleError, format_day, lessons_on, parse_iso_date

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


@router.message(Command("day"))
async def handle_day(message: Message, command: CommandObject) -> None:
    """/day 2026-09-15 — любой день семестра. Без аргумента — сегодняшний."""
    today = clock.today()
    raw = (command.args or "").strip()

    target = today if not raw else parse_iso_date(raw)
    if target is None:
        await message.answer(texts.DAY_BAD_DATE.format(value=common.shown_arg(raw)))
        return

    await _answer_day(message, target, today)
