"""Клавиатуры бота. Только сборка разметки — ни базы, ни сети.

Формат callback_data: «префикс:шаг:значение», например «onb:prep:20» или «set:edit:location».
У заметок значение бывает составным: «note:has:L1216:2026-09-01» — id пары и дата пары,
после которой задали вопрос. Разбирают всё это хендлеры онбординга, настроек и заметок.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app import texts, users
from app.schedule import Lesson

CB_ONBOARDING = "onb"
CB_SETTINGS = "set"
CB_SETTINGS_CANCEL = f"{CB_SETTINGS}:cancel"
CB_NOTES = "note"
CB_WEEK = "week"
CB_ADD_NOTE = "anote"
CB_TEACHER_NOTES = "tnote"

STEP_LOCATION = "location"
STEP_TRANSPORT = "transport"
STEP_PREP = "prep"
STEP_BUFFER = "buffer"

EDITABLE_STEPS: tuple[str, ...] = (STEP_LOCATION, STEP_TRANSPORT, STEP_PREP, STEP_BUFFER)

# Шаги заметок в callback_data.
NOTE_HAS = "has"  # «Есть» — дальше бот спросит текст
NOTE_NONE = "no"  # «Нет» — ничего не создаём
NOTE_DONE = "done"  # «Сделал» — закрыть заметку
NOTE_NOT_DONE = "undone"  # «Не сделал» — просто принять ответ
NOTE_PERIOD = "period"  # выбор периода в /notes

# Периоды списка заметок. «Незакрытые» — главный сценарий и значение по умолчанию.
PERIOD_OPEN = "open"
PERIOD_WEEK = "week"
PERIOD_ALL = "all"
NOTE_PERIODS: tuple[str, ...] = (PERIOD_OPEN, PERIOD_WEEK, PERIOD_ALL)

# Шаги в callback_data остальных команд.
WEEK_GO = "go"  # листание недель: «week:go:2026-09-07» (понедельник нужной недели)
ADD_NOTE_DAY = "day"  # быстрый выбор дня в /addnote: «anote:day:2026-09-07»
TEACHER_ADD = "add"  # «Добавить заметку» в /teachernote
TEACHER_LIST = "list"  # «Посмотреть все» в /teachernote
TEACHER_PICK = "pick"  # выбор преподавателя: «tnote:pick:3» — индекс в teachers()

# Сколько символов названия предмета влезает в кнопку выбора пары: Telegram обрезает
# длинные подписи сам и некрасиво, поэтому режем осмысленно и ставим многоточие.
MAX_BUTTON_SUBJECT = 40


def remove_keyboard() -> ReplyKeyboardRemove:
    """Убрать нижнюю клавиатуру, когда она отработала."""
    return ReplyKeyboardRemove()


def request_location(with_cancel: bool = False) -> ReplyKeyboardMarkup:
    """Нижняя клавиатура с кнопкой «отправить геопозицию».

    При правке через /settings добавляем ещё «Оставить как есть» — иначе единственный
    выход из шага — набрать /cancel руками, а этого пользователь может не знать.
    """
    keyboard = [[KeyboardButton(text=texts.BTN_SEND_LOCATION, request_location=True)]]
    if with_cancel:
        keyboard.append([KeyboardButton(text=texts.BTN_CANCEL_EDIT)])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=texts.LOCATION_PLACEHOLDER,
    )


def _with_cancel_row(rows: list[list[InlineKeyboardButton]], with_cancel: bool) -> None:
    if with_cancel:
        rows.append(
            [InlineKeyboardButton(text=texts.BTN_CANCEL_EDIT, callback_data=CB_SETTINGS_CANCEL)]
        )


def transport_choice(with_cancel: bool = False) -> InlineKeyboardMarkup:
    """Машина или общественный транспорт."""
    rows = [
        [
            InlineKeyboardButton(
                text=texts.TRANSPORT_TITLES[users.TRANSPORT_CAR],
                callback_data=f"{CB_ONBOARDING}:{STEP_TRANSPORT}:{users.TRANSPORT_CAR}",
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.TRANSPORT_TITLES[users.TRANSPORT_PUBLIC],
                callback_data=f"{CB_ONBOARDING}:{STEP_TRANSPORT}:{users.TRANSPORT_PUBLIC}",
            )
        ],
    ]
    _with_cancel_row(rows, with_cancel)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def minutes_choice(
    step: str, options: Sequence[int], with_cancel: bool = False
) -> InlineKeyboardMarkup:
    """Ряд кнопок-подсказок с минутами. Своё число всё равно можно прислать текстом."""
    row = [
        InlineKeyboardButton(
            text=str(value), callback_data=f"{CB_ONBOARDING}:{step}:{value}"
        )
        for value in options
    ]
    rows = [row]
    _with_cancel_row(rows, with_cancel)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prep_choice(with_cancel: bool = False) -> InlineKeyboardMarkup:
    return minutes_choice(STEP_PREP, users.PREP_OPTIONS, with_cancel=with_cancel)


def buffer_choice(with_cancel: bool = False) -> InlineKeyboardMarkup:
    return minutes_choice(STEP_BUFFER, users.BUFFER_OPTIONS, with_cancel=with_cancel)


def settings_menu() -> InlineKeyboardMarkup:
    """Кнопки «поменять …» — по одной на каждую настройку."""
    labels = {
        STEP_LOCATION: texts.BTN_EDIT_LOCATION,
        STEP_TRANSPORT: texts.BTN_EDIT_TRANSPORT,
        STEP_PREP: texts.BTN_EDIT_PREP,
        STEP_BUFFER: texts.BTN_EDIT_BUFFER,
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels[step], callback_data=f"{CB_SETTINGS}:edit:{step}"
                )
            ]
            for step in EDITABLE_STEPS
        ]
    )


def parse_callback_value(data: str | None, prefix: str, step: str) -> str | None:
    """Достаёт значение из callback_data вида «префикс:шаг:значение»."""
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != prefix or parts[1] != step:
        return None
    return parts[2]


# --- Заметки ------------------------------------------------------------------------


def note_prompt(lesson_id: str, lesson_date: date) -> InlineKeyboardMarkup:
    """«Есть / Нет» под вопросом про домашку после пары.

    Пара зашита прямо в кнопку: вопрос приходит сам, без диалога, и к моменту ответа
    бот должен понимать, к какому занятию относить заметку, даже после перезапуска.
    """
    day = lesson_date.isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_NOTE_HAS,
                    callback_data=f"{CB_NOTES}:{NOTE_HAS}:{lesson_id}:{day}",
                ),
                InlineKeyboardButton(
                    text=texts.BTN_NOTE_NONE,
                    callback_data=f"{CB_NOTES}:{NOTE_NONE}:{lesson_id}:{day}",
                ),
            ]
        ]
    )


def note_done(note_id: int, with_not_done: bool = False) -> InlineKeyboardMarkup:
    """Кнопка «Сделал» под заметкой.

    `with_not_done=True` — только для напоминания за сутки: там уместно ответить «нет»,
    и бот просто повторит утром. В списке и в утреннем повторе такой кнопки нет — нажимать
    её было бы не на что.
    """
    row = [
        InlineKeyboardButton(
            text=texts.BTN_NOTE_DONE, callback_data=f"{CB_NOTES}:{NOTE_DONE}:{note_id}"
        )
    ]
    if with_not_done:
        row.append(
            InlineKeyboardButton(
                text=texts.BTN_NOTE_NOT_DONE,
                callback_data=f"{CB_NOTES}:{NOTE_NOT_DONE}:{note_id}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[row])


def notes_periods(current: str | None = None) -> InlineKeyboardMarkup:
    """Три кнопки периода для /notes. Текущий период не показываем — он уже на экране."""
    labels = {
        PERIOD_OPEN: texts.BTN_NOTES_OPEN,
        PERIOD_WEEK: texts.BTN_NOTES_WEEK,
        PERIOD_ALL: texts.BTN_NOTES_ALL,
    }
    row = [
        InlineKeyboardButton(
            text=labels[period], callback_data=f"{CB_NOTES}:{NOTE_PERIOD}:{period}"
        )
        for period in NOTE_PERIODS
        if period != current
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def parse_note_lesson(data: str | None, step: str) -> tuple[str, date] | None:
    """Разбирает «note:has:L1216:2026-09-01» в (lesson_id, дата пары).

    None — значит кнопка не наша или испорчена: хендлер на такое просто вежливо
    промолчит, а не упадёт.
    """
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != CB_NOTES or parts[1] != step:
        return None
    try:
        return parts[2], date.fromisoformat(parts[3])
    except ValueError:
        return None


def parse_note_id(data: str | None, step: str) -> int | None:
    """Достаёт id заметки из «note:done:17»."""
    raw = parse_callback_value(data, CB_NOTES, step)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_note_period(data: str | None) -> str | None:
    """Период списка из «note:period:week». Неизвестное значение — None."""
    value = parse_callback_value(data, CB_NOTES, NOTE_PERIOD)
    return value if value in NOTE_PERIODS else None


def _parse_date_value(data: str | None, prefix: str, step: str) -> date | None:
    """Дата из callback_data вида «префикс:шаг:2026-09-07». Мусор — None."""
    raw = parse_callback_value(data, prefix, step)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


# --- Неделя (/week) -----------------------------------------------------------------


def week_nav(
    monday: date, *, with_prev: bool = True, with_next: bool = True
) -> InlineKeyboardMarkup:
    """Кнопки листания недель. В callback — понедельник соседней недели.

    Дата в кнопке, а не «сдвиг на единицу»: сообщение живёт долго, и через неделю
    относительный сдвиг привёл бы совсем не туда, куда человек ожидает.
    """
    row: list[InlineKeyboardButton] = []
    if with_prev:
        row.append(
            InlineKeyboardButton(
                text=texts.BTN_WEEK_PREV,
                callback_data=f"{CB_WEEK}:{WEEK_GO}:{(monday - timedelta(days=7)).isoformat()}",
            )
        )
    if with_next:
        row.append(
            InlineKeyboardButton(
                text=texts.BTN_WEEK_NEXT,
                callback_data=f"{CB_WEEK}:{WEEK_GO}:{(monday + timedelta(days=7)).isoformat()}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[row] if row else [])


def parse_week_monday(data: str | None) -> date | None:
    """Понедельник нужной недели из «week:go:2026-09-07»."""
    return _parse_date_value(data, CB_WEEK, WEEK_GO)


# --- Заметка к любой паре (/addnote) -------------------------------------------------


def add_note_days(today: date) -> InlineKeyboardMarkup:
    """«Сегодня» и «Завтра» — самые частые ответы на вопрос про дату."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_DAY_TODAY,
                    callback_data=f"{CB_ADD_NOTE}:{ADD_NOTE_DAY}:{today.isoformat()}",
                ),
                InlineKeyboardButton(
                    text=texts.BTN_DAY_TOMORROW,
                    callback_data=(
                        f"{CB_ADD_NOTE}:{ADD_NOTE_DAY}:"
                        f"{(today + timedelta(days=1)).isoformat()}"
                    ),
                ),
            ]
        ]
    )


