"""Тесты команды `/preview`: во сколько бот разбудит в конкретный день и почему.

Telegram не поднимается: сообщение — `FakeMessage`, аргумент команды — обычный
`CommandObject`, база временная, маршрутизатор подставной. «Сейчас» подменяется
через `app.clock.now`, поэтому результат не зависит от реального дня.

Отдельный блок — приёмка этапа: `/preview` прогоняется по всем дням недели при обеих
чётностях, и для каждого дня проверяется, что ответ осмысленный.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import aiosqlite
import pytest
from aiogram import Router
from aiogram.filters import CommandObject

from app import alarm, clock, texts, users, views
from app.alarm import AlarmPlan
from app.clock import MOSCOW
from app.config import DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES, build_config
from app.handlers.preview import handle_preview, parse_preview_date
from app.routing.base import SOURCE_DGIS, Router as TravelRouter, RouterError, TravelTime
from app.routing.stub import StubRouter
from app.schedule import ScheduleError, clear_cache
from fakes import USER_ID, FakeMessage, make_onboarded_user

MON_ODD = date(2026, 8, 31)
TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
THU_ODD = date(2026, 9, 3)
FRI_ODD = date(2026, 9, 4)
SAT_ODD = date(2026, 9, 5)
SUN_ODD = date(2026, 9, 6)

MON_EVEN = date(2026, 9, 7)
TUE_EVEN = date(2026, 9, 8)
WED_EVEN = date(2026, 9, 9)
THU_EVEN = date(2026, 9, 10)
FRI_EVEN = date(2026, 9, 11)
SAT_EVEN = date(2026, 9, 12)
SUN_EVEN = date(2026, 9, 13)


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> Callable[..., datetime]:
    """Подменяет «сейчас» по Москве, не трогая системные часы."""

    def _freeze(day: date, hour: int = 8, minute: int = 0) -> datetime:
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)
        monkeypatch.setattr(clock, "now", lambda *args, **kwargs: moment)
        return moment

    return _freeze


class FixedRouter(TravelRouter):
    """Маршрутизатор с постоянным ответом: 25 минут с пробками."""

    def __init__(self, minutes: int = 25, error: BaseException | None = None) -> None:
        self.minutes = minutes
        self.error = error
        self.calls = 0

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return TravelTime(minutes=self.minutes, traffic_aware=True, source=SOURCE_DGIS)


def command(args: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command="preview", args=args)


CONFIG = build_config(
    {"BOT_TOKEN": "123456:AAHfake-token", "ALARM_FALLBACK_TRAVEL_MINUTES": "45"}
)


async def ask_preview(
    db: aiosqlite.Connection,
    args: str | None = None,
    travel_router: TravelRouter | None = None,
    config=CONFIG,
) -> FakeMessage:
    message = FakeMessage(text=f"/preview {args or ''}".strip())
    await handle_preview(
        message,  # type: ignore[arg-type]
        command(args),
        db,
        travel_router or FixedRouter(),
        config,
    )
    return message


# ======================================================================================
# 1. Разбор даты
# ======================================================================================


def test_parse_preview_date_without_argument_is_tomorrow() -> None:
    assert parse_preview_date(None, TUE_ODD) == WED_ODD
    assert parse_preview_date("   ", TUE_ODD) == WED_ODD


def test_parse_preview_date_reads_iso_date() -> None:
    assert parse_preview_date("2026-09-15", TUE_ODD) == date(2026, 9, 15)
    assert parse_preview_date("  2026-09-15  ", TUE_ODD) == date(2026, 9, 15)


@pytest.mark.parametrize(
    "raw",
    ["завтра", "15.09.2026", "2026/09/15", "2026-13-01", "2026-02-30", "15-09-2026", "0"],
)
def test_parse_preview_date_rejects_garbage(raw: str) -> None:
    assert parse_preview_date(raw, TUE_ODD) is None


# ======================================================================================
# 2. Основные ответы команды
# ======================================================================================


async def test_preview_without_argument_shows_tomorrow(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Без даты — завтрашнее утро (вторник → среда, пара в 10:05 на Вознесенском)."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db)

    answer = message.last_answer
    assert "Завтра" in answer
    assert "2 сентября" in answer
    assert "Вознесенский, 46" in answer
    assert "10:05" in answer


async def test_preview_with_explicit_date(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, "2026-09-10")

    answer = message.last_answer
    assert "10 сентября" in answer
    assert "знаменатель" in answer
    assert texts.PREVIEW_WAKE.format(wake="09:00") in answer  # 10:05 − 25 − 10 − 30
    assert "Выходить из дома в 09:30" in answer
    assert answer.rstrip().endswith(texts.PREVIEW_HINT)


async def test_preview_shows_wake_time_and_hint(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    await make_onboarded_user(db, prep_minutes=45, buffer_minutes=20)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, "2026-09-01")  # вторник, пара 11:40

    answer = message.last_answer
    # 11:40 − 25 минут дороги − 20 запаса = выход 10:55; минус 45 на сборы = подъём 10:10.
    assert texts.PREVIEW_WAKE.format(wake="10:10") in answer
    assert "Выходить из дома в 10:55" in answer
    assert texts.PREVIEW_HINT in answer


@pytest.mark.parametrize(
    "raw",
    ["завтра", "15.09.2026", "2026-02-30", "2026-13-01", "понедельник"],
)
async def test_preview_with_bad_date_answers_politely(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime], raw: str
) -> None:
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, raw)

    assert message.answers == [texts.PREVIEW_BAD_DATE.format(value=raw)]
    assert "ГГГГ-ММ-ДД" in message.last_answer


async def test_preview_escapes_html_in_a_bad_date(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Ответ уходит с ParseMode.HTML — сырой текст пользователя сломал бы разметку."""
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, "<b>ой</b>")

    assert "&lt;b&gt;" in message.last_answer
    assert "<b>ой" not in message.last_answer


