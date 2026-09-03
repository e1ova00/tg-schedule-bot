"""Тесты бизнес-логики расписания: чётность недели, занятия дня, первая очная пара.

Ни Telegram, ни сеть здесь не нужны: `app.schedule` работает с датой и списком занятий.

Источник истины для ожиданий — `CLAUDE.md` (формула чётности, контрольные даты, правила
про пустые дни и понедельник) и реальный файл `data/schedule_4md4.json`.
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from pathlib import Path

import pytest

from app import schedule
from app.schedule import (
    ANCHOR,
    Lesson,
    ScheduleError,
    clear_cache,
    first_offline_lesson,
    format_date,
    format_day,
    format_lesson,
    has_offline_lessons,
    lessons_on,
    load_lessons,
    parity_word,
    parse_lesson,
    parse_schedule,
    week_parity,
)

# --------------------------------------------------------------------------------------
# Календарь для тестов. Неделя 31.08–06.09.2026 — числитель, 07.09–13.09.2026 — знаменатель.
# --------------------------------------------------------------------------------------

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
    """Кэш `load_lessons` глобальный, поэтому чистим его до и после каждого теста."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def real_lessons() -> tuple[Lesson, ...]:
    """Настоящее расписание группы из `data/schedule_4md4.json`."""
    return load_lessons()


def make_lesson(**overrides: object) -> Lesson:
    """Синтетическое занятие: удобно проверять случаи, которых нет в реальных данных."""
    defaults: dict[str, object] = {
        "id": "T1",
        "weekday": 2,
        "start": time(11, 40),
        "end": time(13, 5),
        "parity": "both",
        "subject": "Тестовый предмет",
        "kind": "Пр",
        "teacher": "Тестов Т.Т.",
        "building": VOZNESENSKY,
        "room": "В 100",
        "is_remote": False,
    }
    defaults.update(overrides)
    return Lesson(**defaults)  # type: ignore[arg-type]


def raw_lesson(**overrides: object) -> dict[str, object]:
    """Корректный словарь занятия «как в JSON» — тесты валидации его портят по одному полю."""
    raw: dict[str, object] = {
        "id": "L1216",
        "weekday": 2,
        "start": "11:40",
        "end": "13:05",
        "parity": "odd",
        "subject": "Безопасность жизнедеятельности",
        "kind": "Лаб",
        "teacher": "Бельченко А.Г.",
        "building": VOZNESENSKY,
        "room": "В 570",
        "is_remote": False,
    }
    raw.update(overrides)
    return raw


# ======================================================================================
# 1. Чётность недели
# ======================================================================================


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        pytest.param(date(2026, 9, 1), "odd", id="2026-09-01 вт — числитель"),
        pytest.param(date(2026, 9, 7), "even", id="2026-09-07 пн — знаменатель"),
        pytest.param(date(2026, 9, 14), "odd", id="2026-09-14 пн — числитель"),
        pytest.param(date(2026, 9, 21), "even", id="2026-09-21 пн — знаменатель"),
        pytest.param(date(2026, 10, 1), "odd", id="2026-10-01 чт — числитель"),
    ],
)
def test_week_parity_control_dates(day: date, expected: str) -> None:
    """Контрольная таблица из CLAUDE.md — самая важная проверка этапа."""
    assert week_parity(day) == expected


def test_week_parity_anchor_week_is_odd() -> None:
    """Неделя, в которую попало 1 сентября 2026, — числитель, её понедельник — якорь."""
    assert ANCHOR == date(2026, 8, 31)
    assert week_parity(ANCHOR) == "odd"


@pytest.mark.parametrize("shift", range(7))
def test_week_parity_is_the_same_all_week(shift: int) -> None:
    """Внутри одной календарной недели (пн–вс) чётность не меняется."""
    assert week_parity(MON_ODD + timedelta(days=shift)) == "odd"
    assert week_parity(MON_EVEN + timedelta(days=shift)) == "even"


