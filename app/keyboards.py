"""Клавиатуры бота. Только сборка разметки — ни базы, ни сети.

Формат callback_data: «префикс:шаг:значение», например «onb:prep:20» или «set:edit:location».
У заметок значение бывает составным: «note:has:L1216:2026-09-01» — id пары и дата пары,
после которой задали вопрос. Разбирают всё это хендлеры онбординга, настроек и заметок.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app import texts, users

CB_ONBOARDING = "onb"
CB_SETTINGS = "set"
CB_SETTINGS_CANCEL = f"{CB_SETTINGS}:cancel"
CB_NOTES = "note"

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


__all__ = [
    "CB_NOTES",
    "CB_ONBOARDING",
    "CB_SETTINGS",
    "CB_SETTINGS_CANCEL",
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
    "buffer_choice",
    "minutes_choice",
    "note_done",
    "note_prompt",
    "notes_periods",
    "parse_callback_value",
    "parse_note_id",
    "parse_note_lesson",
    "parse_note_period",
    "prep_choice",
    "remove_keyboard",
    "request_location",
    "settings_menu",
    "transport_choice",
]