async def test_preview_without_onboarding_sends_to_start(db: aiosqlite.Connection) -> None:
    message = await ask_preview(db, "2026-09-02")

    assert message.answers == [texts.PREVIEW_NOT_READY]
    assert "/start" in message.last_answer


async def test_preview_with_half_finished_onboarding_sends_to_start(
    db: aiosqlite.Connection,
) -> None:
    await users.save_user_fields(db, USER_ID, latitude=59.9386, longitude=30.3141)

    message = await ask_preview(db, "2026-09-02")

    assert message.answers == [texts.PREVIEW_NOT_READY]


async def test_preview_ignores_message_without_author(db: aiosqlite.Connection) -> None:
    message = FakeMessage(text="/preview", from_user=None)

    await handle_preview(message, command(), db, FixedRouter(), CONFIG)  # type: ignore[arg-type]

    assert message.answers == []


# ======================================================================================
# 3. Дни, в которые бот молчит
# ======================================================================================


@pytest.mark.parametrize("day", [MON_ODD, MON_EVEN], ids=["числитель", "знаменатель"])
async def test_preview_explains_silent_monday(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime], day: date
) -> None:
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)
    travel_router = FixedRouter()

    message = await ask_preview(db, day.isoformat(), travel_router)

    assert texts.PREVIEW_SILENT_MONDAY in message.last_answer
    assert "Разбужу" not in message.last_answer
    assert travel_router.calls == 0, "В дистант маршрут спрашивать не у кого"


@pytest.mark.parametrize(
    "day",
    [THU_ODD, FRI_EVEN, SAT_EVEN, SUN_ODD],
    ids=["чт-числитель", "пт-знаменатель", "сб-знаменатель", "воскресенье"],
)
async def test_preview_explains_empty_days(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime], day: date
) -> None:
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, day.isoformat())

    assert texts.PREVIEW_SILENT["no_lessons"] in message.last_answer


def test_silence_reason_covers_every_alarm_reason() -> None:
    """Каждая причина молчания должна получить свой текст, а не пустую строку."""
    for reason in (
        alarm.REASON_NO_LESSONS,
        alarm.REASON_ALL_REMOTE,
        alarm.REASON_NOT_ONBOARDED,
        "какая-то новая причина",
    ):
        text = views.alarm_silence_reason(AlarmPlan(day=WED_ODD, reason=reason))
        assert text.strip()


def test_all_remote_on_a_non_monday_has_its_own_wording() -> None:
    """Дистант в среду — не «понедельник всегда дистанционный», текст должен отличаться."""
    plan = AlarmPlan(day=WED_ODD, reason=alarm.REASON_ALL_REMOTE)

    text = views.alarm_silence_reason(plan)

    assert text == texts.PREVIEW_SILENT["all_remote"]
    assert text != texts.PREVIEW_SILENT_MONDAY


# ======================================================================================
# 4. Приёмка этапа: каждый день недели при обеих чётностях
# ======================================================================================

