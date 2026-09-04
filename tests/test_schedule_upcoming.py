"""Тесты `app.schedule.upcoming_offline_lesson` — ближайшая пара, до которой надо доехать.

Функция появилась на этапе 4: от неё зависит, куда `/route` считает дорогу. Ни сети,
ни Telegram: на вход — момент времени с поясом, на выход — дата и занятие.

Календарь тот же, что в `tests/test_schedule.py`: неделя 31.08–06.09.2026 — числитель,
07.09–13.09.2026 — знаменатель (контрольные даты из `CLAUDE.md`).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.clock import MOSCOW
from app.schedule import Lesson, clear_cache, lessons_on, load_lessons, upcoming_offline_lesson

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


def moment(day: date, hour: int, minute: int = 0) -> datetime:
    """Момент по Москве — так, как его отдаёт `app.clock.now()`."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)


# ======================================================================================
# 1. Сегодня ещё есть куда ехать
# ======================================================================================


def test_finds_first_lesson_of_the_day_early_in_the_morning() -> None:
    """Среда, 08:00 — впереди весь день, ближайшая очная пара первая по расписанию."""
    found = upcoming_offline_lesson(moment(WED_ODD, 8))

    assert found is not None
    day, lesson = found
    assert day == WED_ODD
    assert lesson.start == time(10, 5)
    assert lesson.building == VOZNESENSKY


def test_skips_lesson_that_has_already_started() -> None:
    """Среда, 12:00: пара 11:40 уже идёт, ехать надо на следующую — в 13:45."""
    found = upcoming_offline_lesson(moment(WED_ODD, 12))

    assert found is not None
    day, lesson = found
    assert day == WED_ODD
    assert lesson.start == time(13, 45)


def test_next_lesson_can_be_in_another_building_than_the_first_one() -> None:
    """Главная ловушка дня: в 13:45 в среду пара уже на Садовой, а день начинался на Вознесенском.

    Если бы `/route` брал «корпус дня», он повёз бы человека не туда.
    """
    day_lessons = lessons_on(WED_ODD, load_lessons())
    first_offline = next(lesson for lesson in day_lessons if not lesson.is_remote)

    found = upcoming_offline_lesson(moment(WED_ODD, 12))

    assert found is not None
    _, lesson = found
    assert first_offline.building == VOZNESENSKY
    assert lesson.building == SADOVAYA
    assert lesson.building != first_offline.building


def test_lesson_starting_exactly_now_is_considered_late() -> None:
    """Ровно 10:05 — пара началась, ехать на неё уже поздно: берём следующую."""
    found = upcoming_offline_lesson(moment(WED_ODD, 10, 5))

    assert found is not None
    _, lesson = found
    assert lesson.start == time(11, 40)


def test_minute_before_the_lesson_still_counts() -> None:
    """10:04 — пара ещё не началась, значит она и есть ближайшая."""
    found = upcoming_offline_lesson(moment(WED_ODD, 10, 4))

    assert found is not None
    _, lesson = found
    assert lesson.start == time(10, 5)


# ======================================================================================
# 2. Сегодня уже поздно, а завтра есть
# ======================================================================================


def test_rolls_over_to_tomorrow_when_today_is_over() -> None:
    """Вторник, 20:00 — пары кончились, ближайшая очная будет завтра утром."""
    found = upcoming_offline_lesson(moment(TUE_ODD, 20))

    assert found is not None
    day, lesson = found
    assert day == WED_ODD
    assert lesson.start == time(10, 5)


def test_rolls_over_from_fully_remote_monday() -> None:
    """Понедельник целиком дистанционный: ехать сегодня некуда, зато завтра — вторник."""
    found = upcoming_offline_lesson(moment(MON_ODD, 9))

    assert found is not None
    day, lesson = found
    assert day == TUE_ODD
    assert lesson.is_remote is False
    assert lesson.building == VOZNESENSKY


def test_skips_remaining_remote_lesson_and_takes_tomorrow() -> None:
    """Пятница числителя, 13:00: до конца дня осталась только дистанционная пара в 15:20.

    Ехать на неё не надо, поэтому ответом становится суббота.
    """
    friday_rest = [
        lesson
        for lesson in lessons_on(FRI_ODD, load_lessons())
        if lesson.start > time(13, 0)
    ]
    assert friday_rest and all(lesson.is_remote for lesson in friday_rest)

    found = upcoming_offline_lesson(moment(FRI_ODD, 13))

    assert found is not None
    day, lesson = found
    assert day == SAT_ODD
    assert lesson.start == time(15, 20)
    assert lesson.building == VOZNESENSKY


def test_empty_thursday_of_odd_week_looks_at_friday() -> None:
    """Четверг по числителю пустой — значит ближайшая поездка в пятницу на Большую Морскую."""
    assert lessons_on(THU_ODD, load_lessons()) == []

    found = upcoming_offline_lesson(moment(THU_ODD, 9))

    assert found is not None
    day, lesson = found
    assert day == FRI_ODD
    assert lesson.building == BOLSHAYA_MORSKAYA


