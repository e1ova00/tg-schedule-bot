"""Тесты хранилища заметок про преподавателей: таблица `teacher_notes` в `app/db.py`.

База временная (в памяти, фикстура `db` из `tests/conftest.py`), Telegram и сеть не нужны.

Такая заметка — справочная, а не задача: ни статуса, ни срока, ни напоминаний. Зато
важны две вещи: порядок (список печатается сверху вниз, свежее должно остаться на
экране) и изоляция — заметки одного человека не должны попадать в список другого.
"""

from __future__ import annotations

import aiosqlite
import pytest

from app import db as db_module
from app.db import (
    TeacherNote,
    create_teacher_note,
    get_teacher_note,
    list_teacher_notes,
)
from fakes import USER_ID

OTHER_USER_ID = 777

ZVEREV = "Зверев В.В."
AGEEVA = "Агеева Е.А."


async def columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:  # noqa: S608 — имя из теста
        return {str(row["name"]) for row in await cursor.fetchall()}


# ======================================================================================
# 1. Схема
# ======================================================================================


async def test_teacher_notes_table_exists(db: aiosqlite.Connection) -> None:
    assert await columns(db, "teacher_notes") == {
        "id",
        "telegram_id",
        "teacher",
        "text",
        "created_at",
    }


async def test_teacher_note_has_no_status_and_no_due_date(
    db: aiosqlite.Connection,
) -> None:
    """Это справка, а не задача: закрывать и напоминать тут нечего.

    Если однажды в таблице появятся `status` или `due_date`, значит две разные сущности
    начали смешиваться — и напоминания поедут не туда.
    """
    fields = await columns(db, "teacher_notes")

    assert "status" not in fields
    assert "due_date" not in fields
    assert "lesson_id" not in fields


# ======================================================================================
# 2. Создание
# ======================================================================================


async def test_create_returns_the_whole_note(db: aiosqlite.Connection) -> None:
    note = await create_teacher_note(db, USER_ID, ZVEREV, "принимает только на почту")

    assert isinstance(note, TeacherNote)
    assert note.id > 0
    assert note.telegram_id == USER_ID
    assert note.teacher == ZVEREV
    assert note.text == "принимает только на почту"
    assert note.created_at is not None


async def test_created_at_is_moscow_time_with_offset(db: aiosqlite.Connection) -> None:
    """Naive-времени в базе быть не должно: на сервере в UTC оно уедет на три часа."""
    note = await create_teacher_note(db, USER_ID, ZVEREV, "что-то важное")

    assert note.created_at is not None
    assert note.created_at.endswith("+03:00")


async def test_text_is_stored_as_written(db: aiosqlite.Connection) -> None:
    """Экранирование — дело вывода: в базе лежит ровно то, что написал человек."""
    note = await create_teacher_note(db, USER_ID, ZVEREV, "просит отчёт в <pdf> & docx")

    saved = await get_teacher_note(db, note.id)
    assert saved is not None
    assert saved.text == "просит отчёт в <pdf> & docx"


async def test_ids_grow_and_do_not_repeat(db: aiosqlite.Connection) -> None:
    first = await create_teacher_note(db, USER_ID, ZVEREV, "раз")
    second = await create_teacher_note(db, USER_ID, AGEEVA, "два")

    assert second.id > first.id


async def test_several_notes_about_one_teacher_are_allowed(
    db: aiosqlite.Connection,
) -> None:
    """Ограничения «одна заметка на человека» нет и быть не должно."""
    await create_teacher_note(db, USER_ID, ZVEREV, "принимает только на почту")
    await create_teacher_note(db, USER_ID, ZVEREV, "любит вопросы по лекциям")

    found = await list_teacher_notes(db, USER_ID, teacher=ZVEREV)

    assert [note.text for note in found] == [
        "принимает только на почту",
        "любит вопросы по лекциям",
    ]


# ======================================================================================
# 3. Список и его порядок
# ======================================================================================


async def test_empty_list_for_a_new_user(db: aiosqlite.Connection) -> None:
    assert await list_teacher_notes(db, USER_ID) == []


async def test_list_keeps_the_order_of_adding(db: aiosqlite.Connection) -> None:
    """Старые сверху: список печатается сверху вниз, и на экране остаётся свежее."""
    for index in range(5):
        await create_teacher_note(db, USER_ID, ZVEREV, f"заметка {index}")

    found = await list_teacher_notes(db, USER_ID)

    assert [note.text for note in found] == [f"заметка {index}" for index in range(5)]


