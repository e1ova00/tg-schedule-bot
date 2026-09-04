"""Клавиатуры бота. Только сборка разметки — ни базы, ни сети.

Формат callback_data: «префикс:шаг:значение», например «onb:prep:20» или «set:edit:location».
Разбирают его хендлеры онбординга и настроек.
"""

from __future__ import annotations

from collections.abc import Sequence

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

STEP_LOCATION = "location"
STEP_TRANSPORT = "transport"
STEP_PREP = "prep"
STEP_BUFFER = "buffer"

EDITABLE_STEPS: tuple[str, ...] = (STEP_LOCATION, STEP_TRANSPORT, STEP_PREP, STEP_BUFFER)


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


__all__ = [
    "CB_ONBOARDING",
    "CB_SETTINGS",
    "CB_SETTINGS_CANCEL",
    "EDITABLE_STEPS",
    "STEP_BUFFER",
    "STEP_LOCATION",
    "STEP_PREP",
    "STEP_TRANSPORT",
    "buffer_choice",
    "minutes_choice",
    "parse_callback_value",
    "prep_choice",
    "remove_keyboard",
    "request_location",
    "settings_menu",
    "transport_choice",
]