def test_week_parity_switches_on_monday_not_on_sunday() -> None:
    """Граница недели — понедельник: воскресенье ещё относится к прошедшей неделе."""
    assert week_parity(SAT_ODD) == "odd"
    assert week_parity(SUN_ODD) == "odd"  # воскресенье 06.09 — ещё числитель
    assert week_parity(MON_EVEN) == "even"  # понедельник 07.09 — уже знаменатель


def test_week_parity_alternates_every_monday() -> None:
    """Десять недель подряд чередуются без сбоев."""
    parities = [week_parity(ANCHOR + timedelta(weeks=n)) for n in range(10)]

    assert parities == ["odd", "even"] * 5


def test_week_parity_before_anchor_counts_backwards() -> None:
    """Даты раньше якоря тоже считаются корректно (целочисленное деление вниз)."""
    assert week_parity(date(2026, 8, 24)) == "even"  # понедельник за неделю до якоря
    assert week_parity(date(2026, 8, 30)) == "even"  # воскресенье той же недели
    assert week_parity(date(2026, 8, 17)) == "odd"


def test_week_parity_survives_new_year() -> None:
    """Переход через Новый год: неделя не «перезапускается» 1 января.

    31.12.2026 (чт) и 01.01.2027 (пт) — одна и та же календарная неделя, значит одна
    чётность. Смена происходит только в ближайший понедельник, 04.01.2027.
    """
    assert week_parity(date(2026, 12, 28)) == "even"  # понедельник той недели
    assert week_parity(date(2026, 12, 31)) == "even"
    assert week_parity(date(2027, 1, 1)) == "even"
    assert week_parity(date(2027, 1, 3)) == "even"  # воскресенье — всё ещё та же неделя
    assert week_parity(date(2027, 1, 4)) == "odd"  # понедельник — чётность перевернулась


def test_week_parity_returns_only_known_values() -> None:
    """За полтора года не должно появиться никакого третьего значения."""
    values = {week_parity(ANCHOR + timedelta(days=n)) for n in range(500)}

    assert values == {"odd", "even"}


# ======================================================================================
# 2. Занятия дня: пустые дни, понедельник, сортировка
# ======================================================================================


def test_lessons_on_thursday_odd_is_empty(real_lessons: tuple[Lesson, ...]) -> None:
    """По числителю четверг у группы пустой (CLAUDE.md, правило 2)."""
    assert lessons_on(THU_ODD, real_lessons) == []
    assert lessons_on(date(2026, 10, 1), real_lessons) == []  # контрольная дата из таблицы


def test_lessons_on_friday_and_saturday_even_are_empty(
    real_lessons: tuple[Lesson, ...],
) -> None:
    """По знаменателю пустые пятница и суббота (CLAUDE.md, правило 2)."""
    assert lessons_on(FRI_EVEN, real_lessons) == []
    assert lessons_on(SAT_EVEN, real_lessons) == []


def test_empty_days_are_not_empty_on_the_other_parity(
    real_lessons: tuple[Lesson, ...],
) -> None:
    """Обратная сторона правила: те же дни на другой чётности пары есть.

    Иначе тест на «пусто» прошёл бы и при полностью сломанной выборке.
    """
    assert len(lessons_on(THU_EVEN, real_lessons)) == 3
    assert len(lessons_on(FRI_ODD, real_lessons)) == 3
    assert len(lessons_on(SAT_ODD, real_lessons)) == 1


def test_lessons_on_sunday_is_empty(real_lessons: tuple[Lesson, ...]) -> None:
    """Воскресенья в расписании нет вообще — не должно быть и ошибки."""
    assert lessons_on(SUN_ODD, real_lessons) == []
    assert lessons_on(date(2026, 9, 13), real_lessons) == []


def test_wednesday_even_has_four_lessons(real_lessons: tuple[Lesson, ...]) -> None:
    """Критерий приёмки этапа: в среду по знаменателю — 4 пары."""
    day = lessons_on(WED_EVEN, real_lessons)

    assert [lesson.id for lesson in day] == ["L1221", "L1222", "L1223", "L1224"]
    assert len(day) == 4