# Ожидания собраны вручную по data/schedule_4md4.json:
# «wake» — во сколько разбудить (дорога 25 минут, сборы 30, запас 10) и куда ехать.
EXPECTED_PREVIEW: tuple[tuple[date, str, str], ...] = (
    (MON_ODD, "monday", ""),
    (TUE_ODD, "10:35", "Вознесенский, 46"),
    (WED_ODD, "09:00", "Вознесенский, 46"),
    (THU_ODD, "empty", ""),
    (FRI_ODD, "09:00", "Большая Морская, 18"),
    (SAT_ODD, "14:15", "Вознесенский, 46"),
    (SUN_ODD, "empty", ""),
    (MON_EVEN, "monday", ""),
    (TUE_EVEN, "10:35", "Садовая, 54"),
    (WED_EVEN, "09:00", "Вознесенский, 46"),
    (THU_EVEN, "09:00", "Вознесенский, 46"),
    (FRI_EVEN, "empty", ""),
    (SAT_EVEN, "empty", ""),
    (SUN_EVEN, "empty", ""),
)


@pytest.mark.parametrize(
    ("day", "expected", "building"),
    EXPECTED_PREVIEW,
    ids=[f"{day.isoformat()}" for day, _, _ in EXPECTED_PREVIEW],
)
async def test_preview_is_sensible_for_every_day_of_both_weeks(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    day: date,
    expected: str,
    building: str,
) -> None:
    """Критерий приёмки этапа 5: разумный ответ на каждый день при обеих чётностях."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    freeze_now(MON_ODD, 7)

    message = await ask_preview(db, day.isoformat())

    answer = message.last_answer
    assert len(message.answers) == 1
    # Заголовок с датой и чётностью есть всегда.
    assert "Будильник" in answer
    assert ("числитель" in answer) or ("знаменатель" in answer)

    if expected == "monday":
        assert texts.PREVIEW_SILENT_MONDAY in answer
    elif expected == "empty":
        assert texts.PREVIEW_SILENT["no_lessons"] in answer
    else:
        assert texts.PREVIEW_WAKE.format(wake=expected) in answer
        assert building in answer
        assert texts.PREVIEW_HINT in answer


# ======================================================================================
# 5. Сбои: команда отвечает, а не роняет бота
# ======================================================================================


async def test_preview_survives_router_failure(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Сервис маршрутов молчит — предпросмотр всё равно показывает время подъёма."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, "2026-09-10", FixedRouter(error=RouterError("2ГИС молчит")))

    answer = message.last_answer
    assert "45 минут" in answer  # ALARM_FALLBACK_TRAVEL_MINUTES из конфига теста
    assert texts.ALARM_NOTE_FALLBACK in answer
    assert texts.PREVIEW_WAKE.format(wake="08:40") in answer  # 10:05 − 45 − 10 − 30


async def test_preview_without_config_uses_default_fallback(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Конфиг в контекст не попал — берём значение по умолчанию, а не падаем."""
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    message = FakeMessage(text="/preview 2026-09-10")
    await handle_preview(
        message,  # type: ignore[arg-type]
        command("2026-09-10"),
        db,
        FixedRouter(error=RouterError("2ГИС молчит")),
        None,
    )

    assert f"{DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES} минут" in message.last_answer


async def test_preview_reports_broken_schedule(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    async def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr(alarm, "build_alarm_plan", boom)

    message = await ask_preview(db, "2026-09-02")

    assert message.answers == [texts.SCHEDULE_ERROR]


async def test_preview_survives_unexpected_error(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("что-то совсем неожиданное")

    monkeypatch.setattr(alarm, "build_alarm_plan", boom)

    message = await ask_preview(db, "2026-09-02")

    assert message.answers == [texts.SCHEDULE_ERROR]
    assert "Traceback" not in message.last_answer


async def test_preview_works_with_stub_router(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Без ключей сервиса маршрутов команда тоже должна давать осмысленный ответ."""
    await make_onboarded_user(db)
    freeze_now(TUE_ODD, 8)

    message = await ask_preview(db, "2026-09-02", StubRouter())

    assert "Разбужу" in message.last_answer
    assert texts.ROUTE_NOTE_ROUGH in message.last_answer


# ======================================================================================
# 6. Подключение команды
# ======================================================================================


def test_preview_router_is_registered_before_fallback(root_router: Router) -> None:
    """Иначе /preview поймает fallback и ответит «не знаю такой команды»."""
    names = [child.name for child in root_router.sub_routers]

    assert "preview" in names
    assert names.index("preview") < names.index("fallback")


def test_preview_is_listed_in_help() -> None:
    assert "/preview" in texts.HELP_TEXT
    assert "понедельник" in texts.HELP_TEXT.lower(), "Молчание по понедельникам — не баг"
