"""Тесты хендлеров /start, /help и неизвестных команд.

Настоящий Telegram не поднимается: вместо объекта сообщения подставляется заглушка,
которая просто запоминает, что бот попытался ответить. База — временная, в памяти
(фикстура `db`), состояние диалога — MemoryStorage (фикстура `state`).

С этапа 3 `/start` — это первый шаг знакомства: новому человеку бот здоровается и сразу
просит геопозицию, а тому, кто уже всё настроил, отвечает коротким приветствием и заново
диалог не запускает.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import aiosqlite
import pytest
from aiogram import Bot, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Chat, Message, User

from app import texts, users
from app.handlers.fallback import handle_unknown_command
from app.handlers.onboarding import Onboarding
from app.handlers.start import handle_help, handle_start
from fakes import USER_ID, FakeMessage, make_onboarded_user

FAKE_TOKEN = "123456789:AAHfake-token-for-tests-only-000000000"


# --------------------------------------------------------------------------------------
# Тексты ответов
# --------------------------------------------------------------------------------------


async def test_start_greets_new_user_and_asks_for_location(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Новому человеку: сначала приветствие, сразу за ним — первый вопрос про геопозицию."""
    message = FakeMessage(text="/start")

    await handle_start(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.START_GREETING, texts.ONBOARDING_INTRO]
    assert texts.BTN_SEND_LOCATION in message.last_answer
    # Кнопка «Отправить геопозицию» действительно приложена к вопросу.
    assert message.last_markup is not None
    assert await state.get_state() == Onboarding.location.state


async def test_start_registers_new_user_in_database(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Критерий приёмки этапа: после /start запись о человеке лежит в базе."""
    message = FakeMessage(text="/start")

    await handle_start(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None
    assert saved.username == "tester"
    assert saved.created_at
    # Настройки ещё не заданы: знакомство только началось.
    assert users.is_onboarded(saved) is False


async def test_start_works_without_from_user(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Сообщение из канала приходит без from_user — хендлер не должен падать на None.

    Настраивать в этом случае некого, поэтому бот здоровается и на этом останавливается:
    в диалог никого не втягиваем и в базу ничего не пишем.
    """
    message = FakeMessage(text="/start", from_user=None)

    await handle_start(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.START_GREETING]
    assert await state.get_state() is None
    assert await users.list_users(db) == []


async def test_start_for_onboarded_user_does_not_repeat_dialog(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Кто уже настроен — получает короткое приветствие, а не четыре вопроса заново."""
    await make_onboarded_user(db)
    message = FakeMessage(text="/start")

    await handle_start(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.START_RETURNING]
    assert texts.ONBOARDING_INTRO not in message.all_text
    assert await state.get_state() is None


async def test_start_returning_greeting_points_to_settings(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Иначе человек не поймёт, как поменять ответ, если переехал."""
    await make_onboarded_user(db)
    message = FakeMessage(text="/start")

    await handle_start(message, state, db)  # type: ignore[arg-type]

    assert "/settings" in message.last_answer


async def test_start_keeps_saved_answers_of_onboarded_user(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Повторный /start не должен обнулять уже сохранённые настройки."""
    await make_onboarded_user(db, prep_minutes=45, buffer_minutes=15)
    message = FakeMessage(text="/start")

    await handle_start(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None
    assert (saved.prep_minutes, saved.buffer_minutes) == (45, 15)
    assert users.is_onboarded(saved) is True


async def test_start_drops_unfinished_dialog(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """/start посреди зависшего диалога начинает всё заново, а не ждёт старый ответ."""
    await state.set_state(Onboarding.buffer)
    await state.update_data(mode="settings")
    message = FakeMessage(text="/start")

    await handle_start(message, state, db)  # type: ignore[arg-type]

    # Новый пользователь → диалог начался с первого вопроса, старые данные FSM стёрты.
    assert await state.get_state() == Onboarding.location.state
    assert (await state.get_data()).get("mode") == "onboarding"


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


def test_root_router_puts_fallback_last(root_router: Router) -> None:
    """Fallback ловит остаток команд, поэтому он обязан идти после /start и /help.

    Роутер берётся из общей фикстуры: `build_root_router()` можно вызвать за прогон
    только один раз (см. tests/conftest.py).
    """
    names = [child.name for child in root_router.sub_routers]

    assert names[0] == "start"
    assert names[-1] == "fallback"
