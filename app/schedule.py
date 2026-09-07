"""Расписание группы 4-МД-4: загрузка данных, чётность недели, занятия дня.

Модуль намеренно ничего не знает ни про Telegram, ни про сеть: на вход — дата
(и, при желании, свой список занятий), на выход — обычные объекты Python.
Так всю логику можно проверить тестами, не поднимая бота.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

from app import texts
from app.clock import MOSCOW
from app.config import PROJECT_ROOT

# Расписание лежит рядом с кодом и правится только скриптом парсера, не руками.
SCHEDULE_PATH = PROJECT_ROOT / "data" / "schedule_4md4.json"

# Неделя, в которую попало 1 сентября 2026, — числитель. Якорь — её понедельник.
ANCHOR = date(2026, 8, 31)

ODD = "odd"
EVEN = "even"
BOTH = "both"
_KNOWN_PARITIES = (ODD, EVEN, BOTH)

# Сколько дней недели показывать в /week: с понедельника по субботу. Воскресенья
# в расписании нет вообще, и пустая строка «воскресенье — пар нет» только шумит.
WEEK_DAYS_SHOWN = 6

# На сколько недель вперёд и назад разрешено листать /week.
WEEK_NAV_LIMIT = 52


class ScheduleError(Exception):
    """Файл расписания не читается или в нём испорчены данные."""


@dataclass(frozen=True, slots=True)
class Lesson:
    """Одно занятие из `data/schedule_4md4.json`.

    `start` и `end` хранятся как `datetime.time`: так сортировка по времени честная,
    а не лексикографическая по строке.
    """

    id: str
    weekday: int  # 1 = понедельник ... 6 = суббота
    start: time
    end: time
    parity: str  # odd | even | both
    subject: str
    kind: str  # Лек | Пр | Лаб
    teacher: str
    building: str
    room: str
    is_remote: bool

    @property
    def start_text(self) -> str:
        return self.start.strftime("%H:%M")

    @property
    def end_text(self) -> str:
        return self.end.strftime("%H:%M")

    @property
    def time_range(self) -> str:
        return f"{self.start_text}–{self.end_text}"


def _parse_time(raw: object, lesson_id: str, field: str) -> time:
    if not isinstance(raw, str):
        raise ScheduleError(f"Занятие {lesson_id}: поле «{field}» должно быть строкой «ЧЧ:ММ».")
    try:
        hours, minutes = raw.split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise ScheduleError(
            f"Занятие {lesson_id}: не понимаю время «{raw}» в поле «{field}», "
            "ожидается формат «ЧЧ:ММ», например «11:40»."
        ) from exc


def parse_lesson(raw: dict[str, object]) -> Lesson:
    """Собирает `Lesson` из одного словаря JSON, проверяя обязательные поля."""
    lesson_id = str(raw.get("id") or "<без id>")

    missing = [
        field
        for field in (
            "id",
            "weekday",
            "start",
            "end",
            "parity",
            "subject",
            "kind",
            "teacher",
            "building",
            "room",
            "is_remote",
        )
        if field not in raw
    ]
    if missing:
        raise ScheduleError(
            f"Занятие {lesson_id}: в данных нет полей " + ", ".join(missing) + "."
        )

    weekday = raw["weekday"]
    if not isinstance(weekday, int) or isinstance(weekday, bool) or not 1 <= weekday <= 7:
        raise ScheduleError(
            f"Занятие {lesson_id}: weekday=«{weekday}», а должно быть число от 1 (пн) до 7 (вс)."
        )

    parity = raw["parity"]
    if parity not in _KNOWN_PARITIES:
        raise ScheduleError(
            f"Занятие {lesson_id}: parity=«{parity}», допустимы только "
            + ", ".join(_KNOWN_PARITIES)
            + "."
        )

    return Lesson(
        id=str(raw["id"]),
        weekday=weekday,
        start=_parse_time(raw["start"], lesson_id, "start"),
        end=_parse_time(raw["end"], lesson_id, "end"),
        parity=str(parity),
        subject=str(raw["subject"]),
        kind=str(raw["kind"]),
        teacher=str(raw["teacher"]),
        building=str(raw["building"]),
        room=str(raw["room"]),
        is_remote=bool(raw["is_remote"]),
    )


def parse_schedule(payload: object) -> tuple[Lesson, ...]:
    """Разбирает содержимое файла расписания (уже прочитанный JSON) в кортеж занятий."""
    if not isinstance(payload, dict):
        raise ScheduleError("Файл расписания должен быть JSON-объектом с ключом «lessons».")

    lessons = payload.get("lessons")
    if not isinstance(lessons, list) or not lessons:
        raise ScheduleError("В файле расписания нет непустого списка «lessons».")

    parsed: list[Lesson] = []
    for item in lessons:
        if not isinstance(item, dict):
            raise ScheduleError("В списке «lessons» найден элемент, который не является объектом.")
        parsed.append(parse_lesson(item))
    return tuple(parsed)


# Кэш по пути к файлу: расписание за семестр не меняется, читать его каждый раз незачем.
# Ключ — путь, чтобы тесты со своим файлом не затирали основной кэш и наоборот.
_lessons_cache: dict[Path, tuple[Lesson, ...]] = {}


def load_lessons(path: Path | str | None = None, *, use_cache: bool = True) -> tuple[Lesson, ...]:
    """Загружает расписание из JSON. По умолчанию — `data/schedule_4md4.json`."""
    file_path = Path(path) if path is not None else SCHEDULE_PATH
    key = file_path.resolve()

    if use_cache and key in _lessons_cache:
        return _lessons_cache[key]

    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduleError(
            f"Не удалось прочитать файл расписания «{file_path}»: {exc}."
        ) from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ScheduleError(
            f"Файл расписания «{file_path}» повреждён: {exc}."
        ) from exc

    lessons = parse_schedule(payload)
    if use_cache:
        _lessons_cache[key] = lessons
    return lessons


def clear_cache() -> None:
    """Сбрасывает кэш расписания. Нужен тестам и перезагрузке файла без рестарта бота."""
    _lessons_cache.clear()


def parse_iso_date(raw: str | None) -> date | None:
    """«2026-09-15» -> дата. Пусто или непонятный текст -> None.

    Общая точка разбора даты для всех команд (/preview, /day, /week, /addnote): формат
    один, и текст ошибки от команды к команде не разъезжается. Пустую строку и мусор
    намеренно не различаем — «какой день показывать, если даты нет» решает сам хендлер:
    у /preview это завтра, у /day — сегодня.
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def monday_of(d: date) -> date:
    """Понедельник недели, в которую попала дата. Неделя у группы всегда с понедельника."""
    return d - timedelta(days=d.weekday())


