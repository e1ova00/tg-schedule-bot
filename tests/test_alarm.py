"""Тесты утреннего будильника: app/alarm.py.

Ни Telegram, ни сети, ни планировщика: `compute_alarm` и `decide` — чистые функции,
а `build_alarm_plan` получает подставной маршрутизатор и временную базу в памяти.

Проверяется ровно то, на чём легче всего ошибиться и дороже всего ошибиться:

* кого и когда бот НЕ будит (понедельник, пустые дни, ненастроенный пользователь);
* формула из CLAUDE.md: выход = начало очной пары − дорога − запас, подъём = выход − сборы;
* до какого корпуса ехать (вторник по числителю и по знаменателю — разные адреса);
* что делать с планом «сейчас»: ставить задачу, будить немедленно, молчать;
* что бот всё равно будит, когда сервис маршрутов не ответил.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import aiosqlite
import pytest

from app import alarm, buildings, users
from app.alarm import AlarmPlan, compute_alarm, decide
from app.clock import MOSCOW
from app.routing.base import SOURCE_DGIS, Router as TravelRouter, RouterError, TravelTime
from app.routing.stub import StubRouter
from app.schedule import Lesson, clear_cache, week_parity
from fakes import make_onboarded_user

# --- Контрольные даты. Якорь из CLAUDE.md: понедельник 2026-08-31 — числитель. --------

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

VOZNESENSKY = "пр. Вознесенский, д. 46"
SADOVAYA = "ул. Садовая, д. 54"
BOLSHAYA_MORSKAYA = "ул. Большая Морская, д. 18"


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


def make_user(
    *,
    prep_minutes: int | None = 30,
    buffer_minutes: int | None = 10,
    transport_mode: str | None = "car",
    latitude: float | None = 59.9386,
    longitude: float | None = 30.3141,
    onboarded: bool = True,
) -> users.User:
    """Пользователь как из базы, но без базы: чистым функциям хватает и такого."""
    return users.User(
        telegram_id=42,
        latitude=latitude,
        longitude=longitude,
        transport_mode=transport_mode,
        prep_minutes=prep_minutes,
        buffer_minutes=buffer_minutes,
        onboarded_at="2026-08-30T12:00:00+03:00" if onboarded else None,
    )


def make_lesson(
    lesson_id: str,
    weekday: int,
    start: time,
    *,
    is_remote: bool = False,
    building: str = VOZNESENSKY,
    parity: str = "both",
    subject: str = "Тестовый предмет",
) -> Lesson:
    """Синтетическое занятие для случаев, которых нет в настоящем расписании."""
    return Lesson(
        id=lesson_id,
        weekday=weekday,
        start=start,
        end=time((start.hour + 1) % 24, start.minute),
        parity=parity,
        subject=subject,
        kind="Пр",
        teacher="Иванов И.И.",
        building=buildings.REMOTE_ADDRESS if is_remote else building,
        room="—" if is_remote else "В 484",
        is_remote=is_remote,
    )


def msk(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)


class CountingRouter(TravelRouter):
    """Маршрутизатор-свидетель: считает вызовы и отдаёт заданный ответ или ошибку."""

    def __init__(
        self,
        result: TravelTime | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or TravelTime(minutes=25, traffic_aware=True, source=SOURCE_DGIS)
        self.error = error
        self.calls: list[tuple[tuple[float, float], tuple[float, float], str, datetime]] = []

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        self.calls.append((origin, destination, mode, at))
        if self.error is not None:
            raise self.error
        return self.result


# ======================================================================================
# 0. Контрольные даты из CLAUDE.md — чтобы тесты ниже стояли на верном фундаменте
# ======================================================================================


@pytest.mark.parametrize(
    ("day", "parity"),
    [
        (MON_ODD, "odd"),
        (TUE_ODD, "odd"),
        (SUN_ODD, "odd"),
        (MON_EVEN, "even"),
        (THU_EVEN, "even"),
        (date(2026, 9, 14), "odd"),
        (date(2026, 9, 21), "even"),
        (date(2026, 10, 1), "odd"),
    ],
)
def test_control_dates_match_the_spec(day: date, parity: str) -> None:
    assert week_parity(day) == parity


# ======================================================================================
# 1. Когда будильника нет: причина всегда названа, голого None не бывает
# ======================================================================================


@pytest.mark.parametrize("day", [MON_ODD, MON_EVEN], ids=["числитель", "знаменатель"])
def test_monday_is_always_fully_remote(day: date) -> None:
    """Понедельник у группы весь дистанционный — будильника нет ни при какой чётности."""
    plan = compute_alarm(day, make_user())

    assert plan.reason == alarm.REASON_ALL_REMOTE
    assert plan.should_wake is False
    assert plan.wake_at is None
    assert plan.lesson is None


@pytest.mark.parametrize(
    "day",
    [THU_ODD, FRI_EVEN, SAT_EVEN, SUN_ODD],
    ids=["четверг-числитель", "пятница-знаменатель", "суббота-знаменатель", "воскресенье"],
)
def test_empty_days_are_normal_and_named(day: date) -> None:
    """Пустой день — это норма, а не ошибка: причина NO_LESSONS, будильника нет."""
    plan = compute_alarm(day, make_user())

    assert plan.reason == alarm.REASON_NO_LESSONS
    assert plan.should_wake is False
    assert plan.wake_at is None


def test_not_onboarded_user_gets_its_own_reason() -> None:
    plan = compute_alarm(WED_ODD, make_user(onboarded=False))

    assert plan.reason == alarm.REASON_NOT_ONBOARDED


def test_missing_user_is_not_onboarded() -> None:
    assert compute_alarm(WED_ODD, None).reason == alarm.REASON_NOT_ONBOARDED


@pytest.mark.parametrize(
    "day",
    [MON_ODD, THU_ODD, WED_ODD],
    ids=["дистант", "пустой день", "учебный день"],
)
def test_onboarding_is_checked_before_everything_else(day: date) -> None:
    """Порядок проверок: сначала настройки. Иначе человек увидит «пар нет» вместо /start."""
    plan = compute_alarm(day, make_user(onboarded=False))

    assert plan.reason == alarm.REASON_NOT_ONBOARDED


def test_half_finished_onboarding_counts_as_not_onboarded() -> None:
    """Отметка есть, а минут на сборы нет — считать будильник не от чего."""
    plan = compute_alarm(WED_ODD, make_user(prep_minutes=None))

    assert plan.reason == alarm.REASON_NOT_ONBOARDED


def test_all_remote_synthetic_day_on_a_working_weekday() -> None:
    """Дистант — это не только понедельник: причина зависит от пар, а не от дня недели."""
    lessons = (
        make_lesson("R1", WED_ODD.isoweekday(), time(10, 5), is_remote=True),
        make_lesson("R2", WED_ODD.isoweekday(), time(11, 40), is_remote=True),
    )

    plan = compute_alarm(WED_ODD, make_user(), lessons)

    assert plan.reason == alarm.REASON_ALL_REMOTE


# ======================================================================================
# 2. Расчёт времени: выход = пара − дорога − запас, подъём = выход − сборы
# ======================================================================================


def test_alarm_time_matches_the_formula() -> None:
    """Пример из ТЗ: пара в 10:05, сборы 30, запас 10, дорога 25 → выход 09:30, подъём 09:00.

    Четверг по знаменателю — настоящий день расписания, первая пара в 10:05 на Вознесенском.
    """
    plan = compute_alarm(THU_EVEN, make_user(prep_minutes=30, buffer_minutes=10), None, 25)

    assert plan.reason == alarm.REASON_WAKE
    assert plan.lesson is not None
    assert plan.lesson.start_text == "10:05"
    assert plan.leave_at == msk(THU_EVEN, 9, 30)
    assert plan.wake_at == msk(THU_EVEN, 9, 0)
    assert plan.travel_minutes == 25


def test_alarm_time_is_recomputed_from_its_parts() -> None:
    """Та же формула, но с другими числами — на случай, если совпадение выше случайно."""
    user = make_user(prep_minutes=45, buffer_minutes=20)

    plan = compute_alarm(TUE_ODD, user, None, 37)  # пара в 11:40

    assert plan.leave_at == msk(TUE_ODD, 11, 40) - timedelta(minutes=37 + 20)
    assert plan.wake_at == plan.leave_at - timedelta(minutes=45)
    assert plan.leave_at == msk(TUE_ODD, 10, 43)
    assert plan.wake_at == msk(TUE_ODD, 9, 58)


def test_zero_prep_and_buffer_do_not_break_the_formula() -> None:
    """Человек имеет право ответить «0 минут» — тогда подъём совпадает с выходом."""
    plan = compute_alarm(WED_ODD, make_user(prep_minutes=0, buffer_minutes=0), None, 15)

    assert plan.leave_at == msk(WED_ODD, 9, 50)  # пара в 10:05
    assert plan.wake_at == plan.leave_at


def test_very_long_trip_moves_the_alarm_to_the_previous_evening() -> None:
    """Дорога длиннее утра — подъём уезжает на вчерашний вечер, арифметика не ломается."""
    user = make_user(prep_minutes=60, buffer_minutes=30)

    plan = compute_alarm(WED_ODD, user, None, 12 * 60)  # пара в 10:05, дорога 12 часов

    assert plan.leave_at == msk(WED_ODD - timedelta(days=1), 21, 35)
    assert plan.wake_at == msk(WED_ODD - timedelta(days=1), 20, 35)
    assert plan.day == WED_ODD, "День остаётся днём пары, даже если подъём вчерашний"
    assert plan.lesson_start_at == msk(WED_ODD, 10, 5)


def test_lesson_start_at_is_moscow_aware() -> None:
    plan = compute_alarm(WED_ODD, make_user(), None, 10)

    assert plan.lesson_start_at == msk(WED_ODD, 10, 5)
    assert plan.lesson_start_at is not None and plan.lesson_start_at.tzinfo is not None


def test_silent_plan_has_no_lesson_start() -> None:
    assert compute_alarm(MON_ODD, make_user()).lesson_start_at is None


# ======================================================================================
# 3. Выбор корпуса: ехать надо до корпуса первой очной пары этого дня
# ======================================================================================


def test_tuesday_building_depends_on_week_parity() -> None:
    """Правило 3 из ТЗ: вторник по числителю — Вознесенский, по знаменателю — Садовая."""
    odd = compute_alarm(TUE_ODD, make_user(), None, 20)
    even = compute_alarm(TUE_EVEN, make_user(), None, 20)

    assert odd.building == VOZNESENSKY
    assert even.building == SADOVAYA
    assert odd.lesson is not None and odd.lesson.start_text == "11:40"
    assert even.lesson is not None and even.lesson.start_text == "11:40"


@pytest.mark.parametrize(
    ("day", "expected_building", "expected_start"),
    [
        (TUE_ODD, VOZNESENSKY, "11:40"),
        (WED_ODD, VOZNESENSKY, "10:05"),
        (FRI_ODD, BOLSHAYA_MORSKAYA, "10:05"),
        (SAT_ODD, VOZNESENSKY, "15:20"),
        (TUE_EVEN, SADOVAYA, "11:40"),
        (WED_EVEN, VOZNESENSKY, "10:05"),
        (THU_EVEN, VOZNESENSKY, "10:05"),
    ],
)
def test_building_is_taken_from_the_first_offline_lesson(
    day: date, expected_building: str, expected_start: str
) -> None:
    """Все учебные дни обеих недель: корпус и время берутся у первой очной пары."""
    plan = compute_alarm(day, make_user(), None, 20)

    assert plan.reason == alarm.REASON_WAKE
    assert plan.building == expected_building
    assert plan.lesson is not None and plan.lesson.start_text == expected_start
    assert plan.building == buildings.building_for_date(day)


def test_building_is_never_the_remote_pseudo_address() -> None:
    """«Дистанционное обучение» — не адрес: туда бот везти не должен ни при каких данных."""
    for day in (TUE_ODD, WED_ODD, THU_EVEN, FRI_ODD, SAT_ODD, TUE_EVEN):
        plan = compute_alarm(day, make_user(), None, 20)
        assert plan.building != buildings.REMOTE_ADDRESS


# ======================================================================================
# 4. Первая пара дистанционная, а ехать надо к более поздней очной
# ======================================================================================


def test_alarm_is_computed_for_the_first_offline_lesson_not_the_first_one() -> None:
    """День начинается с дистанта: будим к очной паре и помним, что до неё был дистант."""
    weekday = WED_ODD.isoweekday()
    lessons = (
        make_lesson("R1", weekday, time(10, 5), is_remote=True, subject="Дистант с утра"),
        make_lesson("R2", weekday, time(11, 40), is_remote=True, subject="И ещё дистант"),
        make_lesson("O1", weekday, time(13, 45), building=SADOVAYA, subject="Очная пара"),
    )
    user = make_user(prep_minutes=30, buffer_minutes=10)

    plan = compute_alarm(WED_ODD, user, lessons, 25)

    assert plan.reason == alarm.REASON_WAKE
    assert plan.lesson is not None and plan.lesson.id == "O1"
    assert plan.building == SADOVAYA
    assert plan.leave_at == msk(WED_ODD, 13, 10)
    assert plan.wake_at == msk(WED_ODD, 12, 40)
    # Это нужно тексту ALARM_REMOTE_FIRST: человек должен понять, почему подъём поздний.
    assert plan.first_lesson_is_remote_but_alarm_for_later is True
    assert [lesson.id for lesson in plan.remote_before] == ["R1", "R2"]


def test_remote_after_the_offline_lesson_is_not_reported_as_remote_first() -> None:
    """Дистант вечером (как в четверг по знаменателю) — обычный день, без оговорки."""
    plan = compute_alarm(THU_EVEN, make_user(), None, 20)

    assert plan.first_lesson_is_remote_but_alarm_for_later is False
    assert plan.remote_before == ()


# ======================================================================================
# 5. decide: что делать с планом прямо сейчас
# ======================================================================================


def wake_plan() -> AlarmPlan:
    """План на четверг по знаменателю: подъём 09:00, выход 09:30, пара 10:05."""
    return compute_alarm(THU_EVEN, make_user(prep_minutes=30, buffer_minutes=10), None, 25)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (5, 0, alarm.DECISION_SCHEDULE),
        (8, 59, alarm.DECISION_SCHEDULE),
        (9, 0, alarm.DECISION_SEND_NOW),  # ровно время подъёма
        (9, 10, alarm.DECISION_SEND_NOW),  # проспали, но выйти успеваем
        (9, 29, alarm.DECISION_SEND_NOW),
        (9, 30, alarm.DECISION_TOO_LATE),  # ровно время выхода — будить уже незачем
        (9, 50, alarm.DECISION_TOO_LATE),
        (23, 0, alarm.DECISION_TOO_LATE),
    ],
)
def test_decide_depends_on_the_moment(hour: int, minute: int, expected: str) -> None:
    assert decide(wake_plan(), msk(THU_EVEN, hour, minute)) == expected


def test_decide_the_day_before_is_schedule() -> None:
    assert decide(wake_plan(), msk(THU_EVEN - timedelta(days=1), 22, 0)) == (
        alarm.DECISION_SCHEDULE
    )


@pytest.mark.parametrize(
    "day", [MON_ODD, MON_EVEN, THU_ODD, FRI_EVEN], ids=["пн-чис", "пн-знам", "чт-чис", "пт-знам"]
)
@pytest.mark.parametrize("hour", [0, 6, 12, 23])
def test_silent_plan_stays_silent_at_any_moment(day: date, hour: int) -> None:
    """Молчим — значит молчим: никакое «сейчас» не превращает молчание в будильник."""
    plan = compute_alarm(day, make_user())

    assert decide(plan, msk(day, hour)) == alarm.DECISION_SILENT


def test_decide_for_not_onboarded_is_silent() -> None:
    plan = compute_alarm(WED_ODD, make_user(onboarded=False))

    assert decide(plan, msk(WED_ODD, 8)) == alarm.DECISION_SILENT


def test_decide_rejects_naive_moment() -> None:
    """Naive-время на сервере в UTC сдвинуло бы будильник на три часа — это ошибка."""
    with pytest.raises(ValueError):
        decide(wake_plan(), datetime(2026, 9, 10, 9, 0))


def test_decide_accepts_any_timezone_and_compares_correctly() -> None:
    """Сервер живёт в UTC: 06:00 UTC — это 09:00 по Москве, то есть пора будить."""
    in_utc = msk(THU_EVEN, 9, 0).astimezone(timezone.utc)

    assert in_utc.hour == 6
    assert decide(wake_plan(), in_utc) == alarm.DECISION_SEND_NOW
    assert decide(wake_plan(), msk(THU_EVEN, 12, 0).astimezone(timezone.utc)) == (
        alarm.DECISION_TOO_LATE
    )


# ======================================================================================
# 6. build_alarm_plan: настоящее время в пути, база и сбои маршрутизатора
# ======================================================================================


async def test_build_alarm_plan_uses_router_answer(db: aiosqlite.Connection) -> None:
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    travel_router = CountingRouter(TravelTime(minutes=25, traffic_aware=True, source=SOURCE_DGIS))

    plan = await alarm.build_alarm_plan(
        db,
        user,
        THU_EVEN,
        travel_router,
        at=msk(THU_EVEN, 5, 0),
        fallback_travel_minutes=60,
    )

    assert plan.reason == alarm.REASON_WAKE
    assert plan.travel_minutes == 25
    assert plan.travel_failed is False
    assert plan.traffic_aware is True
    assert plan.is_travel_rough is False
    assert plan.wake_at == msk(THU_EVEN, 9, 0)


async def test_build_alarm_plan_asks_the_router_about_the_right_building(
    db: aiosqlite.Connection,
) -> None:
    """Во вторник по знаменателю дорога считается до Садовой, а не до Вознесенского."""
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db, latitude=59.95, longitude=30.35)
    travel_router = CountingRouter()

    await alarm.build_alarm_plan(
        db,
        user,
        TUE_EVEN,
        travel_router,
        at=msk(TUE_EVEN, 5, 0),
        fallback_travel_minutes=60,
    )

    assert len(travel_router.calls) == 1
    origin, destination, mode, at = travel_router.calls[0]
    assert origin == (59.95, 30.35)
    assert destination == buildings.building_coordinates(SADOVAYA)
    assert mode == "car"
    assert at.tzinfo is not None


async def test_build_alarm_plan_prefers_coordinates_from_database(
    db: aiosqlite.Connection,
) -> None:
    """Кэш геокодинга главнее констант — уточнённые координаты должны применяться."""
    await buildings.save_building(
        db,
        buildings.Building(
            address=VOZNESENSKY, title="Вознесенский, 46", latitude=59.91, longitude=30.31
        ),
    )
    user = await make_onboarded_user(db)
    travel_router = CountingRouter()

    await alarm.build_alarm_plan(
        db, user, WED_ODD, travel_router, at=msk(WED_ODD, 5), fallback_travel_minutes=60
    )

    assert travel_router.calls[0][1] == (59.91, 30.31)


@pytest.mark.parametrize(
    "day", [MON_ODD, MON_EVEN, THU_ODD, FRI_EVEN, SAT_EVEN, SUN_ODD]
)
async def test_router_is_not_called_on_silent_days(
    db: aiosqlite.Connection, day: date
) -> None:
    """Дешёвая проверка до сети: в дистант и в пустой день маршрут не запрашивается."""
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db)
    travel_router = CountingRouter()

    plan = await alarm.build_alarm_plan(
        db, user, day, travel_router, at=msk(day, 5), fallback_travel_minutes=60
    )

    assert plan.should_wake is False
    assert travel_router.calls == [], "Лишний запрос к платному API в день без поездки"


async def test_router_is_not_called_for_not_onboarded_user(
    db: aiosqlite.Connection,
) -> None:
    travel_router = CountingRouter()

    plan = await alarm.build_alarm_plan(
        db,
        make_user(onboarded=False),
        WED_ODD,
        travel_router,
        at=msk(WED_ODD, 5),
        fallback_travel_minutes=60,
    )

    assert plan.reason == alarm.REASON_NOT_ONBOARDED
    assert travel_router.calls == []


async def test_router_error_falls_back_to_configured_minutes(
    db: aiosqlite.Connection,
) -> None:
    """Главное свойство этапа: маршрутизатор упал — будильник всё равно есть."""
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    travel_router = CountingRouter(error=RouterError("2ГИС: ответ не пришёл за 10 с"))

    plan = await alarm.build_alarm_plan(
        db,
        user,
        THU_EVEN,
        travel_router,
        at=msk(THU_EVEN, 5),
        fallback_travel_minutes=45,
    )

    assert plan.reason == alarm.REASON_WAKE
    assert plan.travel_minutes == 45
    assert plan.travel_failed is True
    assert plan.is_travel_rough is True
    assert plan.traffic_aware is False
    # 10:05 − 45 дороги − 10 запаса = 09:10, минус 30 на сборы = 08:40.
    assert plan.leave_at == msk(THU_EVEN, 9, 10)
    assert plan.wake_at == msk(THU_EVEN, 8, 40)


@pytest.mark.parametrize(
    "error",
    [
        RouterError("сервис не ответил"),
        TimeoutError("таймаут сети"),
        RuntimeError("совсем неожиданная ошибка"),
        ValueError("битый ответ"),
    ],
    ids=["RouterError", "TimeoutError", "RuntimeError", "ValueError"],
)
async def test_any_router_failure_still_produces_an_alarm(
    db: aiosqlite.Connection, error: BaseException
) -> None:
    """Будильник людей важнее чистоты исключений: наружу не должно вылетать ничего."""
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db)
    travel_router = CountingRouter(error=error)

    plan = await alarm.build_alarm_plan(
        db, user, WED_ODD, travel_router, at=msk(WED_ODD, 5), fallback_travel_minutes=50
    )

    assert plan.reason == alarm.REASON_WAKE
    assert plan.travel_minutes == 50
    assert plan.travel_failed is True


async def test_unknown_building_falls_back_without_calling_router(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Координат корпуса нет нигде — будим по запасному времени, а не молчим."""
    monkeypatch.setattr(buildings, "building_coordinates", lambda address: None)
    user = await make_onboarded_user(db)
    travel_router = CountingRouter()

    plan = await alarm.build_alarm_plan(
        db, user, WED_ODD, travel_router, at=msk(WED_ODD, 5), fallback_travel_minutes=55
    )

    assert plan.reason == alarm.REASON_WAKE
    assert plan.travel_minutes == 55
    assert plan.travel_failed is True
    assert travel_router.calls == []


