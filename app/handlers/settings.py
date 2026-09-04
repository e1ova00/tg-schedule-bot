"""Команда /settings: показать сохранённые ответы и дать поменять любой из них."""

from __future__ import annotations

import logging

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import keyboards, texts, users
from app.handlers import common, onboarding

logger = logging.getLogger(__name__)

router = Router(name="settings")


@router.message(Command("settings"))
async def handle_settings(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    user = await users.get_user(db, user_id)
    if not users.is_onboarded(user):
        # Пока знакомство не закончено, менять нечего: половина ответов пустая.
        await message.answer(texts.SETTINGS_NOT_READY)
        return

    await state.clear()
    await common.send_settings(message, db, user_id)


@router.callback_query(F.data.startswith(f"{keyboards.CB_SETTINGS}:edit:"))
async def handle_edit_request(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «поменять …» — запускает диалог ради одного шага."""
    step = (callback.data or "").split(":")[-1]
    message = common.callback_message(callback)
    if step not in keyboards.EDITABLE_STEPS or message is None:
        await callback.answer()
        return

    await callback.answer()
    await common.hide_inline_keyboard(callback)
    logger.info(
        "Правка настройки «%s» пользователем telegram_id=%s", step, callback.from_user.id
    )
    await onboarding.start_single_step(message, state, step)
