"""Тесты команд /today и /tomorrow.

Настоящий Telegram не поднимается: вместо сообщения — заглушка, которая запоминает
ответы бота. «Сегодняшняя» дата подменяется через `app.clock.today`, поэтому тесты
дают одинаковый результат в любой реальный день.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pytest
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Chat, Message, User

from app import clock, texts
from app.handlers.schedule import handle_today, handle_tomorrow
from app.schedule import ScheduleError, lessons_on, load_lessons

FAKE_TOKEN = "123456789:AAHfake-token-for-tests-only-000000000"

MON_ODD = date(2026, 8, 31)
TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
THU_ODD = date(2026, 9, 3)
SAT_ODD = date(2026, 9, 5)

WED_EVEN = date(2026, 9, 9)
THU_EVEN = date(2026, 9, 10)
FRI_EVEN = date(2026, 9, 11)


@dataclass
class FakeMessage:
    """Минимальная замена aiogram.types.Message: складывает ответы бота в список."""

    text: str = "/today"
    answers: list[str] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: object) -> None:
        self.answers.append(text)


@pytest.fixture()
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> Callable[[date], None]:
    """Подменяет «сегодня» по Москве, не трогая системные часы."""

    def _freeze(day: date) -> None:
        monkeypatch.setattr(clock, "today", lambda *args, **kwargs: day)

    return _freeze


# --------------------------------------------------------------------------------------
# /today
# --------------------------------------------------------------------------------------


async def test_today_reports_empty_thursday_on_odd_week(
    freeze_today: Callable[[date], None],
) -> None:
    """Критерий приёмки этапа: четверг по числителю — «сегодня пар нет»."""
    freeze_today(THU_ODD)
    message = FakeMessage(text="/today")

    await handle_today(message)  # type: ignore[arg-type]

    assert len(message.answers) == 1
    answer = message.answers[0]
    assert "Сегодня пар нет" in answer
    assert "четверг" in answer
    assert "числитель" in answer


async def test_today_shows_four_lessons_on_even_wednesday(
    freeze_today: Callable[[date], None],
) -> None:
    """Критерий приёмки этапа: в среду по знаменателю — 4 пары."""
    freeze_today(WED_EVEN)
    message = FakeMessage(text="/today")

    await handle_today(message)  # type: ignore[arg-type]

    answer = message.answers[0]
    day = lessons_on(WED_EVEN, load_lessons())
    assert len(day) == 4
    for lesson in day:
        assert lesson.time_range in answer
        assert lesson.room in answer
    assert "знаменатель" in answer


async def test_today_shows_three_lessons_on_odd_wednesday(
    freeze_today: Callable[[date], None],
) -> None:
    """По числителю в ту же среду пар три — чётность действительно учитывается."""
    freeze_today(WED_ODD)
    message = FakeMessage(text="/today")

    await handle_today(message)  # type: ignore[arg-type]

    answer = message.answers[0]
    day = lessons_on(WED_ODD, load_lessons())
    assert len(day) == 3
    # Заголовок и по одному жирному времени на пару.
    assert answer.count("<b>") == len(day) + 1
    assert "числитель" in answer


async def test_today_on_monday_lists_remote_lessons(
    freeze_today: Callable[[date], None],
) -> None:
    """Понедельник полностью дистанционный: пары показываем, но с пометкой «дистанционно»."""
    freeze_today(MON_ODD)
    message = FakeMessage(text="/today")

    await handle_today(message)  # type: ignore[arg-type]

    answer = message.answers[0]
    day = lessons_on(MON_ODD, load_lessons())
    assert "пар нет" not in answer
    assert answer.count("дистанционно") == len(day)


async def test_today_names_the_building_for_offline_lesson(
    freeze_today: Callable[[date], None],
) -> None:
    """Во вторник по числителю в ответе виден корпус на Вознесенском."""
    freeze_today(TUE_ODD)
    message = FakeMessage(text="/today")

    await handle_today(message)  # type: ignore[arg-type]

    assert "пр. Вознесенский, д. 46" in message.answers[0]


async def test_today_header_mentions_today_and_date(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(THU_EVEN)
    message = FakeMessage(text="/today")

    await handle_today(message)  # type: ignore[arg-type]

    assert "Сегодня, четверг" in message.answers[0]
    assert "10 сентября" in message.answers[0]


# --------------------------------------------------------------------------------------
# /tomorrow
# --------------------------------------------------------------------------------------


async def test_tomorrow_shows_next_day(freeze_today: Callable[[date], None]) -> None:
    """Стоим в среду по знаменателю — /tomorrow показывает четверг того же знаменателя."""
    freeze_today(WED_EVEN)
    message = FakeMessage(text="/tomorrow")

    await handle_tomorrow(message)  # type: ignore[arg-type]

    answer = message.answers[0]
    assert "Завтра, четверг" in answer
    assert "10 сентября" in answer
    for lesson in lessons_on(THU_EVEN, load_lessons()):
        assert lesson.time_range in answer


async def test_tomorrow_reports_empty_day(freeze_today: Callable[[date], None]) -> None:
    """Четверг по знаменателю → завтра пятница, а она по знаменателю пустая."""
    freeze_today(THU_EVEN)
    message = FakeMessage(text="/tomorrow")

    await handle_tomorrow(message)  # type: ignore[arg-type]

    answer = message.answers[0]
    assert "Завтра пар нет" in answer
    assert "пятница" in answer


async def test_tomorrow_crosses_week_boundary(freeze_today: Callable[[date], None]) -> None:
    """Суббота числителя → «завтра» это воскресенье, чётность недели ещё не меняется."""
    freeze_today(SAT_ODD)
    message = FakeMessage(text="/tomorrow")

    await handle_tomorrow(message)  # type: ignore[arg-type]

    answer = message.answers[0]
    assert "Завтра, воскресенье" in answer
    assert "числитель" in answer
    assert "пар нет" in answer


async def test_tomorrow_crosses_month_boundary(freeze_today: Callable[[date], None]) -> None:
    """30 сентября → 1 октября: месяц в заголовке должен смениться."""
    freeze_today(date(2026, 9, 30))
    message = FakeMessage(text="/tomorrow")

    await handle_tomorrow(message)  # type: ignore[arg-type]

    assert "1 октября" in message.answers[0]


# --------------------------------------------------------------------------------------
# Сломанные данные и маршрутизация команд
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("handler", [handle_today, handle_tomorrow])
async def test_broken_schedule_answers_politely(
    monkeypatch: pytest.MonkeyPatch,
    freeze_today: Callable[[date], None],
    handler: Callable[..., object],
) -> None:
    """Если файл расписания испорчен, бот отвечает человеческим текстом, а не падает."""
    freeze_today(WED_EVEN)

    def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr("app.handlers.schedule.lessons_on", boom)
    message = FakeMessage(text="/today")

    await handler(message)  # type: ignore[arg-type]

    assert message.answers == [texts.SCHEDULE_ERROR]


def make_message(text: str) -> Message:
    """Настоящий объект aiogram Message — нужен фильтру Command."""
    return Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="Тестер"),
        text=text,
    )


@pytest.fixture()
async def offline_bot() -> AsyncIterator[Bot]:
    """Bot нужен фильтрам как аргумент; в сеть он в этих тестах не ходит."""
    bot = Bot(token=FAKE_TOKEN)
    try:
        yield bot
    finally:
        await bot.session.close()


@pytest.mark.parametrize("command", ["today", "tomorrow"])
async def test_command_filter_matches(offline_bot: Bot, command: str) -> None:
    assert await Command(command)(make_message(f"/{command}"), bot=offline_bot)


@pytest.mark.parametrize("text", ["/todayy", "today", "/tomorrows"])
async def test_command_filter_ignores_lookalikes(offline_bot: Bot, text: str) -> None:
    assert not await Command("today")(make_message(text), bot=offline_bot)


def test_schedule_router_is_registered_before_fallback(root_router: Router) -> None:
    """Иначе /today поймает fallback и ответит «не знаю такой команды»."""
    names = [child.name for child in root_router.sub_routers]

    assert "schedule" in names
    assert names.index("schedule") < names.index("fallback")
