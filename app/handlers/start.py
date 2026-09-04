"""Команды знакомства: /start и /help."""

from __future__ import annotations

import logging

import aiosqlite
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import texts, users
from app.handlers import onboarding

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """Новому пользователю — диалог настройки, знакомому — просто приветствие."""
    user = message.from_user
    logger.info(
        "Команда /start от пользователя id=%s username=%s",
        user.id if user else "?",
        user.username if user else "?",
    )
    if user is None:
        # Сообщение из канала: настраивать некого, но и молчать невежливо.
        await message.answer(texts.START_GREETING)
        return

    # /start сбрасывает недоделанный диалог: иначе бот остался бы ждать ответ на старый вопрос.
    await state.clear()

    saved = await users.ensure_user(db, user.id, username=user.username)
    if users.is_onboarded(saved):
        await message.answer(texts.START_RETURNING)
        return

    await message.answer(texts.START_GREETING)
    await onboarding.start_onboarding(message, state)


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Короткий список доступных команд."""
    await message.answer(texts.HELP_TEXT)
