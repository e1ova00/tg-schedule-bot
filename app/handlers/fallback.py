"""Ответ на неизвестные команды. Обычные сообщения не трогаем — они понадобятся диалогам."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app import texts

logger = logging.getLogger(__name__)

router = Router(name="fallback")


@router.message(F.text.startswith("/"))
async def handle_unknown_command(message: Message) -> None:
    logger.info("Неизвестная команда: %s", message.text)
    await message.answer(texts.UNKNOWN_COMMAND)


@router.callback_query()
async def handle_stale_callback(callback: CallbackQuery) -> None:
    """Кнопка от онбординга/настроек, до которой не дошёл ни один хендлер по состоянию.

    Без ответа на callback_query у пользователя в Telegram бесконечно крутятся «часики» —
    отвечаем всплывающей подсказкой, чтобы было понятно, что кнопка устарела.
    """
    logger.info("Неактуальный колбэк: %s", callback.data)
    await callback.answer(texts.STALE_BUTTON, show_alert=True)
