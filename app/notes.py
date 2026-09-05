"""Заметки к парам: когда спросить про домашку и когда о ней напомнить.

Модуль чистый: ни Telegram, ни базы, ни сети, ни `clock.now()` внутри. На вход —
занятие, дата и (там, где нужно) настройка времени; на выход — дата или момент
времени с поясом. Именно поэтому всё это можно проиграть в тестах на любой день
семестра, не трогая системные часы.

Три момента времени, вокруг которых крутится этап:

* `prompt_at` — через 10 минут после конца пары бот спрашивает «есть что записать?»;
* `day_before_reminder_at` — за 24 часа до следующего занятия по тому же предмету;
* `morning_reminder_at` — утренний повтор в день самого занятия.

«Срок» заметки — это дата **следующего** занятия с тем же `lesson.id`. Для пары
с `parity="both"` это через неделю, для «числителя»/«знаменателя» — через две.
Считаем не арифметикой, а перебором дат через `week_parity`: так правило чётности
живёт в одном месте (`app/schedule.py`) и не разъезжается.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.clock import MOSCOW
from app.schedule import BOTH, Lesson, week_parity

# Через сколько минут после конца пары спрашивать про домашку. Сразу после звонка
# человек ещё собирает вещи, а через полчаса уже забудет, что задавали.
PROMPT_DELAY_MINUTES = 10

# За сколько до следующего занятия приходит первое напоминание.
DAY_BEFORE_HOURS = 24

# Насколько заранее имеет смысл утреннее напоминание. Если до пары меньше часа,
# повторять уже незачем: вчерашнее напоминание было совсем недавно, а сделать
# домашку за сорок минут до пары всё равно не выйдет — получится только давление.
MORNING_MIN_LEAD = timedelta(hours=1)

# На сколько дней вперёд ищем следующее занятие. Конца семестра в данных нет
# (см. «Что сознательно не делаем» в docs/PLAN.md), поэтому просто ограничиваем
# горизонт: не нашли за два месяца — считаем, что срока у заметки нет.
DEFAULT_HORIZON_DAYS = 60


def lesson_starts_at(lesson: Lesson, day: date) -> datetime:
    """Начало занятия в конкретный день как момент времени по Москве."""
    return datetime.combine(day, lesson.start, tzinfo=MOSCOW)


def lesson_ends_at(lesson: Lesson, day: date) -> datetime:
    """Конец занятия в конкретный день как момент времени по Москве."""
    return datetime.combine(day, lesson.end, tzinfo=MOSCOW)


def occurs_on(lesson: Lesson, day: date) -> bool:
    """Идёт ли это занятие в указанный день — с учётом дня недели и чётности."""
    return lesson.weekday == day.isoweekday() and lesson.parity in (
        BOTH,
        week_parity(day),
    )


def next_lesson_occurrence(
    lesson: Lesson, after: date, horizon_days: int = DEFAULT_HORIZON_DAYS
) -> date | None:
    """Дата следующего такого же занятия строго после `after`.

    Для `parity="both"` это ровно через неделю, для «числителя»/«знаменателя» — через
    две. None означает «в пределах горизонта такого занятия больше нет»: это не ошибка,
    просто у заметки не будет срока и напоминаний, в списке она всё равно останется.
    """
    for offset in range(1, max(horizon_days, 0) + 1):
        day = after + timedelta(days=offset)
        if occurs_on(lesson, day):
            return day
    return None


def prompt_at(lesson: Lesson, lesson_date: date) -> datetime:
    """Момент вопроса «есть что записать?» — конец пары плюс небольшая пауза."""
    return lesson_ends_at(lesson, lesson_date) + timedelta(minutes=PROMPT_DELAY_MINUTES)


def day_before_reminder_at(next_occurrence: date, lesson: Lesson) -> datetime:
    """За сутки до начала следующего занятия."""
    return lesson_starts_at(lesson, next_occurrence) - timedelta(hours=DAY_BEFORE_HOURS)


def morning_reminder_at(
    next_occurrence: date, lesson: Lesson, morning_time: time
) -> datetime | None:
    """Утренний повтор в день занятия — или None, если повторять уже поздно.

    None возвращается, когда до начала пары остаётся меньше часа: напоминание за сутки
    в этом случае прозвучало почти только что, и второе было бы просто давлением.
    """
    moment = datetime.combine(next_occurrence, morning_time, tzinfo=MOSCOW)
    if lesson_starts_at(lesson, next_occurrence) - moment < MORNING_MIN_LEAD:
        return None
    return moment


def week_bounds(day: date) -> tuple[date, date]:
    """Понедельник и воскресенье недели, в которую попала дата.

    Нужны фильтру «За неделю» в /notes: неделя у группы всегда считается от понедельника,
    как и чётность.
    """
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


__all__ = [
    "DAY_BEFORE_HOURS",
    "DEFAULT_HORIZON_DAYS",
    "MORNING_MIN_LEAD",
    "PROMPT_DELAY_MINUTES",
    "day_before_reminder_at",
    "lesson_ends_at",
    "lesson_starts_at",
    "morning_reminder_at",
    "next_lesson_occurrence",
    "occurs_on",
    "prompt_at",
    "week_bounds",
]