async def test_list_sorts_by_created_at_before_id(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сортировка именно по времени: id — только страховка при одинаковой отметке.

    Отметки подменяем так, чтобы порядок по времени был обратен порядку вставки —
    иначе тест прошёл бы и при сортировке по одному лишь id.
    """
    stamps = iter(
        [
            "2026-09-03T10:00:00+03:00",
            "2026-09-02T10:00:00+03:00",
            "2026-09-01T10:00:00+03:00",
        ]
    )
    monkeypatch.setattr(db_module, "now_iso", lambda: next(stamps))

    for text in ("третья", "вторая", "первая"):
        await create_teacher_note(db, USER_ID, ZVEREV, text)

    found = await list_teacher_notes(db, USER_ID)

    assert [note.text for note in found] == ["первая", "вторая", "третья"]


async def test_id_breaks_the_tie_within_the_same_second(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """created_at пишется с точностью до секунды — две заметки подряд легко совпадут."""
    monkeypatch.setattr(db_module, "now_iso", lambda: "2026-09-01T10:00:00+03:00")

    for text in ("раз", "два", "три"):
        await create_teacher_note(db, USER_ID, ZVEREV, text)

    found = await list_teacher_notes(db, USER_ID)

    assert [note.text for note in found] == ["раз", "два", "три"]
    assert [note.id for note in found] == sorted(note.id for note in found)


async def test_list_can_filter_by_teacher(db: aiosqlite.Connection) -> None:
    await create_teacher_note(db, USER_ID, ZVEREV, "про Зверева")
    await create_teacher_note(db, USER_ID, AGEEVA, "про Агееву")

    found = await list_teacher_notes(db, USER_ID, teacher=ZVEREV)

    assert [note.text for note in found] == ["про Зверева"]


async def test_list_without_user_returns_everyone(db: aiosqlite.Connection) -> None:
    """Без telegram_id — все записи: так удобно смотреть базу руками при отладке."""
    await create_teacher_note(db, USER_ID, ZVEREV, "моя")
    await create_teacher_note(db, OTHER_USER_ID, ZVEREV, "чужая")

    assert len(await list_teacher_notes(db)) == 2


# ======================================================================================
# 4. Изоляция пользователей
# ======================================================================================


async def test_other_users_notes_are_invisible(db: aiosqlite.Connection) -> None:
    """Пользователей у бота двое-трое, и заметки у каждого свои."""
    await create_teacher_note(db, OTHER_USER_ID, ZVEREV, "чужая заметка")
    mine = await create_teacher_note(db, USER_ID, ZVEREV, "моя заметка")

    found = await list_teacher_notes(db, USER_ID)

    assert [note.id for note in found] == [mine.id]
    assert "чужая заметка" not in [note.text for note in found]


async def test_teacher_filter_does_not_leak_other_users(
    db: aiosqlite.Connection,
) -> None:
    """Оба фильтра работают вместе, а не «или-или»."""
    await create_teacher_note(db, OTHER_USER_ID, ZVEREV, "чужая про Зверева")

    assert await list_teacher_notes(db, USER_ID, teacher=ZVEREV) == []


async def test_two_users_keep_their_own_lists(db: aiosqlite.Connection) -> None:
    await create_teacher_note(db, USER_ID, ZVEREV, "моя первая")
    await create_teacher_note(db, OTHER_USER_ID, AGEEVA, "чужая первая")
    await create_teacher_note(db, USER_ID, AGEEVA, "моя вторая")

    assert [note.text for note in await list_teacher_notes(db, USER_ID)] == [
        "моя первая",
        "моя вторая",
    ]
    assert [note.text for note in await list_teacher_notes(db, OTHER_USER_ID)] == [
        "чужая первая"
    ]


# ======================================================================================
# 5. Поиск по id
# ======================================================================================


async def test_get_finds_the_note_by_id(db: aiosqlite.Connection) -> None:
    created = await create_teacher_note(db, USER_ID, ZVEREV, "принимает только на почту")

    assert await get_teacher_note(db, created.id) == created


@pytest.mark.parametrize("note_id", [0, -1, 999])
async def test_get_returns_none_for_a_missing_id(
    db: aiosqlite.Connection, note_id: int
) -> None:
    await create_teacher_note(db, USER_ID, ZVEREV, "единственная")

    assert await get_teacher_note(db, note_id) is None


async def test_get_is_a_plain_lookup_without_owner_check(
    db: aiosqlite.Connection,
) -> None:
    """Фиксируем как есть: `get_teacher_note` ищет строго по id, хозяина не проверяет.

    Для заметок к парам такая проверка есть (`close_note` принимает telegram_id), потому
    что id заметки лежит прямо в callback_data кнопки «Сделал» и его можно подобрать.
    У заметок про преподавателей кнопок с id нет вообще: наружу их отдаёт только
    `list_teacher_notes`, который всегда фильтрует по telegram_id. Если однажды такая
    кнопка появится, этот тест должен упасть — и вместе с ним появиться проверка хозяина.
    """
    someone_elses = await create_teacher_note(db, OTHER_USER_ID, ZVEREV, "чужая")

    found = await get_teacher_note(db, someone_elses.id)

    assert found is not None
    assert found.telegram_id == OTHER_USER_ID
    # Никто, кроме самой функции создания, не зовёт get_teacher_note по чужому id.
    assert await list_teacher_notes(db, USER_ID) == []


async def test_teacher_notes_do_not_touch_lesson_notes(
    db: aiosqlite.Connection,
) -> None:
    """Две таблицы независимы: /notes и /teachernote не должны смешиваться."""
    from datetime import date

    await create_teacher_note(db, USER_ID, ZVEREV, "про преподавателя")
    await db_module.create_note(
        db, USER_ID, "L1216", date(2026, 9, 1), "про домашку", date(2026, 9, 15)
    )

    teacher_notes = await list_teacher_notes(db, USER_ID)
    lesson_notes = await db_module.list_notes(db, USER_ID)

    assert [note.text for note in teacher_notes] == ["про преподавателя"]
    assert [note.text for note in lesson_notes] == ["про домашку"]
