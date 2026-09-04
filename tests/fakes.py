"""Заглушки объектов aiogram: тесты хендлеров не поднимают Telegram и не ходят в сеть.

Настоящие `Message` и `CallbackQuery` умеют отвечать только через живого бота, поэтому
вместо них подставляются эти простые классы: они запоминают, что бот попытался отправить,
и позволяют потом это проверить.

Здесь же лежит `make_onboarded_user` — заполнить временную базу «уже настроенным»
пользователем нужно сразу нескольким тестовым модулям.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aiosqlite

# Один и тот же «тестовый человек» во всех тестах: id совпадает с ключом FSM из conftest.
USER_ID = 42


@dataclass
class FakeUser:
    """Минимальная замена aiogram.types.User."""

    id: int = USER_ID
    username: str | None = "tester"
    first_name: str = "Тестер"
    is_bot: bool = False


@dataclass
class FakeLocation:
    """Точка, которую Telegram присылает по кнопке «Отправить геопозицию»."""

    latitude: float = 59.9386
    longitude: float = 30.3141


@dataclass
class FakeMessage:
    """Замена aiogram.types.Message: складывает ответы бота и их клавиатуры в списки."""

    text: str | None = None
    from_user: FakeUser | None = field(default_factory=FakeUser)
    location: FakeLocation | None = None
    answers: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)
    edited_markups: int = 0

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))

    async def edit_reply_markup(self, **kwargs: Any) -> None:
        """Снятие кнопок под сообщением — только считаем вызовы."""
        self.edited_markups += 1

    @property
    def last_answer(self) -> str:
        return self.answers[-1]

    @property
    def last_markup(self) -> Any:
        return self.markups[-1]

    @property
    def all_text(self) -> str:
        """Все ответы одной строкой — удобно искать фразу, не считая сообщения."""
        return "\n".join(self.answers)


@dataclass
class FakeCallback:
    """Замена aiogram.types.CallbackQuery: нажатие на инлайн-кнопку."""

    data: str | None = None
    message: FakeMessage | None = field(default_factory=FakeMessage)
    from_user: FakeUser = field(default_factory=FakeUser)
    # Что бот ответил на само нажатие: (текст всплывашки, показывать ли алертом).
    answered: list[tuple[str | None, bool]] = field(default_factory=list)

    async def answer(
        self, text: str | None = None, show_alert: bool = False, **kwargs: Any
    ) -> None:
        self.answered.append((text, show_alert))


async def make_onboarded_user(
    db: aiosqlite.Connection,
    telegram_id: int = USER_ID,
    *,
    latitude: float = 59.9386,
    longitude: float = 30.3141,
    transport_mode: str = "car",
    prep_minutes: int = 30,
    buffer_minutes: int = 10,
):
    """Кладёт в базу пользователя, который прошёл знакомство до конца."""
    from app import users

    await users.save_user_fields(
        db,
        telegram_id,
        latitude=latitude,
        longitude=longitude,
        transport_mode=transport_mode,
        prep_minutes=prep_minutes,
        buffer_minutes=buffer_minutes,
    )
    return await users.mark_onboarded(db, telegram_id)


__all__ = [
    "USER_ID",
    "FakeCallback",
    "FakeLocation",
    "FakeMessage",
    "FakeUser",
    "make_onboarded_user",
]