def test_wednesday_odd_has_three_lessons(real_lessons: tuple[Lesson, ...]) -> None:
    """По числителю в среду 3 пары: четвёртая (БЖД) стоит только по знаменателю."""
    day = lessons_on(WED_ODD, real_lessons)

    assert [lesson.id for lesson in day] == ["L1221", "L1222", "L1223"]
    assert all(lesson.parity == "both" for lesson in day)


def test_lessons_on_is_sorted_by_start_time(real_lessons: tuple[Lesson, ...]) -> None:
    """Пары всегда идут по возрастанию времени начала, в какой бы день ни спросили."""
    for day in (MON_ODD, TUE_ODD, WED_ODD, THU_EVEN, FRI_ODD, WED_EVEN, TUE_EVEN):
        starts = [lesson.start for lesson in lessons_on(day, real_lessons)]
        assert starts == sorted(starts), f"порядок пар нарушен для {day}"


def test_lessons_on_sorts_unsorted_input() -> None:
    """Сортировка не полагается на порядок строк в JSON."""
    late = make_lesson(id="B", start=time(15, 20), end=time(16, 45))
    early = make_lesson(id="A", start=time(10, 5), end=time(11, 30))

    day = lessons_on(TUE_ODD, [late, early])

    assert [lesson.id for lesson in day] == ["A", "B"]


def test_lessons_on_keeps_only_matching_weekday_and_parity(
    real_lessons: tuple[Lesson, ...],
) -> None:
    """Формальная проверка фильтра: день недели совпал, чётность — «both» или дневная."""
    for day in (MON_ODD, TUE_ODD, WED_EVEN, THU_EVEN, FRI_ODD, SAT_ODD):
        parity = week_parity(day)
        for lesson in lessons_on(day, real_lessons):
            assert lesson.weekday == day.isoweekday()
            assert lesson.parity in ("both", parity)


def test_lessons_on_uses_real_file_by_default() -> None:
    """Без явного списка занятий функция берёт `data/schedule_4md4.json`."""
    assert lessons_on(WED_EVEN) == lessons_on(WED_EVEN, load_lessons())


# ======================================================================================
# 3. Понедельник: пары есть, но все дистанционные — будильник не нужен
# ======================================================================================


@pytest.mark.parametrize(
    "monday",
    [
        pytest.param(MON_ODD, id="понедельник по числителю"),
        pytest.param(MON_EVEN, id="понедельник по знаменателю"),
        pytest.param(date(2026, 9, 14), id="ещё один числитель"),
        pytest.param(date(2026, 9, 21), id="ещё один знаменатель"),
    ],
)
def test_monday_is_fully_remote(monday: date, real_lessons: tuple[Lesson, ...]) -> None:
    """CLAUDE.md, правило 2: понедельник всегда полностью дистанционный.

    Пары в понедельник есть (их надо показывать в /today), но очных среди них нет,
    поэтому будильник не считается никогда.
    """
    day = lessons_on(monday, real_lessons)

    assert day, "в понедельник дистанционные пары всё-таки есть, список не должен быть пустым"
    assert all(lesson.is_remote for lesson in day)
    assert first_offline_lesson(monday, real_lessons) is None
    assert has_offline_lessons(monday, real_lessons) is False


def test_all_mondays_in_data_are_remote(real_lessons: tuple[Lesson, ...]) -> None:
    """Страховка на случай обновления файла расписания деканатом."""
    mondays = [lesson for lesson in real_lessons if lesson.weekday == 1]

    assert mondays
    assert all(lesson.is_remote for lesson in mondays)


def test_empty_day_has_no_offline_lesson(real_lessons: tuple[Lesson, ...]) -> None:
    """Пустой день — тоже «будильник не нужен», а не ошибка."""
    assert first_offline_lesson(THU_ODD, real_lessons) is None
    assert has_offline_lessons(FRI_EVEN, real_lessons) is False
    assert has_offline_lessons(SAT_EVEN, real_lessons) is False