def week_parity(d: date) -> str:
    """«odd» (числитель) или «even» (знаменатель) для недели, в которую попала дата."""
    return ODD if ((monday_of(d) - ANCHOR).days // 7) % 2 == 0 else EVEN


def week_in_range(
    monday: date, today: date, *, limit_weeks: int = WEEK_NAV_LIMIT
) -> bool:
    """Попадает ли неделя в разумный диапазон вокруг сегодняшней даты.

    Расписание бесконечно повторяется по чётности, поэтому формально листать можно
    хоть до 2050 года — но показывать пары «через двадцать лет» бессмысленно, а если
    в данных однажды появятся границы семестра, ограничение уже будет на месте.
    """
    return abs((monday_of(monday) - monday_of(today)).days) <= limit_weeks * 7


def lessons_on(d: date, lessons: Sequence[Lesson] | None = None) -> list[Lesson]:
    """Занятия на конкретную дату с учётом чётности, по возрастанию времени начала.

    В данных день недели пронумерован 1 = понедельник, поэтому сравниваем с
    `isoweekday()`, а не с `weekday()` (тот считает от нуля). Воскресенье (7) просто
    ни с чем не совпадёт — в расписании таких занятий нет.
    """
    source = load_lessons() if lessons is None else lessons
    parity = week_parity(d)
    today = [
        lesson
        for lesson in source
        if lesson.weekday == d.isoweekday() and lesson.parity in (BOTH, parity)
    ]
    # id в ключе — чтобы порядок пар, начинающихся одновременно, был стабильным.
    return sorted(today, key=lambda lesson: (lesson.start, lesson.id))


def lesson_by_id(lesson_id: str, lessons: Sequence[Lesson] | None = None) -> Lesson | None:
    """Занятие по его id или None, если такого в расписании нет.

    None — не ошибка: заметка могла остаться от старой версии файла расписания, и падать
    из-за этого бот не должен.
    """
    source = load_lessons() if lessons is None else lessons
    for lesson in source:
        if lesson.id == lesson_id:
            return lesson
    return None


def first_offline_lesson(d: date, lessons: Sequence[Lesson] | None = None) -> Lesson | None:
    """Первая очная пара дня или None, если весь день дистанционный/пустой."""
    for lesson in lessons_on(d, lessons):
        if not lesson.is_remote:
            return lesson
    return None


def upcoming_offline_lesson(
    moment: datetime,
    *,
    days: int = 2,
    lessons: Sequence[Lesson] | None = None,
) -> tuple[date, Lesson] | None:
    """Ближайшая очная пара, до которой ещё надо доехать, и её дата.

    `moment` — «сейчас» с часовым поясом (`app.clock.now()`). Пары сегодняшнего дня,
    которые уже начались, пропускаем: ехать на них поздно, а следующая очная пара может
    быть и позже в тот же день. Дальше смотрим ещё `days - 1` дней вперёд.

    None означает «в ближайшие дни ехать некуда»: пустые дни и полный дистант у группы —
    норма, а не ошибка.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("upcoming_offline_lesson ждёт время с часовым поясом (app.clock.now()).")

    # Расписание живёт по Москве, поэтому «сегодня» считаем именно там.
    local = moment.astimezone(MOSCOW)
    start_date = local.date()
    current_time = local.time()

    for offset in range(max(days, 1)):
        day = start_date + timedelta(days=offset)
        for lesson in lessons_on(day, lessons):
            if lesson.is_remote:
                continue
            if offset == 0 and lesson.start <= current_time:
                continue
            return day, lesson
    return None


def teachers(lessons: Sequence[Lesson] | None = None) -> tuple[str, ...]:
    """Уникальные имена преподавателей из расписания, по алфавиту.

    Список берётся только из данных — выдумывать фамилии нельзя. Порядок фиксированный
    и стабильный: индекс в этом кортеже уходит в callback_data кнопки (кириллица плюс
    двоеточия в 64 байта лимита не помещаются и ломают разбор).
    """
    source = load_lessons() if lessons is None else lessons
    return tuple(sorted({lesson.teacher for lesson in source if lesson.teacher.strip()}))


def has_offline_lessons(d: date, lessons: Sequence[Lesson] | None = None) -> bool:
    """Есть ли в этот день хотя бы одна очная пара (пригодится будильнику на этапе 5)."""
    return first_offline_lesson(d, lessons) is not None


def format_date(d: date) -> str:
    """«3 сентября» — дата по-русски, без года и канцелярита."""
    return f"{d.day} {texts.MONTHS_RU[d.month - 1]}"


def parity_word(d: date) -> str:
    """«числитель» / «знаменатель» словами."""
    return texts.PARITY_RU[week_parity(d)]


def _day_prefix(d: date, today: date | None) -> str:
    """«Сегодня» / «Завтра» / «Вчера», если дата рядом с сегодняшней."""
    if today is None:
        return ""
    delta = (d - today).days
    return texts.DAY_PREFIXES.get(delta, "")


def format_lesson(lesson: Lesson) -> str:
    """Две строки про одну пару: время с предметом и место проведения."""
    head = texts.LESSON_HEAD.format(
        time_range=lesson.time_range,
        subject=escape(lesson.subject),
        kind=escape(lesson.kind),
    )
    if lesson.is_remote:
        place = texts.LESSON_REMOTE
    else:
        place = texts.LESSON_PLACE.format(
            building=escape(lesson.building),
            room=escape(lesson.room),
        )
    return f"{head}\n{place} · {escape(lesson.teacher)}"


def format_day(d: date, day_lessons: Sequence[Lesson], today: date | None = None) -> str:
    """Готовый текст для /today и /tomorrow.

    `today` нужен только для слов «Сегодня»/«Завтра» в заголовке — сама выборка пар
    от него не зависит.
    """
    prefix = _day_prefix(d, today)
    weekday_name = texts.WEEKDAYS_RU[d.weekday()]
    title = f"{prefix}, {weekday_name}" if prefix else weekday_name.capitalize()

    header = texts.DAY_HEADER.format(
        title=title,
        date=format_date(d),
        parity=parity_word(d),
    )

    if not day_lessons:
        empty = (
            texts.NO_LESSONS_PREFIXED.format(prefix=prefix) if prefix else texts.NO_LESSONS
        )
        return f"{header}\n\n{empty}"

    body = "\n\n".join(format_lesson(lesson) for lesson in day_lessons)
    return f"{header}\n\n{body}"


def _week_place(lesson: Lesson) -> str:
    """Где идёт пара — коротко, одной строкой в обзоре недели."""
    if lesson.is_remote:
        return texts.LESSON_REMOTE
    return texts.WEEK_ROOM.format(room=escape(lesson.room))


def format_week_day(
    d: date, day_lessons: Sequence[Lesson], today: date | None = None
) -> str:
    """Один день внутри обзора недели: заголовок и компактный список пар."""
    weekday_name = texts.WEEKDAYS_RU[d.weekday()].capitalize()
    template = (
        texts.WEEK_DAY_TODAY if today is not None and d == today else texts.WEEK_DAY
    )
    title = template.format(weekday=weekday_name, date=format_date(d))

    if not day_lessons:
        return f"{title}\n{texts.WEEK_DAY_EMPTY}"

    rows = [
        texts.WEEK_LESSON.format(
            time=lesson.start_text,
            subject=escape(lesson.subject),
            kind=escape(lesson.kind),
            place=_week_place(lesson),
        )
        for lesson in day_lessons
    ]
    return "\n".join([title, *rows])


def format_week(
    monday: date, today: date | None = None, lessons: Sequence[Lesson] | None = None
) -> str:
    """Готовый текст /week: заголовок с диапазоном и чётностью плюс дни с понедельника по субботу.

    `monday` приводится к понедельнику своей недели — так неделя не «съедет», даже если
    хендлер передаст сюда произвольную дату. `today` нужен только для пометки текущего
    дня; на выборку пар он не влияет.
    """
    start = monday_of(monday)
    end = start + timedelta(days=WEEK_DAYS_SHOWN - 1)

    header = texts.WEEK_HEADER.format(
        start=format_date(start),
        end=format_date(end),
        parity=parity_word(start),
    )

    blocks = [header]
    for offset in range(WEEK_DAYS_SHOWN):
        day = start + timedelta(days=offset)
        blocks.append(format_week_day(day, lessons_on(day, lessons), today))
    blocks.append(texts.WEEK_FOOTER)
    return "\n\n".join(blocks)


__all__ = [
    "ANCHOR",
    "WEEK_DAYS_SHOWN",
    "WEEK_NAV_LIMIT",
    "Lesson",
    "ScheduleError",
    "clear_cache",
    "first_offline_lesson",
    "format_day",
    "format_lesson",
    "format_week",
    "format_week_day",
    "has_offline_lessons",
    "lesson_by_id",
    "lessons_on",
    "load_lessons",
    "monday_of",
    "parity_word",
    "parse_iso_date",
    "parse_schedule",
    "teachers",
    "upcoming_offline_lesson",
    "week_in_range",
    "week_parity",
]
