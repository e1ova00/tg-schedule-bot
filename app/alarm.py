"""Утренний будильник: во сколько будить, до какого корпуса ехать и когда молчать.

Модуль состоит из двух слоёв:

* `compute_alarm` и `decide` — чистые функции. Никакого Telegram, сети, базы и
  планировщика: на вход дата, пользователь, занятия и уже посчитанное время в пути,
  на выход — `AlarmPlan`. Это и есть то, что проверяется тестами.
* `build_alarm_plan` — асинхронная обвязка: достаёт корпус, спрашивает у маршрутизатора
  время в пути (а при сбое подставляет запасное) и зовёт ту же чистую функцию.

«Молчим» здесь никогда не выражается голым `None`: у каждого молчания есть причина,
её показывает /preview и по ней же удобно писать тесты.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import aiosqlite

from app import buildings, users
from app.clock import MOSCOW
from app.routing.base import Router, RouterError
from app.schedule import Lesson, lessons_on

logger = logging.getLogger(__name__)

# Почему бот будит или молчит. Строки, а не Enum: их видно в логах как есть,
# и с ними проще сравнивать в тестах.
REASON_WAKE = "wake"
REASON_NOT_ONBOARDED = "not_onboarded"
REASON_NO_LESSONS = "no_lessons"
REASON_ALL_REMOTE = "all_remote"

# Что делать с планом в конкретный момент времени.
DECISION_SCHEDULE = "schedule"  # время подъёма ещё впереди — ставим задачу
DECISION_SEND_NOW = "send_now"  # подъём проспан, но выйти вовремя ещё реально
DECISION_TOO_LATE = "too_late"  # выходить уже поздно, будить незачем
DECISION_SILENT = "silent"  # будильника на этот день нет вовсе


@dataclass(frozen=True, slots=True)
class AlarmPlan:
    """Решение по одному дню и одному человеку: будим во столько-то или молчим почему-то."""

    day: date
    reason: str
    # Дальше — только для reason == REASON_WAKE.
    lesson: Lesson | None = None
    building: str | None = None
    wake_at: datetime | None = None
    leave_at: datetime | None = None
    travel_minutes: int | None = None
    # Оценка «по прямой» или запасная из конфига, а не настоящий маршрут.
    is_travel_rough: bool = False
    # Учтены ли настоящие пробки (2ГИС). У остальных источников False.
    traffic_aware: bool = False
    # Сервис маршрутов не ответил и время в пути взято из настроек.
    travel_failed: bool = False
    # Первая пара дня дистанционная, а будильник поставлен по более поздней очной.
    first_lesson_is_remote_but_alarm_for_later: bool = False
    # Дистанционные пары, которые идут до очной. Нужны тексту будильника.
    remote_before: tuple[Lesson, ...] = ()

    @property
    def should_wake(self) -> bool:
        return self.reason == REASON_WAKE

    @property
    def lesson_start_at(self) -> datetime | None:
        """Начало очной пары как момент времени по Москве."""
        if self.lesson is None:
            return None
        return datetime.combine(self.day, self.lesson.start, tzinfo=MOSCOW)


def _silent(day: date, reason: str) -> AlarmPlan:
    return AlarmPlan(day=day, reason=reason)


def compute_alarm(
    d: date,
    user: users.User | None,
    lessons: Sequence[Lesson] | None = None,
    travel_minutes: int = 0,
    is_travel_rough: bool = False,
    traffic_aware: bool = False,
    travel_failed: bool = False,
) -> AlarmPlan:
    """Во сколько будить в день `d` — или почему в этот день будильника не будет.

    Чистая функция: время в пути ей передают готовым, сама она никуда не ходит.

    ```
    время_выхода   = начало_первой_очной_пары − время_в_пути − запас
    время_будильника = время_выхода − время_на_сборы
    ```

    Порядок проверок: сначала настройки (без координат и минут считать нечего),
    потом пустой день, потом полностью дистанционный. Понедельник у группы весь
    дистанционный, поэтому попадает в `REASON_ALL_REMOTE` и будильника не получает.
    """
    if not users.is_onboarded(user) or user is None:
        return _silent(d, REASON_NOT_ONBOARDED)

    day_lessons = lessons_on(d, lessons)
    if not day_lessons:
        return _silent(d, REASON_NO_LESSONS)

    target: Lesson | None = None
    remote_before: list[Lesson] = []
    for lesson in day_lessons:
        if lesson.is_remote:
            remote_before.append(lesson)
            continue
        target = lesson
        break

    if target is None:
        return _silent(d, REASON_ALL_REMOTE)

    # prep/buffer уже проверены в is_onboarded, но пусть тип будет честным.
    prep = user.prep_minutes or 0
    buffer = user.buffer_minutes or 0

    starts_at = datetime.combine(d, target.start, tzinfo=MOSCOW)
    leave_at = starts_at - timedelta(minutes=travel_minutes + buffer)
    wake_at = leave_at - timedelta(minutes=prep)

    return AlarmPlan(
        day=d,
        reason=REASON_WAKE,
        lesson=target,
        building=target.building,
        wake_at=wake_at,
        leave_at=leave_at,
        travel_minutes=travel_minutes,
        is_travel_rough=is_travel_rough,
        traffic_aware=traffic_aware,
        travel_failed=travel_failed,
        first_lesson_is_remote_but_alarm_for_later=bool(remote_before),
        remote_before=tuple(remote_before),
    )


def decide(plan: AlarmPlan, moment: datetime) -> str:
    """Что делать с планом прямо сейчас. `moment` — «сейчас» с часовым поясом.

    Момент передаётся параметром, а не берётся из `clock.now()`: так тест может
    подставить любое утро, не трогая системные часы.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("decide ждёт время с часовым поясом (app.clock.now()).")

    if not plan.should_wake or plan.wake_at is None or plan.leave_at is None:
        return DECISION_SILENT
    if moment < plan.wake_at:
        return DECISION_SCHEDULE
    # Подъём проспан из-за позднего старта бота, но пока не поздно выходить —
    # лучше разбудить сразу, чем промолчать.
    if moment < plan.leave_at:
        return DECISION_SEND_NOW
    return DECISION_TOO_LATE


