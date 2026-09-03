"""Ответ на неизвестные команды. Обычные сообщения не трогаем — они понадобятся диалогам."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from app import texts

logger = logging.getLogger(__name__)

router = Router(name="fallback")


@router.message(F.text.startswith("/"))
async def handle_unknown_command(message: Message) -> None:
    logger.info("Неизвестная команда: %s", message.text)
    await message.answer(texts.UNKNOWN_COMMAND)
