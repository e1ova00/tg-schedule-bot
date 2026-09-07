"""Команда /addnote: заметка к любой паре в любой момент, не дожидаясь вопроса после пары.

Диалог короткий: дата → пара кнопкой → текст. Причём текст уже никто здесь не
обрабатывает: кнопка выбора пары присылает ровно тот же callback, что и кнопка «Есть»
в вопросе после пары (`note:has:L1216:2026-09-01`), поэтому дальше работает
`app/handlers/notes.py` — то же состояние `NoteEntry.text`, тот же расчёт срока, то же
подтверждение. Второго пути сохранения заметки в проекте нет и быть не должно.

Порядок хендлеров в файле — порядок их проверки: команды объявлены раньше, чем
«поймать любой текст в состоянии», иначе /cancel записался бы как дата.
"""

from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import clock, keyboards, texts, views
from app.handlers import common
from app.schedule import ScheduleError, lessons_on, parse_iso_date

logger = logging.getLogger(__name__)

router = Router(name="add-note")


class AddNote(StatesGroup):
    """Ожидание даты, к парам которой человек хочет привязать заметку.

    Состояния для текста заметки здесь нет намеренно — его ждёт `NoteEntry.text`
    из `app/handlers/notes.py`.
    """

    day = State()


async def offer_lessons(message: Message, day: date, state: FSMContext) -> None:
    """Показывает пары этого дня кнопками. Пустой день — не ошибка, а обычное дело."""
    try:
        day_lessons = lessons_on(day)
    except ScheduleError:
        logger.exception("Не удалось загрузить расписание для /addnote на %s", day)
        await state.clear()
        await message.answer(texts.SCHEDULE_ERROR)
        return

    # Дальше диалогом управляют кнопки: ждать текст даты больше не нужно, иначе
    # случайное сообщение в чат уйдёт «датой» и человек получит непонятную ошибку.
    await state.clear()

    if not day_lessons:
        await message.answer(views.format_no_lessons_to_note(day))
        return

    await message.answer(
        views.format_lesson_pick(day),
        reply_markup=keyboards.lesson_choice(day, day_lessons),
    )


@router.message(Command("addnote"))
async def handle_add_note(
    message: Message, command: CommandObject, state: FSMContext
) -> None:
    """/addnote — спросить дату. /addnote 2026-09-15 — сразу показать пары этого дня."""
    today = clock.today()
    raw = (command.args or "").strip()

    if raw:
        day = parse_iso_date(raw)
        if day is None:
            await message.answer(
                texts.ADDNOTE_BAD_DATE.format(value=common.shown_arg(raw))
            )
            return
        await offer_lessons(message, day, state)
        return

    await state.set_state(AddNote.day)
    await message.answer(
        texts.ADDNOTE_ASK_DATE, reply_markup=keyboards.add_note_days(today)
    )


@router.message(AddNote.day, Command("cancel"))
async def handle_add_note_cancel(message: Message, state: FSMContext) -> None:
    """/cancel во время выбора дня: выходим из диалога, ничего не создавая."""
    await state.clear()
    await message.answer(texts.ADDNOTE_CANCELLED)


@router.callback_query(
    F.data.startswith(f"{keyboards.CB_ADD_NOTE}:{keyboards.ADD_NOTE_DAY}:")
)
async def handle_add_note_day_button(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Кнопки «Сегодня» / «Завтра» — тот же путь, что и дата текстом."""
    day = keyboards.parse_add_note_day(callback.data)
    message = common.callback_message(callback)
    if day is None or message is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    await callback.answer()
    await common.hide_inline_keyboard(callback)
    await offer_lessons(message, day, state)


@router.message(AddNote.day)
async def handle_add_note_day(message: Message, state: FSMContext) -> None:
    """Дата текстом. Всё непонятное — вежливая просьба повторить, а не выход из диалога."""
    raw = (message.text or "").strip()
    # Команду датой считать нельзя: свои команды ловят роутеры выше, а чужую лучше
    # честно не понять, чем молча показать чей-то день.
    day = None if raw.startswith("/") else parse_iso_date(raw)
    if day is None:
        await message.answer(texts.ADDNOTE_BAD_DATE.format(value=common.shown_arg(raw)))
        return

    await offer_lessons(message, day, state)


__all__ = [
    "AddNote",
    "handle_add_note",
    "handle_add_note_cancel",
    "handle_add_note_day",
    "handle_add_note_day_button",
    "offer_lessons",
    "router",
]