# ======================================================================================
# 4. Первая очная пара и выбор корпуса
# ======================================================================================


def test_tuesday_odd_goes_to_voznesensky(real_lessons: tuple[Lesson, ...]) -> None:
    """CLAUDE.md, правило 3: во вторник по числителю первая пара на Вознесенском."""
    lesson = first_offline_lesson(TUE_ODD, real_lessons)

    assert lesson is not None
    assert lesson.building == VOZNESENSKY
    assert lesson.start == time(11, 40)
    assert lesson.id == "L1216"


def test_tuesday_even_goes_to_sadovaya(real_lessons: tuple[Lesson, ...]) -> None:
    """А по знаменателю — на Садовую. Адрес назначения зависит от чётности."""
    lesson = first_offline_lesson(TUE_EVEN, real_lessons)

    assert lesson is not None
    assert lesson.building == SADOVAYA
    assert lesson.start == time(11, 40)
    assert lesson.id == "L1217"


def test_tuesday_destination_differs_between_parities(
    real_lessons: tuple[Lesson, ...],
) -> None:
    """Явно фиксируем: один и тот же вторник, разные недели — разные корпуса."""
    odd = first_offline_lesson(TUE_ODD, real_lessons)
    even = first_offline_lesson(TUE_EVEN, real_lessons)

    assert odd is not None and even is not None
    assert odd.building != even.building


def test_friday_odd_goes_to_bolshaya_morskaya(real_lessons: tuple[Lesson, ...]) -> None:
    """Третий корпус тоже используется: пятница по числителю — Большая Морская."""
    lesson = first_offline_lesson(FRI_ODD, real_lessons)

    assert lesson is not None
    assert lesson.building == BOLSHAYA_MORSKAYA


def test_first_offline_lesson_skips_earlier_remote_lesson() -> None:
    """Дистант в 10:05, очная в 13:45 → ехать надо к очной (CLAUDE.md, правило 4).

    В текущем файле расписания такого дня нет (дистант везде либо весь день, либо
    последней парой), поэтому случай проверяем на синтетических данных.
    """
    remote_first = make_lesson(id="R1", start=time(10, 5), end=time(11, 30), is_remote=True)
    remote_second = make_lesson(id="R2", start=time(11, 40), end=time(13, 5), is_remote=True)
    offline_third = make_lesson(
        id="O3", start=time(13, 45), end=time(15, 10), building=SADOVAYA
    )
    late_offline = make_lesson(id="O4", start=time(15, 20), end=time(16, 45))

    day = [late_offline, remote_second, offline_third, remote_first]
    lesson = first_offline_lesson(TUE_ODD, day)

    assert lesson is not None
    assert lesson.id == "O3"
    assert lesson.building == SADOVAYA
    assert has_offline_lessons(TUE_ODD, day) is True


def test_first_offline_lesson_is_none_when_all_remote() -> None:
    day = [
        make_lesson(id="R1", start=time(10, 5), is_remote=True),
        make_lesson(id="R2", start=time(13, 45), is_remote=True),
    ]

    assert first_offline_lesson(TUE_ODD, day) is None
    assert has_offline_lessons(TUE_ODD, day) is False


def test_first_offline_lesson_matches_earliest_offline_every_day(
    real_lessons: tuple[Lesson, ...],
) -> None:
    """Свойство на две недели подряд: результат — самая ранняя очная пара дня."""
    for shift in range(14):
        day = MON_ODD + timedelta(days=shift)
        offline = [lesson for lesson in lessons_on(day, real_lessons) if not lesson.is_remote]
        expected = offline[0] if offline else None

        assert first_offline_lesson(day, real_lessons) == expected


# ======================================================================================
# 5. Разбор данных: понятные ошибки вместо KeyError/ValueError
# ======================================================================================