# --- Асинхронная обвязка: база и маршрутизатор ---------------------------------------


async def build_alarm_plan(
    db: aiosqlite.Connection,
    user: users.User | None,
    day: date,
    travel_router: Router,
    *,
    at: datetime,
    fallback_travel_minutes: int,
    lessons: Sequence[Lesson] | None = None,
) -> AlarmPlan:
    """`compute_alarm` с настоящим временем в пути.

    `at` — момент, на который спрашиваем дорогу (обычно «сейчас»): это лучшее доступное
    приближение к пробкам будущего выезда. Если маршрутизатор не ответил, берём
    `fallback_travel_minutes` из настроек и помечаем план как посчитанный на глазок —
    молчать из-за упавшего API бот не должен.
    """
    # Сначала дешёвая проверка без сети: в пустой день и в дистант ехать некуда.
    preflight = compute_alarm(day, user, lessons)
    if not preflight.should_wake:
        return preflight

    assert user is not None and preflight.lesson is not None  # гарантировано compute_alarm

    minutes = fallback_travel_minutes
    is_rough = True
    traffic_aware = False
    failed = True

    origin = user.coordinates
    destination = await buildings.building_point(db, preflight.lesson.building)

    if origin is None or destination is None:
        logger.error(
            "Не нашлись координаты для будильника %s (корпус «%s»), беру запасные %d минут",
            day,
            preflight.lesson.building,
            fallback_travel_minutes,
        )
    else:
        try:
            travel = await travel_router.travel_time(
                origin, destination, user.transport_mode or "", at
            )
        except RouterError:
            logger.warning(
                "Сервис маршрутов не ответил, будильник на %s считаю по запасным %d минутам",
                day,
                fallback_travel_minutes,
                exc_info=True,
            )
        except Exception:  # noqa: BLE001 — будильник не должен падать ни на каких данных
            logger.exception("Неожиданная ошибка при расчёте дороги для будильника на %s", day)
        else:
            minutes = travel.minutes
            is_rough = travel.is_rough
            traffic_aware = travel.traffic_aware
            failed = False

    return compute_alarm(
        day,
        user,
        lessons,
        minutes,
        is_rough,
        traffic_aware,
        failed,
    )


__all__ = [
    "DECISION_SCHEDULE",
    "DECISION_SEND_NOW",
    "DECISION_SILENT",
    "DECISION_TOO_LATE",
    "REASON_ALL_REMOTE",
    "REASON_NOT_ONBOARDED",
    "REASON_NO_LESSONS",
    "REASON_WAKE",
    "AlarmPlan",
    "build_alarm_plan",
    "compute_alarm",
    "decide",
]
