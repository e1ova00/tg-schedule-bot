"""Хендлеры-заглушки: неизвестная команда и устаревшая inline-кнопка."""

from __future__ import annotations

import pytest

from app import texts
from app.handlers.fallback import handle_stale_callback, handle_unknown_command
from tests.fakes import FakeCallback, FakeMessage


@pytest.mark.asyncio
async def test_unknown_command_answers_with_help_pointer() -> None:
    message = FakeMessage(text="/погода")
    await handle_unknown_command(message)
    assert message.last_answer == texts.UNKNOWN_COMMAND


@pytest.mark.asyncio
async def test_stale_callback_answers_with_alert_instead_of_spinner() -> None:
    """Без ответа на callback_query у пользователя в Telegram висят «часики» — проверяем,
    что кнопка вне актуального состояния FSM всё равно получает всплывающий ответ."""
    callback = FakeCallback(data="onboarding:transport:car")
    await handle_stale_callback(callback)

    assert callback.answered == [(texts.STALE_BUTTON, True)]