def test_parse_lesson_reads_correct_data() -> None:
    lesson = parse_lesson(raw_lesson())

    assert lesson.id == "L1216"
    assert lesson.start == time(11, 40)
    assert lesson.end == time(13, 5)
    assert lesson.is_remote is False
    assert lesson.time_range == "11:40–13:05"


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "weekday",
        "start",
        "end",
        "parity",
        "subject",
        "kind",
        "building",
        "room",
        "is_remote",
    ],
)
def test_parse_lesson_reports_missing_field(field: str) -> None:
    """Пропущенное поле — это ScheduleError с русским текстом, а не голый KeyError."""
    raw = raw_lesson()
    del raw[field]

    with pytest.raises(ScheduleError) as exc:
        parse_lesson(raw)

    assert field in str(exc.value)
    assert "нет полей" in str(exc.value)


def test_parse_lesson_lists_all_missing_fields_at_once() -> None:
    raw = raw_lesson()
    del raw["teacher"]
    del raw["room"]

    with pytest.raises(ScheduleError) as exc:
        parse_lesson(raw)

    assert "teacher" in str(exc.value)
    assert "room" in str(exc.value)


@pytest.mark.parametrize(
    "bad_time",
    [
        pytest.param("25:70", id="такого времени не бывает"),
        pytest.param("11-40", id="разделитель не двоеточие"),
        pytest.param("утро", id="вообще не время"),
        pytest.param("", id="пустая строка"),
        pytest.param(1140, id="число вместо строки"),
        pytest.param(None, id="null вместо строки"),
    ],
)
def test_parse_lesson_rejects_broken_time(bad_time: object) -> None:
    with pytest.raises(ScheduleError) as exc:
        parse_lesson(raw_lesson(start=bad_time))

    message = str(exc.value)
    assert "L1216" in message
    assert "start" in message


@pytest.mark.parametrize(
    "bad_weekday",
    [
        pytest.param(0, id="нулевого дня недели нет"),
        pytest.param(8, id="восьмого дня недели нет"),
        pytest.param("2", id="строка вместо числа"),
        pytest.param(True, id="булево значение — не день недели"),
        pytest.param(2.0, id="дробное число"),
    ],
)
def test_parse_lesson_rejects_broken_weekday(bad_weekday: object) -> None:
    with pytest.raises(ScheduleError) as exc:
        parse_lesson(raw_lesson(weekday=bad_weekday))

    assert "weekday" in str(exc.value)


@pytest.mark.parametrize("bad_parity", ["числитель", "ODD", "", None, 1])
def test_parse_lesson_rejects_unknown_parity(bad_parity: object) -> None:
    with pytest.raises(ScheduleError) as exc:
        parse_lesson(raw_lesson(parity=bad_parity))

    message = str(exc.value)
    assert "parity" in message
    assert "odd" in message and "even" in message and "both" in message


def test_parse_lesson_without_id_still_explains_problem() -> None:
    """Даже если id потерян, текст ошибки должен быть читаемым."""
    raw = raw_lesson()
    del raw["id"]
    del raw["subject"]

    with pytest.raises(ScheduleError) as exc:
        parse_lesson(raw)

    assert "без id" in str(exc.value)


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        pytest.param([], "JSON-объектом", id="список вместо объекта"),
        pytest.param("расписание", "JSON-объектом", id="строка вместо объекта"),
        pytest.param({}, "lessons", id="нет ключа lessons"),
        pytest.param({"lessons": []}, "lessons", id="пустой список занятий"),
        pytest.param({"lessons": {}}, "lessons", id="объект вместо списка"),
        pytest.param({"lessons": ["L1"]}, "не является объектом", id="строка в списке"),
    ],
)
def test_parse_schedule_rejects_broken_payload(payload: object, expected_fragment: str) -> None:
    with pytest.raises(ScheduleError) as exc:
        parse_schedule(payload)

    assert expected_fragment in str(exc.value)


def test_parse_schedule_reads_list_of_lessons() -> None:
    lessons = parse_schedule({"lessons": [raw_lesson(), raw_lesson(id="L1217", parity="even")]})

    assert isinstance(lessons, tuple)
    assert [lesson.id for lesson in lessons] == ["L1216", "L1217"]


