"""Заметки по парам: вопрос после пары, список /notes и кнопки «Сделал / Не сделал».

Хендлеры тонкие: разобрать нажатие, дёрнуть `app/db.py` и `app/notes.py`, ответить
готовым текстом из `app/views.py`. Сроки и чётность недели считаются в `app/notes.py`
и проверяются тестами без Telegram.

Единственный диалог здесь — ввод текста заметки: бот спрашивает «что задали?» и ждёт
следующее сообщение (состояние `NoteEntry.text`), как в онбординге. Пустых заметок-заглушек
не создаём: пока текста нет, в базе ничего не появляется.

Порядок хендлеров в файле — это порядок их проверки. Команды (`/notes`, `/cancel`)
объявлены раньше, чем «поймать любой текст в состоянии»: иначе посреди диалога они
записались бы в заметку вместо того, чтобы сработать.
"""

from __future__ import annotations

import logging
from datetime import date

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import clock, db as db_module, keyboards, notes, texts, views
from app.handlers import common
from app.schedule import Lesson, ScheduleError, lesson_by_id

logger = logging.getLogger(__name__)

router = Router(name="notes")

# Ключи в данных FSM: какая пара ждёт текст заметки.
LESSON_ID_KEY = "note_lesson_id"
LESSON_DATE_KEY = "note_lesson_date"

# Сколько заметок показывать в списке. Больше — уже не список, а лента: остальное
# останется в базе, а человеку скажем, что показали не всё.
MAX_NOTES_SHOWN = 15


class NoteEntry(StatesGroup):
    """Ожидание текста заметки после кнопки «Есть»."""

    text = State()


def _lesson(lesson_id: str) -> Lesson | None:
    """Пара по id. Сломанное расписание не должно ронять ни список, ни сохранение."""
    try:
        return lesson_by_id(lesson_id)
    except ScheduleError:
        logger.exception("Расписание не читается, работаю с заметкой без данных пары")
        return None


# --- Список /notes -------------------------------------------------------------------


async def _load_notes(
    db: aiosqlite.Connection, telegram_id: int, period: str, today: date
) -> list[db_module.Note]:
    """Заметки нужного периода. «Незакрытые» — главный сценарий и значение по умолчанию."""
    if period == keyboards.PERIOD_WEEK:
        monday, sunday = notes.week_bounds(today)
        return await db_module.list_notes(db, telegram_id, since=monday, until=sunday)
    if period == keyboards.PERIOD_ALL:
        return await db_module.list_notes(db, telegram_id)
    return await db_module.list_open_notes(db, telegram_id)


async def send_notes_list(
    message: Message,
    db: aiosqlite.Connection,
    telegram_id: int,
    period: str,
    today: date,
) -> None:
    """Показывает список заметок за период. Каждая незакрытая — с кнопкой «Сделал»."""
    found = await _load_notes(db, telegram_id, period, today)

    await message.answer(views.notes_title(period))

    if not found:
        await message.answer(
            views.notes_empty(period), reply_markup=keyboards.notes_periods(period)
        )
        return

    shown = found[-MAX_NOTES_SHOWN:]
    if len(shown) < len(found):
        await message.answer(
            texts.NOTES_TRUNCATED.format(shown=len(shown), total=len(found))
        )

    for note in shown:
        # Кнопка «Сделал» есть прямо в списке: закрывать заметку только из напоминания
        # было бы неудобно — напоминание легко пролистать.
        markup = keyboards.note_done(note.id) if note.is_open else None
        await message.answer(
            views.format_note_item(note, _lesson(note.lesson_id)), reply_markup=markup
        )

    await message.answer(
        texts.NOTES_PERIOD_HINT, reply_markup=keyboards.notes_periods(period)
    )


