"""Команда /teachernote: справочные заметки про преподавателей.

Это не задачи: у таких заметок нет ни статуса, ни срока, ни напоминаний — они просто
лежат и ждут, когда понадобятся («принимает только на почту», «просит отчёт в pdf»).
Всё, что про сроки и напоминания, живёт в заметках к парам (/notes, /addnote).

Преподаватель выбирается **только кнопкой** из списка `schedule.teachers()`: если
разрешить ввод текстом, «Зверев» и «Зверев В.В.» станут разными людьми, и заметки
разъедутся. В callback_data уходит индекс в этом списке, а не фамилия: кириллица в
64 байта лимита Telegram не помещается.

Порядок хендлеров в файле — порядок их проверки: команды объявлены раньше, чем
«поймать любой текст в состоянии».
"""

from __future__ import annotations

import logging
from html import escape

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import db as db_module, keyboards, texts, views
from app.handlers import common
from app.schedule import ScheduleError, teachers

logger = logging.getLogger(__name__)

router = Router(name="teacher-notes")

# Ключ в данных FSM: про кого пишем заметку.
TEACHER_KEY = "teacher_note_name"

# Сколько заметок показывать в одном сообщении. Их немного, но список растёт всегда
# в одну сторону, а сообщение в Telegram не бесконечное.
MAX_TEACHER_NOTES_SHOWN = 40


class TeacherNoteEntry(StatesGroup):
    """Ожидание текста заметки после выбора преподавателя."""

    text = State()


def _teachers() -> tuple[str, ...]:
    """Список преподавателей. Сломанное расписание не должно ронять команду."""
    try:
        return teachers()
    except ScheduleError:
        logger.exception("Расписание не читается, список преподавателей пуст")
        return ()


async def send_teacher_notes(
    message: Message, db: aiosqlite.Connection, telegram_id: int
) -> None:
    """Показывает все заметки про преподавателей одним сообщением."""
    found = await db_module.list_teacher_notes(db, telegram_id)

    if not found:
        await message.answer(texts.TEACHER_NOTES_EMPTY)
        return

    shown = found[-MAX_TEACHER_NOTES_SHOWN:]
    await message.answer(texts.TEACHER_NOTES_TITLE)
    if len(shown) < len(found):
        await message.answer(
            texts.TEACHER_NOTES_TRUNCATED.format(shown=len(shown), total=len(found))
        )
    await message.answer(views.format_teacher_notes(shown))


async def ask_teacher(message: Message) -> None:
    """Предлагает выбрать преподавателя кнопкой."""
    names = _teachers()
    if not names:
        await message.answer(texts.TEACHER_NOTES_NO_TEACHERS)
        return
    await message.answer(
        texts.TEACHER_NOTES_PICK, reply_markup=keyboards.teacher_choice(names)
    )


@router.message(Command("teachernote"))
async def handle_teacher_notes(message: Message, state: FSMContext) -> None:
    """/teachernote — меню из двух действий: добавить заметку или посмотреть все."""
    if message.from_user is None:
        return
    # Команда прерывает недописанную заметку: продолжать диалог, показав меню, странно.
    await state.clear()
    await message.answer(
        texts.TEACHER_NOTES_MENU, reply_markup=keyboards.teacher_notes_menu()
    )


@router.callback_query(
    F.data == f"{keyboards.CB_TEACHER_NOTES}:{keyboards.TEACHER_ADD}"
)
async def handle_teacher_note_add(callback: CallbackQuery, state: FSMContext) -> None:
    """«Добавить заметку» — показываем список преподавателей."""
    message = common.callback_message(callback)
    if message is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    await callback.answer()
    await state.clear()
    await ask_teacher(message)


@router.callback_query(
    F.data == f"{keyboards.CB_TEACHER_NOTES}:{keyboards.TEACHER_LIST}"
)
async def handle_teacher_note_list(
    callback: CallbackQuery, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """«Посмотреть все» — список сохранённого."""
    message = common.callback_message(callback)
    if message is None:
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    await callback.answer()
    await state.clear()
    await send_teacher_notes(message, db, callback.from_user.id)


@router.callback_query(
    F.data.startswith(f"{keyboards.CB_TEACHER_NOTES}:{keyboards.TEACHER_PICK}:")
)
async def handle_teacher_pick(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбран преподаватель — ждём текст следующим сообщением."""
    index = keyboards.parse_teacher_index(callback.data)
    message = common.callback_message(callback)
    names = _teachers()
    if index is None or message is None or index >= len(names):
        # Список мог измениться после обновления расписания — старая кнопка не должна
        # молча записать заметку не тому человеку.
        await callback.answer(texts.STALE_BUTTON, show_alert=True)
        return

    teacher = names[index]
    await callback.answer()
    await common.hide_inline_keyboard(callback)

    await state.set_state(TeacherNoteEntry.text)
    # Имя запоминаем в состоянии: текст придёт отдельным сообщением, и к тому моменту
    # знать, про кого он, будет неоткуда.
    await state.update_data(**{TEACHER_KEY: teacher})
    # Имя приходит из расписания, но сообщения уходят с parse_mode=HTML — экранируем
    # на общих основаниях, чтобы правка данных не приводила к «бот молчит».
    await message.answer(texts.TEACHER_NOTES_ASK_TEXT.format(teacher=escape(teacher)))


@router.message(TeacherNoteEntry.text, Command("cancel"))
async def handle_teacher_note_cancel(message: Message, state: FSMContext) -> None:
    """/cancel во время ввода: выходим из ожидания, ничего не сохраняя."""
    await state.clear()
    await message.answer(texts.TEACHER_NOTES_CANCELLED)


@router.message(TeacherNoteEntry.text)
async def handle_teacher_note_text(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """Пришёл текст заметки про преподавателя — сохраняем."""
    user_id = message.from_user.id if message.from_user else None
    text = (message.text or "").strip()

    # Команду записывать заметкой было бы странно: свои команды ловят хендлеры выше,
    # а на остальные просто просим прислать текст.
    if not text or text.startswith("/") or user_id is None:
        await message.answer(texts.TEACHER_NOTES_TEXT_RETRY)
        return

    data = await state.get_data()
    teacher = str(data.get(TEACHER_KEY) or "").strip()
    if not teacher:
        # Состояние испорчено (например, бот обновился посреди диалога) — не молчим.
        logger.warning("В состоянии заметки про преподавателя нет имени: %r", data)
        await state.clear()
        await message.answer(texts.STALE_BUTTON)
        return

    note = await db_module.create_teacher_note(db, user_id, teacher, text)
    await state.clear()
    logger.info(
        "Заметка про преподавателя %s создана: пользователь=%s, id=%s",
        teacher,
        user_id,
        note.id,
    )
    await message.answer(texts.TEACHER_NOTE_SAVED.format(teacher=escape(teacher)))


__all__ = [
    "MAX_TEACHER_NOTES_SHOWN",
    "TEACHER_KEY",
    "TeacherNoteEntry",
    "ask_teacher",
    "handle_teacher_note_add",
    "handle_teacher_note_cancel",
    "handle_teacher_note_list",
    "handle_teacher_note_text",
    "handle_teacher_notes",
    "handle_teacher_pick",
    "router",
    "send_teacher_notes",
]