def parse_add_note_day(data: str | None) -> date | None:
    """Дата из «anote:day:2026-09-07»."""
    return _parse_date_value(data, CB_ADD_NOTE, ADD_NOTE_DAY)


def _button_subject(subject: str) -> str:
    """Название предмета для подписи кнопки — коротко, но узнаваемо."""
    if len(subject) <= MAX_BUTTON_SUBJECT:
        return subject
    return subject[: MAX_BUTTON_SUBJECT - 1].rstrip() + "…"


def lesson_choice(day: date, lessons: Sequence[Lesson]) -> InlineKeyboardMarkup:
    """Кнопки «выбрать пару» для /addnote.

    callback_data намеренно совпадает с кнопкой «Есть» из вопроса после пары
    (`note:has:L1216:2026-09-01`): дальше работает уже существующий хендлер, который
    спросит текст и сохранит заметку. Второго пути сохранения быть не должно.
    """
    day_iso = day.isoformat()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{lesson.start_text} · {_button_subject(lesson.subject)}",
                    callback_data=f"{CB_NOTES}:{NOTE_HAS}:{lesson.id}:{day_iso}",
                )
            ]
            for lesson in lessons
        ]
    )


# --- Заметки про преподавателей (/teachernote) ---------------------------------------


def teacher_notes_menu() -> InlineKeyboardMarkup:
    """Два действия команды: добавить заметку или посмотреть все."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_TEACHER_NOTE_ADD,
                    callback_data=f"{CB_TEACHER_NOTES}:{TEACHER_ADD}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.BTN_TEACHER_NOTE_LIST,
                    callback_data=f"{CB_TEACHER_NOTES}:{TEACHER_LIST}",
                )
            ],
        ]
    )


def teacher_choice(names: Sequence[str]) -> InlineKeyboardMarkup:
    """Список преподавателей кнопками, по двое в ряд.

    В callback_data уходит индекс в переданном списке, а не фамилия: кириллица в UTF-8
    занимает по два байта на букву, а лимит Telegram — 64 байта на всю строку.
    Индекс же гарантирует, что «Зверев В.В.» из разных мест — это один и тот же человек.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for index, name in enumerate(names):
        button = InlineKeyboardButton(
            text=name, callback_data=f"{CB_TEACHER_NOTES}:{TEACHER_PICK}:{index}"
        )
        if index % 2 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_teacher_index(data: str | None) -> int | None:
    """Индекс преподавателя из «tnote:pick:3». Не число или мусор — None."""
    raw = parse_callback_value(data, CB_TEACHER_NOTES, TEACHER_PICK)
    if raw is None:
        return None
    try:
        index = int(raw)
    except ValueError:
        return None
    return index if index >= 0 else None


