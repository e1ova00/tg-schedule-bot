"""Команды знакомства: /start и /help."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app import texts

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Приветствие. Онбординг с геопозицией появится на этапе 3."""
    user = message.from_user
    logger.info(
        "Команда /start от пользователя id=%s username=%s",
        user.id if user else "?",
        user.username if user else "?",
    )
    await message.answer(texts.START_GREETING)


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Короткий список доступных команд."""
    await message.answer(texts.HELP_TEXT)