# ======================================================================================
# 6. Загрузка файла и кэш
# ======================================================================================


def write_schedule(path: Path, lessons: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"lessons": lessons}, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_lessons_reads_own_file(tmp_path: Path) -> None:
    path = write_schedule(tmp_path / "schedule.json", [raw_lesson()])

    lessons = load_lessons(path)

    assert [lesson.id for lesson in lessons] == ["L1216"]


def test_load_lessons_accepts_path_as_string(tmp_path: Path) -> None:
    path = write_schedule(tmp_path / "schedule.json", [raw_lesson()])

    assert load_lessons(str(path)) == load_lessons(path)


def test_load_lessons_caches_by_path(tmp_path: Path) -> None:
    """Второй вызов возвращает тот же объект: файл читается один раз."""
    path = write_schedule(tmp_path / "schedule.json", [raw_lesson()])

    first = load_lessons(path)
    write_schedule(path, [raw_lesson(id="L9999")])
    second = load_lessons(path)

    assert second is first
    assert [lesson.id for lesson in second] == ["L1216"]


def test_load_lessons_without_cache_rereads_file(tmp_path: Path) -> None:
    path = write_schedule(tmp_path / "schedule.json", [raw_lesson()])
    load_lessons(path)

    write_schedule(path, [raw_lesson(id="L9999")])
    fresh = load_lessons(path, use_cache=False)

    assert [lesson.id for lesson in fresh] == ["L9999"]


def test_clear_cache_forces_reread(tmp_path: Path) -> None:
    path = write_schedule(tmp_path / "schedule.json", [raw_lesson()])
    load_lessons(path)

    write_schedule(path, [raw_lesson(id="L9999")])
    clear_cache()

    assert [lesson.id for lesson in load_lessons(path)] == ["L9999"]


def test_load_lessons_keeps_files_apart(tmp_path: Path) -> None:
    """Кэш по пути: тестовый файл не должен подменять боевое расписание."""
    path = write_schedule(tmp_path / "schedule.json", [raw_lesson(id="L9999")])

    custom = load_lessons(path)
    real = load_lessons()

    assert [lesson.id for lesson in custom] == ["L9999"]
    assert len(real) == 21


def test_load_lessons_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ScheduleError) as exc:
        load_lessons(tmp_path / "нет-такого-файла.json")

    assert "прочитать файл расписания" in str(exc.value)


def test_load_lessons_reports_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    path.write_text("{ это не json", encoding="utf-8")

    with pytest.raises(ScheduleError) as exc:
        load_lessons(path)

    assert "повреждён" in str(exc.value)


def test_broken_file_is_not_cached(tmp_path: Path) -> None:
    """После починки файла бот должен подхватить его без перезапуска процесса."""
    path = tmp_path / "schedule.json"
    path.write_text("{ это не json", encoding="utf-8")
    with pytest.raises(ScheduleError):
        load_lessons(path)

    write_schedule(path, [raw_lesson()])

    assert [lesson.id for lesson in load_lessons(path)] == ["L1216"]


# ======================================================================================
# 7. Целостность боевого файла расписания
# ======================================================================================


def test_real_schedule_matches_description(real_lessons: tuple[Lesson, ...]) -> None:
    """Цифры из CLAUDE.md: 21 занятие, 7 дистанционных, 3 корпуса."""
    assert len(real_lessons) == 21
    assert sum(1 for lesson in real_lessons if lesson.is_remote) == 7

    buildings = {lesson.building for lesson in real_lessons if not lesson.is_remote}
    assert buildings == {VOZNESENSKY, SADOVAYA, BOLSHAYA_MORSKAYA}


def test_real_schedule_has_no_broken_rows(real_lessons: tuple[Lesson, ...]) -> None:
    ids = [lesson.id for lesson in real_lessons]

    assert len(set(ids)) == len(ids), "в расписании есть повторяющиеся id"
    for lesson in real_lessons:
        assert 1 <= lesson.weekday <= 6
        assert lesson.start < lesson.end
        assert lesson.subject and lesson.teacher and lesson.room