async def test_build_alarm_plan_survives_broken_database(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Таблица корпусов не читается — берём координаты из констант и всё равно будим."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(buildings, "get_building", boom)
    user = await make_onboarded_user(db)
    travel_router = CountingRouter(TravelTime(minutes=25, traffic_aware=True, source=SOURCE_DGIS))

    plan = await alarm.build_alarm_plan(
        db, user, WED_ODD, travel_router, at=msk(WED_ODD, 5), fallback_travel_minutes=60
    )

    assert plan.travel_minutes == 25
    assert plan.travel_failed is False


async def test_build_alarm_plan_with_stub_router_is_marked_rough(
    db: aiosqlite.Connection,
) -> None:
    """Без ключа сервиса маршрутов будильник считается по прямой и честно помечен."""
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db)

    plan = await alarm.build_alarm_plan(
        db, user, WED_ODD, StubRouter(), at=msk(WED_ODD, 5), fallback_travel_minutes=60
    )

    assert plan.reason == alarm.REASON_WAKE
    assert plan.travel_failed is False
    assert plan.is_travel_rough is True
    assert plan.traffic_aware is False
    assert plan.travel_minutes is not None and plan.travel_minutes > 0


async def test_build_alarm_plan_accepts_custom_lessons(db: aiosqlite.Connection) -> None:
    """Свой список занятий должен доходить до расчёта — иначе синтетику не проверить."""
    await buildings.seed_buildings(db)
    user = await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    lessons = (
        make_lesson("R1", WED_ODD.isoweekday(), time(9, 0), is_remote=True),
        make_lesson("O1", WED_ODD.isoweekday(), time(13, 45), building=SADOVAYA),
    )

    plan = await alarm.build_alarm_plan(
        db,
        user,
        WED_ODD,
        CountingRouter(TravelTime(minutes=25, traffic_aware=True, source=SOURCE_DGIS)),
        at=msk(WED_ODD, 5),
        fallback_travel_minutes=60,
        lessons=lessons,
    )

    assert plan.lesson is not None and plan.lesson.id == "O1"
    assert plan.first_lesson_is_remote_but_alarm_for_later is True
    assert plan.wake_at == msk(WED_ODD, 12, 40)
