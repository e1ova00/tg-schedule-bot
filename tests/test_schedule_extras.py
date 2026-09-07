"""Тесты того, что добавилось в `app/schedule.py` сверх плана: разбор даты, неделя,
список преподавателей и обзор недели.

Ни Telegram, ни сети: все функции здесь чистые — на вход дата (и, при желании, свой
список занятий), на выход строка или обычные объекты Python.

Источник истины для ожиданий тот же, что и в `tests/test_schedule.py`: `CLAUDE.md`
(чётность недели, пустые дни, дистанционный понедельник) и реальный файл
`data/schedule_4md4.json`. Фамилии преподавателей не выдумываем — сверяем с файлом.
"""

from __future__ import annotations

import json
import re
from datetime import date, time, timedelta

import pytest

from app import texts
from app.handlers.preview import parse_preview_date
from app.schedule import (
    SCHEDULE_PATH,
    WEEK_DAYS_SHOWN,
    WEEK_NAV_LIMIT,
    Lesson,
    clear_cache,
    format_week,
    format_week_day,
    load_lessons,
    lessons_on,
    monday_of,
    parse_iso_date,
    teachers,
    week_in_range,
)

# Календарь тот же, что и в остальных тестах: 31.08–06.09.2026 — числитель,
# 07.09–13.09.2026 — знаменатель.
MON_ODD = date(2026, 8, 31)
TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
THU_ODD = date(2026, 9, 3)
FRI_ODD = date(2026, 9, 4)
SAT_ODD = date(2026, 9, 5)
SUN_ODD = date(2026, 9, 6)

MON_EVEN = date(2026, 9, 7)
TUE_EVEN = date(2026, 9, 8)
THU_EVEN = date(2026, 9, 10)
FRI_EVEN = date(2026, 9, 11)
SAT_EVEN = date(2026, 9, 12)
SUN_EVEN = date(2026, 9, 13)

