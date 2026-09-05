"""Тесты чистой логики заметок: app/notes.py.

Ни Telegram, ни базы, ни сети, ни системных часов: на вход — занятие из настоящего
`data/schedule_4md4.json` и дата, на выход — дата или момент времени с поясом.

Что здесь важнее всего:

* **срок заметки** — это дата *следующего такого же* занятия. Для пары «каждую неделю»
  (`parity="both"`) это ровно через 7 дней, для «числителя»/«знаменателя» — через 14.
  Ошибка здесь означает, что бот напомнит про домашку не в тот день;
* **три момента времени** — вопрос после пары, напоминание за сутки и утренний повтор.
  Каждый проверяется на конкретных числах, а не «примерно».
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from app import notes
from app.clock import MOSCOW
from app.notes import (
    DAY_BEFORE_HOURS,
    MORNING_MIN_LEAD,
    PROMPT_DELAY_MINUTES,
    day_before_reminder_at,
    lesson_ends_at,
    lesson_starts_at,
    morning_reminder_at,
    next_lesson_occurrence,
    occurs_on,
    prompt_at,
    week_bounds,
)
from app.schedule import Lesson, clear_cache, lesson_by_id, week_parity

# Реальные пары из data/schedule_4md4.json.
BOTH_WED = "L1221"  # среда, каждую неделю, 10:05–11:30, Прикладной дизайн
ODD_TUE = "L1216"  # вторник, числитель, 11:40–13:05, БЖД (Лаб)
EVEN_TUE = "L1217"  # вторник, знаменатель, 11:40–13:05, АВСиС (Пр)
BOTH_MON = "L1215"  # понедельник, каждую неделю, 15:20–16:45, дистант
ODD_SAT = "L1231"  # суббота, числитель, 15:20–18:20

MON_ODD = date(2026, 8, 31)
TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
SAT_ODD = date(2026, 9, 5)
SUN_ODD = date(2026, 9, 6)
TUE_EVEN = date(2026, 9, 8)
WED_EVEN = date(2026, 9, 9)


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


def lesson(lesson_id: str) -> Lesson:
    """Занятие из настоящего расписания. Выдуманных данных в тестах не держим."""
    found = lesson_by_id(lesson_id)
    assert found is not None, f"В data/schedule_4md4.json нет пары {lesson_id}"
    return found


def msk(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)


# ======================================================================================
# 1. occurs_on: идёт ли пара в этот день
# ======================================================================================


@pytest.mark.parametrize(
    ("lesson_id", "day", "expected"),
    [
        pytest.param(ODD_TUE, TUE_ODD, True, id="числитель во вторник числителя"),
        pytest.param(ODD_TUE, TUE_EVEN, False, id="числитель во вторник знаменателя"),
        pytest.param(EVEN_TUE, TUE_EVEN, True, id="знаменатель во вторник знаменателя"),
        pytest.param(EVEN_TUE, TUE_ODD, False, id="знаменатель во вторник числителя"),
        pytest.param(BOTH_WED, WED_ODD, True, id="каждую неделю по числителю"),
        pytest.param(BOTH_WED, WED_EVEN, True, id="каждую неделю по знаменателю"),
        pytest.param(BOTH_WED, TUE_ODD, False, id="другой день недели"),
        pytest.param(BOTH_WED, SUN_ODD, False, id="воскресенье"),
    ],
)
def test_occurs_on_respects_weekday_and_parity(
    lesson_id: str, day: date, expected: bool
) -> None:
    assert occurs_on(lesson(lesson_id), day) is expected


# ======================================================================================
# 2. next_lesson_occurrence: срок заметки
# ======================================================================================


def test_weekly_lesson_repeats_in_seven_days() -> None:
    """parity="both" — пара каждую неделю, значит следующая ровно через 7 дней."""
    assert next_lesson_occurrence(lesson(BOTH_WED), WED_ODD) == WED_ODD + timedelta(days=7)


def test_weekly_lesson_next_occurrence_falls_on_the_other_parity() -> None:
    """Проверка, что «каждую неделю» действительно каждую, а не через одну."""
    following = next_lesson_occurrence(lesson(BOTH_WED), WED_ODD)

    assert following is not None
    assert week_parity(WED_ODD) == "odd"
    assert week_parity(following) == "even"


def test_weekly_monday_lesson_also_repeats_in_seven_days() -> None:
    """Понедельник целиком дистанционный, но домашку на нём задать могут."""
    assert next_lesson_occurrence(lesson(BOTH_MON), MON_ODD) == MON_ODD + timedelta(days=7)


@pytest.mark.parametrize(
    ("lesson_id", "start", "expected"),
    [
        pytest.param(ODD_TUE, TUE_ODD, date(2026, 9, 15), id="числитель, вторник"),
        pytest.param(EVEN_TUE, TUE_EVEN, date(2026, 9, 22), id="знаменатель, вторник"),
        pytest.param(ODD_SAT, SAT_ODD, date(2026, 9, 19), id="числитель, суббота"),
    ],
)
def test_parity_lesson_repeats_in_fourteen_days(
    lesson_id: str, start: date, expected: date
) -> None:
    """Числитель и знаменатель чередуются, поэтому шаг — две недели, а не одна."""
    following = next_lesson_occurrence(lesson(lesson_id), start)

    assert following == expected
    assert following == start + timedelta(days=14)


def test_next_occurrence_keeps_parity_and_weekday() -> None:
    """Инвариант: следующая пара — тот же день недели и та же чётность."""
    source = lesson(ODD_TUE)
    day = TUE_ODD

    for _ in range(5):
        following = next_lesson_occurrence(source, day)
        assert following is not None
        assert following.isoweekday() == source.weekday
        assert week_parity(following) == source.parity
        day = following


def test_next_occurrence_is_strictly_after_the_given_day() -> None:
    """Заметку заводят в день самой пары — сегодняшняя дата сроком быть не может."""
    assert next_lesson_occurrence(lesson(ODD_TUE), TUE_ODD) != TUE_ODD


def test_next_occurrence_works_from_a_day_without_this_lesson() -> None:
    """Заметку можно завести и позже: считаем от любой даты, не только от дня пары."""
    assert next_lesson_occurrence(lesson(ODD_TUE), WED_ODD) == date(2026, 9, 15)


def test_next_occurrence_crosses_the_new_year_for_weekly_lesson() -> None:
    """Смена года — не повод потерять срок: 30 декабря + 7 дней = 6 января."""
    assert next_lesson_occurrence(lesson(BOTH_WED), date(2026, 12, 30)) == date(2027, 1, 6)


def test_next_occurrence_crosses_the_new_year_for_parity_lesson() -> None:
    """То же самое для «через неделю»: 29 декабря + 14 дней = 12 января."""
    assert next_lesson_occurrence(lesson(EVEN_TUE), date(2026, 12, 29)) == date(2027, 1, 12)


# --- Горизонт поиска ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("lesson_id", "start", "enough", "not_enough"),
    [
        pytest.param(BOTH_WED, WED_ODD, 7, 6, id="каждую неделю"),
        pytest.param(ODD_TUE, TUE_ODD, 14, 13, id="числитель"),
    ],
)
def test_horizon_boundary(
    lesson_id: str, start: date, enough: int, not_enough: int
) -> None:
    """Ровно в день следующей пары горизонт ещё срабатывает, за день до — уже нет."""
    source = lesson(lesson_id)

    assert next_lesson_occurrence(source, start, horizon_days=enough) is not None
    assert next_lesson_occurrence(source, start, horizon_days=not_enough) is None


@pytest.mark.parametrize("horizon", [0, -1, -100])
def test_non_positive_horizon_gives_none_without_crashing(horizon: int) -> None:
    """Горизонт «ноль дней» — не ошибка, просто срока у заметки не будет."""
    assert next_lesson_occurrence(lesson(BOTH_WED), WED_ODD, horizon_days=horizon) is None


def test_default_horizon_covers_two_months() -> None:
    assert notes.DEFAULT_HORIZON_DAYS >= 14, "Иначе у пар по чётности не будет срока вовсе"


# --- Пары, которой нет в расписании -------------------------------------------------


def test_unknown_lesson_id_returns_none_instead_of_raising() -> None:
    """Заметка могла остаться от старой версии расписания — падать из-за этого нельзя.

    `next_lesson_occurrence` принимает уже найденное занятие, поэтому «несуществующая
    пара» отсекается раньше: `lesson_by_id` честно возвращает None, а вызывающий код
    (хендлер и планировщик) просто оставляет заметку без срока.
    """
    assert lesson_by_id("L9999") is None
    assert lesson_by_id("") is None


def test_lesson_that_never_happens_gives_none() -> None:
    """Искусственный случай: занятие в день недели, которого в семестре нет."""
    ghost = Lesson(
        id="L0000",
        weekday=7,  # воскресенье
        start=time(10, 5),
        end=time(11, 30),
        parity="odd",
        subject="Занятие-призрак",
        kind="Пр",
        teacher="—",
        building="Дистанционное обучение",
        room="ДО",
        is_remote=True,
    )

    # Воскресенья в расписании нет, но функция про это не знает: она честно найдёт
    # ближайшее воскресенье нужной чётности. Проверяем главное — не падает и не врёт.
    following = next_lesson_occurrence(ghost, WED_ODD)
    assert following is not None
    assert following.isoweekday() == 7
    assert week_parity(following) == "odd"


# ======================================================================================
# 3. Три момента времени
# ======================================================================================


def test_prompt_is_ten_minutes_after_the_bell() -> None:
    """L1216 заканчивается в 13:05 → вопрос про домашку в 13:15."""
    assert prompt_at(lesson(ODD_TUE), TUE_ODD) == msk(TUE_ODD, 13, 15)


def test_prompt_time_is_aware_and_moscow() -> None:
    moment = prompt_at(lesson(BOTH_WED), WED_ODD)

    assert moment.utcoffset() == timedelta(hours=3)
    assert moment == msk(WED_ODD, 11, 40)  # пара 10:05–11:30 плюс десять минут


def test_prompt_delay_is_exactly_ten_minutes() -> None:
    source = lesson(ODD_SAT)

    delta = prompt_at(source, SAT_ODD) - lesson_ends_at(source, SAT_ODD)

    assert delta == timedelta(minutes=PROMPT_DELAY_MINUTES) == timedelta(minutes=10)


def test_long_lesson_prompt_uses_the_end_not_the_start() -> None:
    """L1231 идёт 15:20–18:20: спрашивать надо в 18:30, а не в 15:30."""
    assert prompt_at(lesson(ODD_SAT), SAT_ODD) == msk(SAT_ODD, 18, 30)


def test_day_before_reminder_is_exactly_twenty_four_hours_earlier() -> None:
    """Следующая L1216 — 15 сентября в 11:40, значит напоминание 14-го в 11:40."""
    source = lesson(ODD_TUE)
    following = date(2026, 9, 15)

    moment = day_before_reminder_at(following, source)

    assert moment == msk(date(2026, 9, 14), 11, 40)
    assert lesson_starts_at(source, following) - moment == timedelta(hours=24)
    assert DAY_BEFORE_HOURS == 24


def test_day_before_reminder_can_land_in_the_previous_month() -> None:
    """1 октября 10:05 → напоминание 30 сентября: границы месяца ничего не ломают."""
    source = lesson(BOTH_WED)

    assert day_before_reminder_at(date(2026, 10, 7), source) == msk(date(2026, 10, 6), 10, 5)


def test_morning_reminder_is_at_the_configured_time() -> None:
    """Пара в 11:40, напоминаем в 08:00 — до пары ещё 3 часа 40 минут."""
    moment = morning_reminder_at(date(2026, 9, 15), lesson(ODD_TUE), time(8, 0))

    assert moment == msk(date(2026, 9, 15), 8, 0)


def test_morning_reminder_is_skipped_when_the_lesson_is_too_close() -> None:
    """До пары 40 минут — второе напоминание за утро было бы просто давлением."""
    assert morning_reminder_at(date(2026, 9, 15), lesson(ODD_TUE), time(11, 0)) is None


def test_morning_reminder_boundary_exactly_one_hour_still_goes() -> None:
    """Граница: ровно час до пары — напоминание ещё имеет смысл."""
    assert morning_reminder_at(date(2026, 9, 15), lesson(ODD_TUE), time(10, 40)) == msk(
        date(2026, 9, 15), 10, 40
    )


def test_morning_reminder_boundary_one_minute_less_is_skipped() -> None:
    """Обратная граница: 59 минут до пары — уже молчим."""
    assert morning_reminder_at(date(2026, 9, 15), lesson(ODD_TUE), time(10, 41)) is None


def test_morning_min_lead_is_an_hour() -> None:
    assert MORNING_MIN_LEAD == timedelta(hours=1)


def test_morning_reminder_for_an_early_lesson_with_late_setting() -> None:
    """Пара в 10:05 и NOTES_MORNING_TIME=09:30 → напоминания утром не будет."""
    assert morning_reminder_at(WED_EVEN, lesson(BOTH_WED), time(9, 30)) is None


# ======================================================================================
# 4. Начало и конец пары
# ======================================================================================


def test_lesson_bounds_are_aware_moscow_times() -> None:
    source = lesson(BOTH_WED)

    assert lesson_starts_at(source, WED_ODD) == msk(WED_ODD, 10, 5)
    assert lesson_ends_at(source, WED_ODD) == msk(WED_ODD, 11, 30)
    assert lesson_starts_at(source, WED_ODD).tzinfo is not None
    assert lesson_ends_at(source, WED_ODD).utcoffset() == timedelta(hours=3)


# ======================================================================================
# 5. week_bounds: фильтр «За неделю» в /notes
# ======================================================================================


@pytest.mark.parametrize(
    "day",
    [
        pytest.param(MON_ODD, id="понедельник"),
        pytest.param(WED_ODD, id="среда"),
        pytest.param(SAT_ODD, id="суббота"),
        pytest.param(SUN_ODD, id="воскресенье"),
    ],
)
def test_week_bounds_are_monday_to_sunday(day: date) -> None:
    """Неделя у группы всегда от понедельника — как и чётность."""
    monday, sunday = week_bounds(day)

    assert monday == MON_ODD
    assert sunday == SUN_ODD
    assert monday.isoweekday() == 1
    assert sunday.isoweekday() == 7
    assert (sunday - monday).days == 6
    assert monday <= day <= sunday


def test_week_bounds_of_the_next_week_do_not_overlap() -> None:
    """Воскресенье — граничный день: понедельник уже другая неделя."""
    _, this_sunday = week_bounds(SUN_ODD)
    next_monday, _ = week_bounds(SUN_ODD + timedelta(days=1))

    assert next_monday == this_sunday + timedelta(days=1)


def test_week_bounds_cross_the_new_year() -> None:
    """Неделя может лежать в двух годах — фильтр списка от этого не ломается."""
    monday, sunday = week_bounds(date(2026, 12, 31))

    assert monday == date(2026, 12, 28)
    assert sunday == date(2027, 1, 3)