@router.message(Command("notes"))
async def handle_notes(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """/notes — сразу показывает незакрытые и даёт переключить период."""
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return
    # Команда прерывает недописанную заметку: продолжать диалог, показав список, странно.
    await state.clear()
    await send_notes_list(message, db, user_id, keyboards.PERIOD_OPEN, clock.today())


@router.callback_query(F.data.startswith(f"{keyboards.CB_NOTES}:{keyboards.NOTE_PERIOD}:"))
async def handle_notes_period(callback: CallbackQuery, db: aiosqlite.Connection) -> None:
    """Кнопка периода под списком."""
    period = keyboards.parse_note_period(callback.data)
    message = common.callback_message(callback)
    if period is None or message is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    await callback.answer()
    await send_notes_list(message, db, callback.from_user.id, period, clock.today())


# --- Вопрос после пары: «Есть / Нет» -------------------------------------------------


@router.callback_query(F.data.startswith(f"{keyboards.CB_NOTES}:{keyboards.NOTE_HAS}:"))
async def handle_note_has(callback: CallbackQuery, state: FSMContext) -> None:
    """«Есть» — спрашиваем текст и ждём следующее сообщение."""
    parsed = keyboards.parse_note_lesson(callback.data, keyboards.NOTE_HAS)
    message = common.callback_message(callback)
    if parsed is None or message is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    lesson_id, lesson_date = parsed
    await callback.answer()
    await common.hide_inline_keyboard(callback)

    await state.set_state(NoteEntry.text)
    # Пару запоминаем в состоянии: текст придёт отдельным сообщением, и к тому моменту
    # знать, к какому занятию его привязать, будет неоткуда.
    await state.update_data(
        **{LESSON_ID_KEY: lesson_id, LESSON_DATE_KEY: lesson_date.isoformat()}
    )
    await message.answer(texts.NOTES_ASK_TEXT)


@router.callback_query(F.data.startswith(f"{keyboards.CB_NOTES}:{keyboards.NOTE_NONE}:"))
async def handle_note_none(callback: CallbackQuery) -> None:
    """«Нет» — ничего не создаём, коротко подтверждаем и уходим."""
    message = common.callback_message(callback)
    await callback.answer()
    await common.hide_inline_keyboard(callback)
    if message is not None:
        await message.answer(texts.NOTES_NOTHING_TO_SAVE)


# --- Текст заметки -------------------------------------------------------------------


@router.message(NoteEntry.text, Command("cancel"))
async def handle_note_cancel(message: Message, state: FSMContext) -> None:
    """/cancel во время ввода текста: выходим из ожидания, ничего не сохраняя.

    Общий /cancel живёт в онбординге и фильтра по состоянию не имеет, поэтому роутер
    заметок подключён раньше — иначе отмена ввода заметки досталась бы ему.
    """
    await state.clear()
    await message.answer(texts.NOTES_ENTRY_CANCELLED)


@router.message(NoteEntry.text)
async def handle_note_text(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """Пришёл текст заметки — сохраняем и считаем срок."""
    user_id = message.from_user.id if message.from_user else None
    text = (message.text or "").strip()

    # Команду записывать «домашкой» было бы странно: /today, /notes и /cancel ловят
    # хендлеры выше, а на остальные просто просим прислать текст.
    if not text or text.startswith("/") or user_id is None:
        await message.answer(texts.NOTES_TEXT_RETRY)
        return

    data = await state.get_data()
    lesson_id = data.get(LESSON_ID_KEY)
    raw_date = data.get(LESSON_DATE_KEY)
    try:
        lesson_date = date.fromisoformat(str(raw_date))
    except ValueError:
        # Состояние испорчено (например, бот обновился посреди диалога) — не молчим.
        logger.warning("В состоянии заметки нет корректной даты пары: %r", raw_date)
        await state.clear()
        await message.answer(texts.STALE_BUTTON)
        return

    lesson = _lesson(str(lesson_id))
    # Срок — дата следующего такого же занятия. Её может не быть (конец семестра,
    # пара пропала из расписания): тогда заметка живёт без напоминаний.
    due_date = (
        notes.next_lesson_occurrence(lesson, lesson_date) if lesson is not None else None
    )

    note = await db_module.create_note(
        db, user_id, str(lesson_id), lesson_date, text, due_date
    )
    await state.clear()
    logger.info(
        "Заметка %s создана: пользователь=%s пара=%s (%s), срок=%s",
        note.id,
        user_id,
        lesson_id,
        lesson_date,
        due_date or "нет",
    )
    await message.answer(views.format_note_saved(due_date))


# --- «Сделал» и «Не сделал» ----------------------------------------------------------


@router.callback_query(F.data.startswith(f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:"))
async def handle_note_done(callback: CallbackQuery, db: aiosqlite.Connection) -> None:
    """«Сделал» — закрывает заметку. Работает и из списка, и из напоминания."""
    note_id = keyboards.parse_note_id(callback.data, keyboards.NOTE_DONE)
    if note_id is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    note = await db_module.get_note(db, note_id)
    # Повторное нажатие (например, и в списке, и в напоминании) не ошибка: сообщаем
    # мягко и ничего не переписываем.
    was_open = note is None or note.is_open

    closed = await db_module.close_note(db, note_id, callback.from_user.id)
    if closed is None:
        await callback.answer(texts.NOTE_NOT_FOUND, show_alert=True)
        return

    await callback.answer(texts.NOTE_DONE_TOAST if was_open else texts.NOTE_ALREADY_DONE)
    await common.hide_inline_keyboard(callback)

    message = common.callback_message(callback)
    if message is not None and was_open:
        logger.info("Заметка %s закрыта пользователем %s", note_id, callback.from_user.id)
        await message.answer(texts.NOTE_DONE_REPLY)


@router.callback_query(
    F.data.startswith(f"{keyboards.CB_NOTES}:{keyboards.NOTE_NOT_DONE}:")
)
async def handle_note_not_done(callback: CallbackQuery) -> None:
    """«Не сделал» — ничего не закрываем и не создаём, просто спокойно принимаем ответ."""
    message = common.callback_message(callback)
    await callback.answer()
    await common.hide_inline_keyboard(callback)
    if message is not None:
        await message.answer(texts.NOTE_NOT_DONE_REPLY)


__all__ = ["MAX_NOTES_SHOWN", "NoteEntry", "router", "send_notes_list"]
