"""Мелочи, нужные сразу нескольким хендлерам."""

from __future__ import annotations

from html import escape

import aiosqlite
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from app import keyboards, users, views

# Сколько символов непонятного аргумента показать в ответе об ошибке формата.
MAX_SHOWN_ARG = 50


def shown_arg(raw: str | None) -> str:
    """Аргумент команды в том виде, в котором его безопасно вернуть пользователю.

    Ответы уходят с parse_mode=HTML, поэтому «/day <b>» без экранирования сломал бы
    разметку всего сообщения. Длину режем: цитировать простыню незачем.
    """
    return escape((raw or "").strip()[:MAX_SHOWN_ARG])


async def send_settings(
    message: Message, db: aiosqlite.Connection, telegram_id: int
) -> None:
    """Показывает карточку настроек с кнопками «поменять …»."""
    user = await users.get_user(db, telegram_id)
    await message.answer(
        views.format_settings(user), reply_markup=keyboards.settings_menu()
    )


def callback_message(callback: CallbackQuery) -> Message | None:
    """Сообщение с кнопкой, если с ним ещё можно работать.

    Telegram отдаёт «недоступное» сообщение (InaccessibleMessage), если кнопку нажали
    под очень старым постом. Отвечать в него нельзя, поэтому такой случай отсекаем сразу.
    """
    message = callback.message
    return None if isinstance(message, InaccessibleMessage) else message


async def hide_inline_keyboard(callback: CallbackQuery) -> None:
    """Убирает кнопки под сообщением, на которое уже ответили.

    Не критично: если Telegram откажет (сообщение слишком старое, кнопок уже нет),
    диалог должен продолжаться как ни в чём не бывало.
    """
    message = callback_message(callback)
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — косметика, ради неё падать нельзя
        pass


__all__ = [
    "MAX_SHOWN_ARG",
    "callback_message",
    "hide_inline_keyboard",
    "send_settings",
    "shown_arg",
]