def test_tuesday_destination_depends_on_parity() -> None:
    """Вторник по числителю — Вознесенский, по знаменателю — Садовая (правило 3 из ТЗ)."""
    odd = upcoming_offline_lesson(moment(TUE_ODD, 8))
    even = upcoming_offline_lesson(moment(TUE_EVEN, 8))

    assert odd is not None and even is not None
    assert odd[1].building == VOZNESENSKY
    assert even[1].building == SADOVAYA


# ======================================================================================
# 3. Ехать некуда
# ======================================================================================


def test_no_trips_on_sunday_before_remote_monday() -> None:
    """Воскресенье пустое, понедельник дистанционный — ближайшие два дня ехать некуда."""
    assert upcoming_offline_lesson(moment(SUN_ODD, 9)) is None


def test_no_trips_after_the_last_saturday_lesson() -> None:
    """Суббота числителя, 18:00: пара уже идёт, а воскресенье пустое."""
    assert upcoming_offline_lesson(moment(SAT_ODD, 18)) is None


def test_no_trips_on_empty_saturday_of_even_week() -> None:
    """Суббота по знаменателю пустая, воскресенье тоже — «ехать никуда не надо»."""
    assert lessons_on(SAT_EVEN, load_lessons()) == []

    assert upcoming_offline_lesson(moment(SAT_EVEN, 10)) is None


def test_no_trips_on_empty_friday_of_even_week() -> None:
    """Пятница знаменателя пустая, суббота знаменателя — тоже."""
    assert upcoming_offline_lesson(moment(FRI_EVEN, 9)) is None


def test_lookahead_window_can_be_widened() -> None:
    """С запасом в четыре дня из пустой субботы знаменателя виден вторник."""
    found = upcoming_offline_lesson(moment(SAT_EVEN, 10), days=4)

    assert found is not None
    day, lesson = found
    assert day == date(2026, 9, 15)  # вторник следующей (нечётной) недели
    assert lesson.building == VOZNESENSKY


def test_zero_days_is_treated_as_today() -> None:
    """`days=0` не должно означать «смотреть в пустоту»: минимум один день всё равно берём."""
    found = upcoming_offline_lesson(moment(WED_ODD, 8), days=0)

    assert found is not None
    assert found[0] == WED_ODD


# ======================================================================================
# 4. Часовой пояс и защита от naive datetime
# ======================================================================================


def test_utc_moment_is_converted_to_moscow_date() -> None:
    """21:30 UTC вторника — это уже среда по Москве, и день расписания должен быть средой."""
    found = upcoming_offline_lesson(datetime(2026, 9, 1, 21, 30, tzinfo=timezone.utc))

    assert found is not None
    day, lesson = found
    assert day == WED_ODD
    assert lesson.start == time(10, 5)


def test_utc_moment_during_the_day_keeps_the_same_date() -> None:
    """09:00 UTC среды — 12:00 по Москве: пара 11:40 уже началась."""
    found = upcoming_offline_lesson(datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc))

    assert found is not None
    day, lesson = found
    assert day == WED_ODD
    assert lesson.start == time(13, 45)


def test_naive_datetime_is_an_error() -> None:
    """Naive-время незаметно сдвинуло бы расчёт на три часа — лучше явная ошибка."""
    with pytest.raises(ValueError) as exc_info:
        upcoming_offline_lesson(datetime(2026, 9, 2, 8, 0))

    assert "часовым поясом" in str(exc_info.value)


# ======================================================================================
# 5. Общие свойства
# ======================================================================================


def test_result_is_never_remote_and_never_in_the_past() -> None:
    """Проверяем каждый час двух недель: ответ — только очная пара и только впереди."""
    lessons = load_lessons()
    for shift in range(14):
        day = MON_ODD + timedelta(days=shift)
        for hour in range(0, 24, 3):
            found = upcoming_offline_lesson(moment(day, hour), days=7, lessons=lessons)
            if found is None:
                continue
            found_day, lesson = found
            assert lesson.is_remote is False
            assert found_day >= day
            if found_day == day:
                assert lesson.start > time(hour, 0)


def test_custom_lessons_are_used_instead_of_the_file() -> None:
    """Свой список занятий: функция не обязана лезть в data/schedule_4md4.json."""
    custom = (
        Lesson(
            id="T1",
            weekday=3,  # среда
            start=time(9, 0),
            end=time(10, 30),
            parity="both",
            subject="Тестовый предмет",
            kind="Пр",
            teacher="Тестов Т.Т.",
            building=BOLSHAYA_MORSKAYA,
            room="100",
            is_remote=False,
        ),
    )

    found = upcoming_offline_lesson(moment(WED_ODD, 8), lessons=custom)

    assert found == (WED_ODD, custom[0])
