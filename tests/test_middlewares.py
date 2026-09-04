"""Тесты middleware: кого пускать к боту и как хендлеры получают базу.

Сети нет: апдейт собирается через `Update.model_construct`, а внутрь кладутся заглушки
сообщения и нажатия из `tests/fakes.py` — они только запоминают, что бот хотел ответить.
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite
import pytest
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Update, User

from app import texts
from app.middlewares import AccessMiddleware, DatabaseMiddleware
from fakes import FakeCallback, FakeMessage

OWNER_ID = 42
STRANGER_ID = 999


class SpyHandler:
    """Хендлер-шпион: считает вызовы и запоминает, что ему передали."""

    def __init__(self, result: object = "готово") -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.result = result

    async def __call__(self, event: Any, data: dict[str, Any]) -> object:
        self.calls.append((event, data))
        return self.result

    @property
    def called(self) -> bool:
        return bool(self.calls)


def message_update(message: FakeMessage | None = None) -> Update:
    """Апдейт с сообщением. Валидацию pydantic обходим: внутри заглушка, а не Message."""
    return Update.model_construct(update_id=1, message=message or FakeMessage(text="/start"))


def callback_update(callback: FakeCallback | None = None) -> Update:
    return Update.model_construct(update_id=2, callback_query=callback or FakeCallback())


def event_data(user_id: int | None, username: str | None = "tester") -> dict[str, Any]:
    """`data` от aiogram: автора апдейта диспетчер кладёт под ключ event_from_user."""
    if user_id is None:
        return {}
    return {
        "event_from_user": User(
            id=user_id, is_bot=False, first_name="Тестер", username=username
        )
    }


# ======================================================================================
# 1. Пустой список — бот открыт для всех
# ======================================================================================


def test_empty_list_means_open_bot() -> None:
    middleware = AccessMiddleware([])

    assert middleware.is_open is True
    assert middleware.is_allowed(STRANGER_ID) is True
    assert middleware.is_allowed(None) is True


async def test_open_bot_passes_everyone_through() -> None:
    middleware = AccessMiddleware([])
    handler = SpyHandler()
    message = FakeMessage(text="/start")

    result = await middleware(handler, message_update(message), event_data(STRANGER_ID))

    assert handler.called is True
    assert result == "готово"
    assert message.answers == []


async def test_open_bot_passes_update_without_author() -> None:
    """Апдейты без from_user (например, из канала) открытый бот тоже пропускает."""
    middleware = AccessMiddleware([])
    handler = SpyHandler()

    await middleware(handler, message_update(), event_data(None))

    assert handler.called is True


# ======================================================================================
# 2. Непустой список — пускаем только своих
# ======================================================================================


def test_non_empty_list_closes_the_bot() -> None:
    middleware = AccessMiddleware([OWNER_ID, 100])

    assert middleware.is_open is False
    assert middleware.is_allowed(OWNER_ID) is True
    assert middleware.is_allowed(100) is True
    assert middleware.is_allowed(STRANGER_ID) is False
    assert middleware.is_allowed(None) is False


async def test_allowed_user_reaches_handler() -> None:
    middleware = AccessMiddleware([OWNER_ID])
    handler = SpyHandler()
    message = FakeMessage(text="/start")
    data = event_data(OWNER_ID)

    result = await middleware(handler, message_update(message), data)

    assert handler.called is True
    assert result == "готово"
    assert message.answers == []


async def test_stranger_is_stopped_before_handler() -> None:
    """Главное: чужой хендлер не увидит, а в ответ получит вежливый отказ."""
    middleware = AccessMiddleware([OWNER_ID])
    handler = SpyHandler()
    message = FakeMessage(text="/start")

    result = await middleware(handler, message_update(message), event_data(STRANGER_ID))

    assert handler.called is False
    assert result is None
    assert message.answers == [texts.ACCESS_DENIED]


async def test_update_without_author_is_stopped_when_list_is_set() -> None:
    middleware = AccessMiddleware([OWNER_ID])
    handler = SpyHandler()
    message = FakeMessage(text="/start", from_user=None)

    await middleware(handler, message_update(message), event_data(None))

    assert handler.called is False
    assert message.answers == [texts.ACCESS_DENIED]


async def test_stranger_pressing_button_gets_alert() -> None:
    """Нажатие на инлайн-кнопку отвечается всплывающим окном, а не письмом в чат."""
    middleware = AccessMiddleware([OWNER_ID])
    handler = SpyHandler()
    callback = FakeCallback(data="onb:transport:car")

    await middleware(handler, callback_update(callback), event_data(STRANGER_ID))

    assert handler.called is False
    assert callback.answered == [(texts.ACCESS_DENIED, True)]


def test_denial_text_is_short_and_without_internals() -> None:
    """Отказ — одна вежливая фраза. Ни настроек, ни чужих id постороннему не показываем."""
    text = texts.ACCESS_DENIED

    assert text.strip()
    assert len(text) < 200
    for secret in ("ALLOWED_USER_IDS", ".env", "telegram_id", "Traceback"):
        assert secret not in text


async def test_denied_access_is_logged_with_user_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """По логу владелец решит, добавлять ли человека в ALLOWED_USER_IDS."""
    middleware = AccessMiddleware([OWNER_ID])

    with caplog.at_level(logging.WARNING, logger="app.middlewares"):
        await middleware(
            SpyHandler(), message_update(), event_data(STRANGER_ID, username="chuzhoy")
        )

    record = caplog.text
    assert str(STRANGER_ID) in record
    assert "chuzhoy" in record


async def test_failure_to_answer_does_not_break_the_bot() -> None:
    """Telegram может не дать ответить (чат заблокирован) — падать из-за этого нельзя."""

    class BrokenMessage(FakeMessage):
        async def answer(self, text: str, **kwargs: Any) -> None:
            raise TelegramAPIError(method=None, message="bot was blocked by the user")  # type: ignore[arg-type]

    middleware = AccessMiddleware([OWNER_ID])
    handler = SpyHandler()

    result = await middleware(
        handler, message_update(BrokenMessage()), event_data(STRANGER_ID)
    )

    assert result is None
    assert handler.called is False


async def test_access_list_accepts_any_sequence() -> None:
    """В конфиге список приходит кортежем — middleware не должна этому удивляться."""
    middleware = AccessMiddleware((OWNER_ID,))

    assert middleware.is_allowed(OWNER_ID) is True
    assert middleware.is_allowed(STRANGER_ID) is False


# ======================================================================================
# 3. DatabaseMiddleware
# ======================================================================================


async def test_database_middleware_gives_connection_to_handler(
    db: aiosqlite.Connection,
) -> None:
    middleware = DatabaseMiddleware(db)
    handler = SpyHandler()

    await middleware(handler, message_update(), {})

    _, data = handler.calls[0]
    assert data["db"] is db


async def test_database_middleware_keeps_other_data(db: aiosqlite.Connection) -> None:
    middleware = DatabaseMiddleware(db)
    handler = SpyHandler()
    data: dict[str, Any] = {"event_from_user": "кто-то"}

    await middleware(handler, message_update(), data)

    assert data["event_from_user"] == "кто-то"
    assert data["db"] is db


async def test_database_middleware_returns_handler_result(
    db: aiosqlite.Connection,
) -> None:
    middleware = DatabaseMiddleware(db)

    result = await middleware(SpyHandler(result=123), message_update(), {})

    assert result == 123


# ======================================================================================
# 4. Подключение middleware к диспетчеру
# ======================================================================================


class FakeConnection:
    """Пустышка вместо соединения: проверяем только, что его донесли до middleware."""


@pytest.fixture(scope="session")
def dispatcher():
    """Диспетчер, собранный ровно один раз за прогон.

    `create_dispatcher` включает в себя общий корневой роутер, а aiogram разрешает
    прикрепить роутер только к одному диспетчеру: второй вызов упал бы с RuntimeError.
    """
    from app.bot import create_dispatcher
    from app.config import build_config

    config = build_config({"BOT_TOKEN": "123:тестовый", "ALLOWED_USER_IDS": "42, 100"})
    return create_dispatcher(config, FakeConnection())  # type: ignore[arg-type]


def test_dispatcher_registers_both_middlewares(dispatcher) -> None:
    """Проверка доступа обязана стоять раньше базы: чужим её открывать незачем."""
    names = [type(item).__name__ for item in dispatcher.update.outer_middleware]

    assert "AccessMiddleware" in names
    assert "DatabaseMiddleware" in names
    assert names.index("AccessMiddleware") < names.index("DatabaseMiddleware")


def test_dispatcher_access_middleware_uses_config_list(dispatcher) -> None:
    access = next(
        item
        for item in dispatcher.update.outer_middleware
        if isinstance(item, AccessMiddleware)
    )

    assert access.is_open is False
    assert access.is_allowed(42) is True
    assert access.is_allowed(100) is True
    assert access.is_allowed(STRANGER_ID) is False


def test_dispatcher_passes_the_same_connection(dispatcher) -> None:
    database = next(
        item
        for item in dispatcher.update.outer_middleware
        if isinstance(item, DatabaseMiddleware)
    )

    assert isinstance(database._connection, FakeConnection)  # noqa: SLF001
