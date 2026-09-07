"""Сборка текстов из данных: без Telegram, без сети, без базы.

Сюда попадает всё, что превращает `User` в человеческие строки. Отдельный модуль нужен,
чтобы и онбординг, и /settings показывали настройки одинаково, а тесты могли проверить
формулировки, не поднимая бота.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from html import escape

from app import alarm, texts, users
from app.alarm import AlarmPlan
from app.buildings import building_title
from app.clock import MOSCOW
from app.db import Note, TeacherNote
from app.routing.base import TravelTime
from app.schedule import Lesson, format_date, parity_word

# Понедельник в нумерации isoweekday(): у группы он всегда полностью дистанционный.
MONDAY = 1


def minutes_word(count: int) -> str:
    """«1 минута», «3 минуты», «15 минут» — правильное окончание для числа."""
    tail_two = count % 100
    tail_one = count % 10
    if 11 <= tail_two <= 14:
        return "минут"
    if tail_one == 1:
        return "минута"
    if 2 <= tail_one <= 4:
        return "минуты"
    return "минут"


def format_minutes(count: int | None) -> str:
    """«30 минут» или «пока не задано», если значения ещё нет."""
    if count is None:
        return texts.VALUE_MISSING
    if count == 0:
        return "0 минут (совсем без запаса)"
    return f"{count} {minutes_word(count)}"


def transport_title(mode: str | None) -> str:
    """Способ передвижения по-русски — для карточки настроек и кнопок."""
    return texts.TRANSPORT_TITLES.get(mode or "", texts.VALUE_MISSING)


def transport_phrase(mode: str | None) -> str:
    """То же самое, но так, чтобы вставлялось в середину фразы."""
    return texts.TRANSPORT_PHRASES.get(mode or "", texts.VALUE_MISSING)


def location_title(user: users.User | None) -> str:
    """Про геопозицию говорим «сохранена», без сырых координат — так спокойнее."""
    if user is not None and user.has_location:
        return texts.LOCATION_SAVED_SHORT
    return texts.VALUE_MISSING


def format_settings(user: users.User | None) -> str:
    """Карточка настроек для /settings и для финала онбординга."""
    return texts.SETTINGS_CARD.format(
        location=location_title(user),
        transport=transport_title(user.transport_mode if user else None),
        prep=format_minutes(user.prep_minutes if user else None),
        buffer=format_minutes(user.buffer_minutes if user else None),
    )


def transport_way(mode: str | None) -> str:
    """«на машине» / «на общественном транспорте» — для фразы «Ехать ...»."""
    return texts.TRANSPORT_WAY.get(mode or "", texts.VALUE_MISSING)


def travel_quality_note(
    *, is_rough: bool, traffic_aware: bool, failed: bool = False
) -> str:
    """Оговорка про качество оценки времени в пути.

    Четыре случая, от честного к грубому: сервис не ответил вовсе, прикидка по прямой,
    настоящий маршрут с пробками, настоящий маршрут без пробок. Одна функция на /route
    и на будильник — чтобы формулировки не разъехались.
    """
    if failed:
        return texts.ALARM_NOTE_FALLBACK
    if is_rough:
        return texts.ROUTE_NOTE_ROUGH
    return texts.ROUTE_NOTE_TRAFFIC if traffic_aware else texts.ROUTE_NOTE_NO_TRAFFIC


def travel_note(travel: TravelTime) -> str:
    """То же самое для готового ответа маршрутизатора (/route)."""
    return travel_quality_note(
        is_rough=travel.is_rough, traffic_aware=travel.traffic_aware
    )


def format_route(
    day: date,
    lesson: Lesson,
    travel: TravelTime,
    transport_mode: str | None,
    today: date,
) -> str:
    """Ответ на /route: куда, когда, сколько ехать и насколько этому числу можно верить."""
    when = texts.DAY_PREFIXES.get((day - today).days, "").lower() or f"{day.day}-го"

    return "\n\n".join(
        (
            texts.ROUTE_TITLE.format(building=escape(building_title(lesson.building))),
            texts.ROUTE_LESSON.format(
                when=when,
                time=lesson.start_text,
                subject=escape(lesson.subject),
                kind=escape(lesson.kind),
                room=escape(lesson.room),
            ),
            texts.ROUTE_TRAVEL.format(
                transport=transport_way(transport_mode),
                minutes=format_minutes(travel.minutes),
            )
            + " "
            + travel_note(travel),
        )
    )


# --- Будильник и /preview -----------------------------------------------------------


def _time_text(moment: datetime) -> str:
    """«07:35» по Москве. Время всегда aware, поэтому пояс приводим явно."""
    return moment.astimezone(MOSCOW).strftime("%H:%M")


def _alarm_body(plan: AlarmPlan, transport_mode: str | None, prep_minutes: int | None) -> str:
    """Общая начинка будильника и предпросмотра: пара, дорога, время выхода."""
    lesson = plan.lesson
    if lesson is None or plan.leave_at is None or plan.travel_minutes is None:
        return ""

    place = escape(building_title(lesson.building))
    if plan.first_lesson_is_remote_but_alarm_for_later and plan.remote_before:
        remote = plan.remote_before[0]
        head = texts.ALARM_REMOTE_FIRST.format(
            remote_time=remote.start_text,
            remote_subject=escape(remote.subject),
            time=lesson.start_text,
        ) + "\n" + texts.ALARM_LESSON.format(
            time=lesson.start_text,
            subject=escape(lesson.subject),
            kind=escape(lesson.kind),
            building=place,
            room=escape(lesson.room),
        )
    else:
        head = texts.ALARM_LESSON.format(
            time=lesson.start_text,
            subject=escape(lesson.subject),
            kind=escape(lesson.kind),
            building=place,
            room=escape(lesson.room),
        )

    travel = texts.ALARM_TRAVEL.format(
        transport=transport_way(transport_mode),
        minutes=format_minutes(plan.travel_minutes),
    ) + " " + travel_quality_note(
        is_rough=plan.is_travel_rough,
        traffic_aware=plan.traffic_aware,
        failed=plan.travel_failed,
    )

    leave = texts.ALARM_LEAVE.format(
        leave=_time_text(plan.leave_at),
        prep=format_minutes(prep_minutes),
    )

    return "\n\n".join((head, travel, leave))


def format_alarm(
    plan: AlarmPlan, user: users.User | None, *, late_start: bool = False
) -> str:
    """Текст самого будильника. Зовётся только для плана, где `should_wake` истинно."""
    if not plan.should_wake:
        raise ValueError("format_alarm вызван для плана без будильника")

    parts = [texts.ALARM_GREETING]
    if late_start:
        parts.append(texts.ALARM_LATE_START)
    parts.append(
        _alarm_body(
            plan,
            user.transport_mode if user else None,
            user.prep_minutes if user else None,
        )
    )
    parts.append(texts.ALARM_FOOTER)
    return "\n\n".join(part for part in parts if part)


def alarm_silence_reason(plan: AlarmPlan) -> str:
    """Почему в этот день будильника не будет — человеческим языком."""
    if plan.reason == alarm.REASON_NOT_ONBOARDED:
        return texts.PREVIEW_NOT_READY
    if plan.reason == alarm.REASON_ALL_REMOTE and plan.day.isoweekday() == MONDAY:
        return texts.PREVIEW_SILENT_MONDAY
    return texts.PREVIEW_SILENT.get(plan.reason, texts.PREVIEW_SILENT_DEFAULT)


def format_preview(plan: AlarmPlan, user: users.User | None, today: date) -> str:
    """Ответ на /preview: то же, что прислал бы будильник, но по запросу и с заголовком."""
    prefix = texts.DAY_PREFIXES.get((plan.day - today).days, "")
    weekday_name = texts.WEEKDAYS_RU[plan.day.weekday()]
    title = f"{prefix}, {weekday_name}" if prefix else weekday_name.capitalize()

    header = texts.PREVIEW_HEADER.format(
        title=title,
        date=format_date(plan.day),
        parity=parity_word(plan.day),
    )

    if not plan.should_wake or plan.wake_at is None:
        return f"{header}\n\n{alarm_silence_reason(plan)}"

    body = _alarm_body(
        plan,
        user.transport_mode if user else None,
        user.prep_minutes if user else None,
    )
    wake = texts.PREVIEW_WAKE.format(wake=_time_text(plan.wake_at))
    return "\n\n".join((header, wake, body, texts.PREVIEW_HINT))


# --- Заметки ------------------------------------------------------------------------


def _subject_text(lesson: Lesson | None) -> str:
    """Название предмета для заметки. Пары может уже не быть в расписании — не падаем."""
    if lesson is None:
        return texts.NOTE_UNKNOWN_LESSON
    return escape(lesson.subject)


def format_note_prompt(lesson: Lesson) -> str:
    """Вопрос «есть что записать?» через десять минут после пары."""
    return texts.NOTES_PROMPT.format(
        subject=escape(lesson.subject), time=lesson.time_range
    )


def format_note_saved(due_date: date | None) -> str:
    """Подтверждение сохранения. Без срока — честно говорим, что напоминаний не будет."""
    if due_date is None:
        return texts.NOTES_SAVED_NO_DUE
    return texts.NOTES_SAVED.format(due=format_date(due_date))


def format_note_item(note: Note, lesson: Lesson | None) -> str:
    """Одна заметка в списке /notes.

    Текст заметки писал человек, а сообщения уходят с parse_mode=HTML — экранируем,
    иначе «<3» в домашке уронит отправку всего списка.
    """
    mark = "" if note.is_open else texts.NOTE_ITEM_DONE_MARK
    if lesson is None:
        head = texts.NOTE_ITEM_NO_TIME.format(
            mark=mark,
            date=format_date(note.lesson_date),
            subject=_subject_text(lesson),
            text=escape(note.text),
        )
    else:
        head = texts.NOTE_ITEM.format(
            mark=mark,
            date=format_date(note.lesson_date),
            time=lesson.time_range,
            subject=_subject_text(lesson),
            text=escape(note.text),
        )
    lines = [head]
    if not note.is_open:
        lines.append(texts.NOTE_ITEM_CLOSED)
    elif note.due_date is None:
        lines.append(texts.NOTE_ITEM_DUE_NONE)
    else:
        lines.append(texts.NOTE_ITEM_DUE.format(due=format_date(note.due_date)))
    return "\n".join(lines)


def format_note_reminder(note: Note, lesson: Lesson | None, *, morning: bool) -> str:
    """Напоминание по заметке: за сутки до пары или утром в её день."""
    template = (
        texts.NOTE_REMINDER_MORNING if morning else texts.NOTE_REMINDER_DAY_BEFORE
    )
    return template.format(
        time=lesson.start_text if lesson else "",
        subject=_subject_text(lesson),
        text=escape(note.text),
    )


def notes_title(period: str) -> str:
    return texts.NOTES_TITLES.get(period, texts.NOTES_TITLES["open"])


def notes_empty(period: str) -> str:
    return texts.NOTES_EMPTY.get(period, texts.NOTES_EMPTY["open"])


def format_lesson_pick(day: date) -> str:
    """Заголовок над кнопками выбора пары в /addnote."""
    return texts.ADDNOTE_PICK_LESSON.format(
        weekday=texts.WEEKDAYS_RU[day.weekday()].capitalize(),
        date=format_date(day),
    )


def format_no_lessons_to_note(day: date) -> str:
    """Пустой день в /addnote: привязывать заметку не к чему, и это нормально."""
    return texts.ADDNOTE_NO_LESSONS.format(date=format_date(day).capitalize())


# --- Заметки про преподавателей ------------------------------------------------------


def format_teacher_notes(notes: Sequence[TeacherNote]) -> str:
    """Список заметок про преподавателей, сгруппированный по человеку.

    Группировка по фамилии, а не сплошная лента: заметки справочные, и ищут их именно
    «что там было про Зверева». Порядок преподавателей — по алфавиту, внутри группы
    сохраняется порядок добавления.
    """
    by_teacher: dict[str, list[TeacherNote]] = {}
    for note in notes:
        by_teacher.setdefault(note.teacher, []).append(note)

    blocks: list[str] = []
    for teacher in sorted(by_teacher):
        lines = [texts.TEACHER_NOTE_GROUP.format(teacher=escape(teacher))]
        lines.extend(
            # Текст писал человек, а сообщения уходят с parse_mode=HTML: «<3» без
            # экранирования уронил бы отправку всего списка.
            texts.TEACHER_NOTE_ITEM.format(text=escape(note.text))
            for note in by_teacher[teacher]
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


__all__ = [
    "alarm_silence_reason",
    "format_lesson_pick",
    "format_no_lessons_to_note",
    "format_note_item",
    "format_teacher_notes",
    "format_note_prompt",
    "format_note_reminder",
    "format_note_saved",
    "notes_empty",
    "notes_title",
    "format_alarm",
    "format_minutes",
    "format_preview",
    "format_route",
    "format_settings",
    "location_title",
    "minutes_word",
    "transport_phrase",
    "transport_title",
    "transport_way",
    "travel_note",
    "travel_quality_note",
]
