"""Тесты хендлеров /start, /help и неизвестных команд.

Настоящий Telegram не поднимается: вместо объекта сообщения подставляется заглушка,
которая просто запоминает, что бот попытался ответить.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from aiogram import Bot, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Chat, Message, User

from app import texts
from app.handlers import build_root_router
from app.handlers.fallback import handle_unknown_command
from app.handlers.start import handle_help, handle_start

FAKE_TOKEN = "123456789:AAHfake-token-for-tests-only-000000000"


@dataclass
class FakeUser:
    id: int = 42
    username: str | None = "tester"


@dataclass
class FakeMessage:
    """Минимальная замена aiogram.types.Message: хранит ответы бота в списке."""

    text: str = "/start"
    from_user: FakeUser | None = field(default_factory=FakeUser)
    answers: list[str] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: object) -> None:
        self.answers.append(text)


# --------------------------------------------------------------------------------------
# Тексты ответов
# --------------------------------------------------------------------------------------


async def test_start_answers_with_greeting() -> None:
    message = FakeMessage(text="/start")

    await handle_start(message)  # type: ignore[arg-type]

    assert message.answers == [texts.START_GREETING]


async def test_start_works_without_from_user() -> None:
    """Сообщение из канала приходит без from_user — хендлер не должен падать на None."""
    message = FakeMessage(text="/start", from_user=None)

    await handle_start(message)  # type: ignore[arg-type]

    assert message.answers == [texts.START_GREETING]


async def test_help_answers_with_help_text() -> None:
    message = FakeMessage(text="/help")

    await handle_help(message)  # type: ignore[arg-type]

    assert message.answers == [texts.HELP_TEXT]


async def test_unknown_command_answers_with_hint() -> None:
    message = FakeMessage(text="/погода")

    await handle_unknown_command(message)  # type: ignore[arg-type]

    assert message.answers == [texts.UNKNOWN_COMMAND]


# --------------------------------------------------------------------------------------
# Маршрутизация: что команда вообще доедет до нужного хендлера
# --------------------------------------------------------------------------------------


def make_message(text: str) -> Message:
    """Настоящий объект aiogram Message — нужен фильтрам Command/CommandStart."""
    return Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Тестер"),
        text=text,
    )


@pytest.fixture()
async def offline_bot() -> AsyncIterator[Bot]:
    """Bot нужен фильтрам как аргумент; в сеть он в этих тестах не ходит.

    Команды с упоминанием («/start@имя_бота») здесь не проверяются: чтобы их разобрать,
    aiogram спрашивает у Telegram имя бота, а сети в тестах нет.
    """
    bot = Bot(token=FAKE_TOKEN)
    try:
        yield bot
    finally:
        await bot.session.close()


@pytest.mark.parametrize("text", ["/start", "/start payload"])
async def test_command_start_filter_matches(offline_bot: Bot, text: str) -> None:
    assert await CommandStart()(make_message(text), bot=offline_bot)


async def test_command_help_filter_matches(offline_bot: Bot) -> None:
    assert await Command("help")(make_message("/help"), bot=offline_bot)


@pytest.mark.parametrize("text", ["/help", "просто текст", "/startup"])
async def test_command_start_filter_ignores_other_texts(offline_bot: Bot, text: str) -> None:
    assert not await CommandStart()(make_message(text), bot=offline_bot)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("/погода", True, id="неизвестная команда ловится"),
        pytest.param("привет", False, id="обычный текст не трогаем"),
    ],
)
def test_fallback_filter_reacts_only_to_commands(text: str, expected: bool) -> None:
    """Fallback отвечает только на сообщения, начинающиеся со слэша."""
    assert bool(F.text.startswith("/").resolve(make_message(text))) is expected


def test_root_router_puts_fallback_last() -> None:
    """Fallback ловит остаток команд, поэтому он обязан идти после /start и /help."""
    root = build_root_router()
    names = [child.name for child in root.sub_routers]

    assert names == ["start", "fallback"]