__all__ = [
    "CB_ADD_NOTE",
    "CB_NOTES",
    "CB_ONBOARDING",
    "CB_SETTINGS",
    "CB_SETTINGS_CANCEL",
    "CB_TEACHER_NOTES",
    "CB_WEEK",
    "EDITABLE_STEPS",
    "NOTE_DONE",
    "NOTE_HAS",
    "NOTE_NONE",
    "NOTE_NOT_DONE",
    "NOTE_PERIOD",
    "NOTE_PERIODS",
    "PERIOD_ALL",
    "PERIOD_OPEN",
    "PERIOD_WEEK",
    "STEP_BUFFER",
    "STEP_LOCATION",
    "STEP_PREP",
    "STEP_TRANSPORT",
    "TEACHER_ADD",
    "TEACHER_LIST",
    "TEACHER_PICK",
    "WEEK_GO",
    "add_note_days",
    "buffer_choice",
    "lesson_choice",
    "minutes_choice",
    "note_done",
    "note_prompt",
    "notes_periods",
    "parse_add_note_day",
    "parse_callback_value",
    "parse_note_id",
    "parse_note_lesson",
    "parse_note_period",
    "parse_teacher_index",
    "parse_week_monday",
    "prep_choice",
    "remove_keyboard",
    "request_location",
    "settings_menu",
    "teacher_choice",
    "teacher_notes_menu",
    "transport_choice",
    "week_nav",
]