def test_remote_lessons_have_no_real_building(real_lessons: tuple[Lesson, ...]) -> None:
    """Иначе будильник однажды повезёт человека в «Дистанционное обучение»."""
    for lesson in real_lessons:
        if lesson.is_remote:
            assert "Дистанционное" in lesson.building
        else:
            assert "Дистанционное" not in lesson.building


# ======================================================================================
# 8. Тексты для пользователя
# ======================================================================================


def test_format_date_is_russian() -> None:
    assert format_date(date(2026, 9, 1)) == "1 сентября"
    assert format_date(date(2027, 1, 4)) == "4 января"


def test_parity_word_is_russian() -> None:
    assert parity_word(TUE_ODD) == "числитель"
    assert parity_word(TUE_EVEN) == "знаменатель"


def test_format_lesson_shows_place_and_time() -> None:
    text = format_lesson(make_lesson(subject="Web-дизайн", kind="Пр", room="С 407"))

    assert "11:40–13:05" in text
    assert "Web-дизайн" in text
    assert "С 407" in text
    assert "дистанционно" not in text


def test_format_lesson_marks_remote() -> None:
    text = format_lesson(make_lesson(is_remote=True, building="Дистанционное обучение"))

    assert "дистанционно" in text
    assert "Дистанционное обучение" not in text


def test_format_lesson_escapes_html() -> None:
    """Текст уходит с ParseMode.HTML — угловые скобки в данных не должны ломать сообщение."""
    text = format_lesson(make_lesson(subject="Алгоритмы <A & B>", teacher="Иванов & Со"))

    assert "&lt;A &amp; B&gt;" in text
    assert "Алгоритмы <A" not in text
    assert "Иванов &amp; Со" in text


def test_format_day_says_no_lessons_today() -> None:
    text = format_day(THU_ODD, lessons_on(THU_ODD, load_lessons()), today=THU_ODD)

    assert "Сегодня пар нет" in text
    assert "четверг" in text
    assert "числитель" in text
    assert "3 сентября" in text


def test_format_day_says_no_lessons_tomorrow() -> None:
    text = format_day(FRI_EVEN, [], today=THU_EVEN)

    assert "Завтра пар нет" in text
    assert "пятница" in text
    assert "знаменатель" in text


def test_format_day_without_today_has_no_prefix() -> None:
    text = format_day(FRI_EVEN, [])

    assert "Сегодня" not in text
    assert "Завтра" not in text
    assert "Пятница" in text
    assert "Пар нет" in text


def test_format_day_lists_every_lesson() -> None:
    day = lessons_on(WED_EVEN, load_lessons())

    text = format_day(WED_EVEN, day, today=WED_EVEN)

    assert "Сегодня, среда" in text
    assert "знаменатель" in text
    for lesson in day:
        assert lesson.time_range in text
        assert lesson.subject in text
    assert text.count("—") >= len(day)


def test_format_day_marks_remote_lessons() -> None:
    day = lessons_on(MON_ODD, load_lessons())

    text = format_day(MON_ODD, day, today=MON_ODD)

    assert text.count("дистанционно") == len(day)


def test_format_day_works_for_every_day_of_two_weeks() -> None:
    """Ни один день двух недель не должен ронять форматирование."""
    lessons = load_lessons()
    for shift in range(14):
        day = MON_ODD + timedelta(days=shift)
        text = format_day(day, lessons_on(day, lessons), today=MON_ODD)

        assert text.strip()
        assert format_date(day) in text


def test_public_api_is_exported() -> None:
    """Всё, чем пользуются хендлеры и будущий будильник, доступно из пакета."""
    for name in (
        "week_parity",
        "lessons_on",
        "first_offline_lesson",
        "has_offline_lessons",
        "load_lessons",
        "format_day",
    ):
        assert hasattr(schedule, name)