# Все десять фамилий из data/schedule_4md4.json, по алфавиту. Список зашит намеренно:
# он уходит индексами в callback_data кнопок /teachernote, и его молчаливое изменение
# привязало бы старые заметки не к тому человеку.
EXPECTED_TEACHERS = (
    "Агеева Е.А.",
    "Бельченко А.Г.",
    "Волков А.И.",
    "Зверев В.В.",
    "Кокорин Е.С.",
    "Маньков В.Д.",
    "Николаева Л.Г.",
    "Прохорова А.А.",
    "Савенкова П.В.",
    "Шевякова А.Р.",
)


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    """Кэш `load_lessons` глобальный, поэтому чистим его до и после каждого теста."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def real_lessons() -> tuple[Lesson, ...]:
    return load_lessons()


def make_lesson(**overrides: object) -> Lesson:
    """Синтетическое занятие — для случаев, которых нет в реальных данных."""
    defaults: dict[str, object] = {
        "id": "T1",
        "weekday": 2,
        "start": time(11, 40),
        "end": time(13, 5),
        "parity": "both",
        "subject": "Тестовый предмет",
        "kind": "Пр",
        "teacher": "Тестов Т.Т.",
        "building": "пр. Вознесенский, д. 46",
        "room": "В 100",
        "is_remote": False,
    }
    defaults.update(overrides)
    return Lesson(**defaults)  # type: ignore[arg-type]


# ======================================================================================
# 1. parse_iso_date — общий разбор даты для /day, /week, /preview и /addnote
# ======================================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("2026-09-15", date(2026, 9, 15), id="обычная дата"),
        pytest.param("  2026-09-15  ", date(2026, 9, 15), id="с пробелами по краям"),
        pytest.param("2026-01-01", date(2026, 1, 1), id="первое января"),
        pytest.param("2026-12-31", date(2026, 12, 31), id="последний день года"),
        pytest.param("2024-02-29", date(2024, 2, 29), id="29 февраля високосного года"),
    ],
)
def test_parse_iso_date_reads_correct_dates(raw: str, expected: date) -> None:
    assert parse_iso_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="аргумента нет вообще"),
        pytest.param("", id="пустая строка"),
        pytest.param("   ", id="одни пробелы"),
    ],
)
def test_parse_iso_date_treats_empty_as_no_date(raw: str | None) -> None:
    """Пусто — это не ошибка формата, а «даты не назвали».

    Что показывать без даты, решает сам хендлер: /day — сегодня, /preview — завтра,
    /week — текущую неделю. Поэтому здесь просто None.
    """
    assert parse_iso_date(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("завтра", id="слово вместо даты"),
        pytest.param("15.09.2026", id="точки вместо дефисов"),
        pytest.param("2026/09/15", id="слэши вместо дефисов"),
        pytest.param("15-09-2026", id="день впереди года"),
        pytest.param("2026-13-01", id="тринадцатого месяца не бывает"),
        pytest.param("2026-09-31", id="в сентябре 30 дней"),
        pytest.param("2026-9-5", id="без ведущих нулей"),
        pytest.param("0", id="просто ноль"),
        pytest.param("<b>", id="html вместо даты"),
    ],
)
def test_parse_iso_date_rejects_garbage(raw: str) -> None:
    assert parse_iso_date(raw) is None


@pytest.mark.parametrize("year", [2025, 2026, 2027, 2100])
def test_parse_iso_date_rejects_29_february_in_common_year(year: int) -> None:
    """29 февраля в невисокосном году — самая безобидная на вид кривая дата."""
    assert parse_iso_date(f"{year}-02-29") is None


def test_parse_iso_date_accepts_29_february_in_leap_year() -> None:
    assert parse_iso_date("2028-02-29") == date(2028, 2, 29)


def test_parse_iso_date_is_as_tolerant_as_python_itself() -> None:
    """Фиксируем то, что есть: `date.fromisoformat` в Python 3.11+ понимает и слитную
    запись, и «номер недели».

    Человеку мы такое не предлагаем и в подсказках не показываем, но и ошибкой это не
    считаем — лишний повод ответить «не понял дату» тут не нужен.
    """
    assert parse_iso_date("20260915") == date(2026, 9, 15)
    assert parse_iso_date("2026-W38-2") == date(2026, 9, 15)


def test_day_and_preview_share_one_date_parser() -> None:
    """/day и /preview разбирают дату одинаково — расходиться формату негде.

    Отличие ровно одно и намеренное: пустой аргумент. У /preview это «завтра»,
    у /day — «сегодня», и решает это хендлер, а не разбор.
    """
    for raw in ("2026-09-15", " 2026-09-15 ", "завтра", "15.09.2026", "2026-02-29", "0"):
        assert parse_preview_date(raw, TUE_ODD) == parse_iso_date(raw), raw

    assert parse_preview_date("", TUE_ODD) == WED_ODD
    assert parse_iso_date("") is None


def test_bad_date_texts_show_the_right_command() -> None:
    """У каждой команды свой пример в подсказке — иначе человек скопирует чужой."""
    assert "/day" in texts.DAY_BAD_DATE
    assert "/week" in texts.WEEK_BAD_DATE
    assert "/preview" in texts.PREVIEW_BAD_DATE
    for template in (texts.DAY_BAD_DATE, texts.WEEK_BAD_DATE, texts.PREVIEW_BAD_DATE):
        assert "ГГГГ-ММ-ДД" in template
        assert "{value}" in template


# ======================================================================================
# 2. monday_of — начало недели
# ======================================================================================


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        pytest.param(MON_ODD, MON_ODD, id="понедельник — сам себе начало недели"),
        pytest.param(TUE_ODD, MON_ODD, id="вторник"),
        pytest.param(THU_ODD, MON_ODD, id="четверг"),
        pytest.param(SAT_ODD, MON_ODD, id="суббота"),
        pytest.param(SUN_ODD, MON_ODD, id="воскресенье — ещё та же неделя"),
        pytest.param(MON_EVEN, MON_EVEN, id="следующий понедельник — уже другая"),
    ],
)
def test_monday_of_finds_start_of_week(day: date, expected: date) -> None:
    assert monday_of(day) == expected


def test_monday_of_is_idempotent() -> None:
    """Повторное применение ничего не сдвигает — на это опирается format_week."""
    for shift in range(14):
        day = MON_ODD + timedelta(days=shift)
        assert monday_of(monday_of(day)) == monday_of(day)


def test_monday_of_always_returns_a_monday() -> None:
    for shift in range(400):
        assert monday_of(MON_ODD + timedelta(days=shift)).weekday() == 0


def test_monday_of_survives_new_year() -> None:
    """31.12.2026 (чт) и 01.01.2027 (пт) — одна и та же неделя, а не две разные."""
    assert monday_of(date(2026, 12, 31)) == date(2026, 12, 28)
    assert monday_of(date(2027, 1, 1)) == date(2026, 12, 28)
    assert monday_of(date(2027, 1, 3)) == date(2026, 12, 28)  # воскресенье
    assert monday_of(date(2027, 1, 4)) == date(2027, 1, 4)  # новый понедельник


# ======================================================================================
# 3. week_in_range — докуда разрешено листать /week
# ======================================================================================


def test_week_nav_limit_is_a_year() -> None:
    assert WEEK_NAV_LIMIT == 52


@pytest.mark.parametrize("weeks", [0, 1, -1, 10, -10, 51, -51, 52, -52])
def test_week_in_range_allows_a_year_in_both_directions(weeks: int) -> None:
    """Ровно 52 недели вперёд и назад — ещё можно."""
    monday = monday_of(MON_EVEN) + timedelta(weeks=weeks)

    assert week_in_range(monday, MON_EVEN) is True


@pytest.mark.parametrize("weeks", [53, -53, 60, -60, 200, -200])
def test_week_in_range_rejects_further_than_a_year(weeks: int) -> None:
    monday = monday_of(MON_EVEN) + timedelta(weeks=weeks)

    assert week_in_range(monday, MON_EVEN) is False


def test_week_in_range_counts_from_mondays_not_from_exact_dates() -> None:
    """Оба аргумента приводятся к понедельнику: «52 недели и три дня» — та же неделя."""
    far_monday = monday_of(MON_EVEN) + timedelta(weeks=52)

    assert week_in_range(far_monday + timedelta(days=3), MON_EVEN) is True
    assert week_in_range(far_monday + timedelta(days=7), MON_EVEN) is False
    # «Сегодня» тоже может быть любым днём недели.
    assert week_in_range(far_monday, SUN_EVEN) is True


def test_week_in_range_respects_custom_limit() -> None:
    assert week_in_range(MON_EVEN + timedelta(weeks=2), MON_EVEN, limit_weeks=2) is True
    assert week_in_range(MON_EVEN + timedelta(weeks=3), MON_EVEN, limit_weeks=2) is False


def test_week_in_range_is_a_flag_not_an_exception() -> None:
    """За краем диапазона функция возвращает False, а не падает и не отдаёт None.

    Хендлеру /week этого достаточно: он либо не рисует кнопку, либо показывает алерт.
    """
    result = week_in_range(MON_EVEN + timedelta(weeks=500), MON_EVEN)

    assert result is False
    assert isinstance(result, bool)


# ======================================================================================
# 4. teachers() — список для кнопок /teachernote
# ======================================================================================


def test_teachers_has_exactly_ten_names(real_lessons: tuple[Lesson, ...]) -> None:
    names = teachers(real_lessons)

    assert len(names) == 10
    assert len(set(names)) == 10, "в списке не должно быть повторов"


def test_teachers_matches_the_schedule_file() -> None:
    """Сверка с сырым JSON: список берётся из данных, а не откуда-то ещё."""
    payload = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
    from_file = {lesson["teacher"] for lesson in payload["lessons"]}

    assert set(teachers()) == from_file
    assert teachers() == EXPECTED_TEACHERS


def test_teachers_are_sorted_alphabetically() -> None:
    names = teachers()

    assert list(names) == sorted(names)
    assert names[0] == "Агеева Е.А."
    assert names[-1] == "Шевякова А.Р."


def test_teachers_returns_a_stable_tuple() -> None:
    """Индекс в этом кортеже уходит в callback_data — порядок обязан быть одинаковым."""
    assert isinstance(teachers(), tuple)
    assert teachers() == teachers()


def test_teachers_uses_real_file_by_default(real_lessons: tuple[Lesson, ...]) -> None:
    assert teachers() == teachers(real_lessons)


def test_teachers_deduplicates_and_skips_blank_names() -> None:
    """Один человек ведёт несколько пар — в списке он один раз, пустых строк нет."""
    lessons = [
        make_lesson(id="A", teacher="Зверев В.В."),
        make_lesson(id="B", teacher="Зверев В.В."),
        make_lesson(id="C", teacher="Агеева Е.А."),
        make_lesson(id="D", teacher="   "),
        make_lesson(id="E", teacher=""),
    ]

    assert teachers(lessons) == ("Агеева Е.А.", "Зверев В.В.")


def test_every_lesson_has_a_teacher_from_the_list(
    real_lessons: tuple[Lesson, ...],
) -> None:
    """Обратная сторона: ни одна пара не осталась без человека из списка."""
    names = set(teachers(real_lessons))

    for lesson in real_lessons:
        assert lesson.teacher in names


# ======================================================================================
# 5. format_week_day — один день внутри обзора недели
# ======================================================================================


def test_format_week_day_lists_lessons_shortly() -> None:
    """В обзоре недели у пары только время начала, предмет, вид и место."""
    lessons = [
        make_lesson(id="A", start=time(10, 5), subject="Web-дизайн", room="С 407"),
        make_lesson(id="B", start=time(11, 40), subject="Прикладной дизайн", room="В 484"),
    ]

    text = format_week_day(TUE_ODD, lessons)

    assert "<b>Вторник</b>, 1 сентября" in text
    assert "10:05 — Web-дизайн (Пр), ауд. С 407" in text
    assert "11:40 — Прикладной дизайн (Пр), ауд. В 484" in text
    # Время окончания в обзор недели не попадает — за подробностями человек идёт в /day.
    assert "13:05" not in text


def test_format_week_day_marks_remote_lessons() -> None:
    lessons = [make_lesson(is_remote=True, building="Дистанционное обучение")]

    text = format_week_day(MON_ODD, lessons)

    assert texts.LESSON_REMOTE in text
    assert "ауд." not in text
    assert "Дистанционное обучение" not in text


def test_format_week_day_says_no_lessons() -> None:
    text = format_week_day(THU_ODD, [])

    assert "<b>Четверг</b>, 3 сентября" in text
    assert texts.WEEK_DAY_EMPTY in text


def test_format_week_day_marks_today_only_when_it_is_today() -> None:
    marked = format_week_day(WED_ODD, [], today=WED_ODD)
    plain = format_week_day(WED_ODD, [], today=THU_ODD)

    assert "сегодня" in marked
    assert "сегодня" not in plain
    assert "сегодня" not in format_week_day(WED_ODD, [])


def test_format_week_day_escapes_html() -> None:
    """Сообщения уходят с parse_mode=HTML: «<A & B>» в данных не должно ломать разметку."""
    lessons = [make_lesson(subject="Алгоритмы <A & B>", room="<C>")]

    text = format_week_day(TUE_ODD, lessons)

    assert "&lt;A &amp; B&gt;" in text
    assert "Алгоритмы <A" not in text
    assert "&lt;C&gt;" in text


# ======================================================================================
# 6. format_week — вся неделя одним сообщением
# ======================================================================================


def test_week_shows_six_days_from_monday_to_saturday() -> None:
    """Воскресенья в расписании нет вообще — строка «воскресенье: пар нет» только шумит."""
    assert WEEK_DAYS_SHOWN == 6

    text = format_week(MON_ODD)

    for weekday in texts.WEEKDAYS_RU[:6]:
        assert weekday.capitalize() in text
    assert "Воскресенье" not in text
    assert "6 сентября" not in text


def test_week_header_shows_range_and_parity() -> None:
    odd = format_week(MON_ODD)
    even = format_week(MON_EVEN)

    assert "Неделя 31 августа — 5 сентября" in odd
    assert "числитель" in odd
    assert "Неделя 7 сентября — 12 сентября" in even
    assert "знаменатель" in even


def test_week_starts_from_monday_whatever_date_is_given() -> None:
    """Хендлер может передать любую дату — неделя от этого не «съедет»."""
    expected = format_week(MON_ODD)

    for shift in range(7):
        assert format_week(MON_ODD + timedelta(days=shift)) == expected


def test_odd_week_has_an_empty_thursday() -> None:
    """CLAUDE.md, правило 2: по числителю четверг пустой — и в обзоре недели тоже."""
    text = format_week(MON_ODD)

    thursday = block_for(text, "Четверг")
    assert texts.WEEK_DAY_EMPTY in thursday
    # А остальные дни числителя пустыми не стали.
    for weekday in ("Понедельник", "Вторник", "Среда", "Пятница", "Суббота"):
        assert texts.WEEK_DAY_EMPTY not in block_for(text, weekday), weekday


def test_even_week_has_empty_friday_and_saturday() -> None:
    """CLAUDE.md, правило 2: по знаменателю пустые пятница и суббота."""
    text = format_week(MON_EVEN)

    assert texts.WEEK_DAY_EMPTY in block_for(text, "Пятница")
    assert texts.WEEK_DAY_EMPTY in block_for(text, "Суббота")
    for weekday in ("Понедельник", "Вторник", "Среда", "Четверг"):
        assert texts.WEEK_DAY_EMPTY not in block_for(text, weekday), weekday


def test_week_counts_lessons_by_parity(real_lessons: tuple[Lesson, ...]) -> None:
    """Число строк с парами в обзоре совпадает с числом пар этой недели."""
    for monday in (MON_ODD, MON_EVEN):
        text = format_week(monday)
        expected = sum(
            len(lessons_on(monday + timedelta(days=shift), real_lessons))
            for shift in range(WEEK_DAYS_SHOWN)
        )
        shown = len(re.findall(r"^\d\d:\d\d — ", text, flags=re.MULTILINE))

        assert shown == expected, monday


def test_week_marks_monday_lessons_as_remote(real_lessons: tuple[Lesson, ...]) -> None:
    """Понедельник у группы полностью дистанционный — это должно быть видно в обзоре."""
    text = format_week(MON_ODD)

    monday = block_for(text, "Понедельник")
    assert monday.count(texts.LESSON_REMOTE) == len(lessons_on(MON_ODD, real_lessons))
    assert "ауд." not in monday


def test_week_marks_today_once() -> None:
    text = format_week(MON_ODD, today=WED_ODD)

    assert text.count("— сегодня") == 1
    assert "<b>Среда</b>, 2 сентября — сегодня" in text


def test_week_of_another_period_has_no_today_mark() -> None:
    """Листаем на месяц вперёд — пометки «сегодня» в этой неделе быть не должно."""
    text = format_week(MON_EVEN + timedelta(weeks=4), today=WED_ODD)

    assert "сегодня" not in text


def test_week_without_today_has_no_mark() -> None:
    assert "сегодня" not in format_week(MON_ODD)


def test_week_ends_with_a_hint_about_the_day_command() -> None:
    text = format_week(MON_ODD)

    assert text.rstrip().endswith(texts.WEEK_FOOTER)
    assert "/day" in text


def test_week_accepts_custom_lessons_and_escapes_them() -> None:
    """Свой список занятий — и текст пользователя всё так же экранируется."""
    lessons = [make_lesson(weekday=3, subject="Web <script>", parity="both")]

    text = format_week(MON_ODD, lessons=lessons)

    assert "Web &lt;script&gt;" in text
    assert "<script>" not in text


def test_week_works_for_a_whole_year_without_crashing() -> None:
    """Листать разрешено ±52 недели — ни одна из них не должна ронять форматирование."""
    for weeks in range(-WEEK_NAV_LIMIT, WEEK_NAV_LIMIT + 1):
        monday = MON_ODD + timedelta(weeks=weeks)
        text = format_week(monday, today=MON_ODD)

        assert text.strip()
        assert texts.WEEK_FOOTER in text


def block_for(week_text: str, weekday: str) -> str:
    """Кусок обзора недели, относящийся к одному дню."""
    blocks = [part for part in week_text.split("\n\n") if part.startswith(f"<b>{weekday}</b>")]
    assert len(blocks) == 1, f"день «{weekday}» встречается {len(blocks)} раз"
    return blocks[0]
