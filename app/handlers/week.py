"""Команда /week: вся неделя одним сообщением, с листанием кнопками.

Хендлер тонкий: посчитал понедельник нужной недели, попросил текст у
`app.schedule.format_week` (чистая функция, тестируется без Telegram) и отправил.
Кнопки «← Предыдущая» / «Следующая →» редактируют то же самое сообщение, а не
плодят новые: листать расписание в ленте из десяти одинаковых простыней неудобно.

В callback_data лежит понедельник нужной недели («week:go:2026-09-07»), а не сдвиг
на единицу: сообщение живёт долго, и относительный сдвиг со временем уехал бы.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app import clock, keyboards, texts
from app.handlers import common
from app.schedule import (
    ScheduleError,
    format_week,
    monday_of,
    parse_iso_date,
    week_in_range,
)

logger = logging.getLogger(__name__)

router = Router(name="week")

_WEEK = timedelta(days=7)


def render_week(monday: date, today: date) -> tuple[str, InlineKeyboardMarkup]:
    """Текст недели и кнопки листания. Кнопка не показывается, если дальше листать некуда."""
    text = format_week(monday, today=today)
    markup = keyboards.week_nav(
        monday,
        with_prev=week_in_range(monday - _WEEK, today),
        with_next=week_in_range(monday + _WEEK, today),
    )
    return text, markup


@router.message(Command("week"))
async def handle_week(message: Message, command: CommandObject) -> None:
    """/week — текущая неделя, /week 2026-09-15 — неделя, в которую попадает эта дата."""
    today = clock.today()
    raw = (command.args or "").strip()

    target = today if not raw else parse_iso_date(raw)
    if target is None:
        await message.answer(texts.WEEK_BAD_DATE.format(value=common.shown_arg(raw)))
        return

    monday = monday_of(target)
    if not week_in_range(monday, today):
        # Кнопки такую неделю не показывают — саму команду нужно ограничить так же,
        # иначе можно улететь на десятки лет вперёд и застрять там без кнопок обратно.
        await message.answer(texts.WEEK_EDGE)
        return

    try:
        text, markup = render_week(monday, today)
    except ScheduleError:
        logger.exception("Не удалось загрузить расписание для /week на %s", target)
        await message.answer(texts.SCHEDULE_ERROR)
        return

    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith(f"{keyboards.CB_WEEK}:{keyboards.WEEK_GO}:"))
async def handle_week_nav(callback: CallbackQuery) -> None:
    """Листание недель: редактируем то же сообщение, чтобы чат не зарастал."""
    monday = keyboards.parse_week_monday(callback.data)
    message = common.callback_message(callback)
    if monday is None or message is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    today = clock.today()
    if not week_in_range(monday, today):
        # Кнопку в такую неделю мы не рисуем, но старое сообщение может пережить год.
        await callback.answer(texts.WEEK_EDGE, show_alert=True)
        return

    try:
        text, markup = render_week(monday, today)
    except ScheduleError:
        logger.exception("Не удалось загрузить расписание для недели %s", monday)
        await callback.answer(texts.SCHEDULE_ERROR, show_alert=True)
        return

    await callback.answer()
    try:
        await message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001 — сообщение могло устареть, но ответить всё равно надо
        logger.info("Не получилось отредактировать неделю %s, шлю новым сообщением", monday)
        await message.answer(text, reply_markup=markup)


__all__ = ["handle_week", "handle_week_nav", "render_week", "router"]
