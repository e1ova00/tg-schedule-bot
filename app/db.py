"""Хранилище SQLite: подключение, схема таблиц и журналы.

Модуль знает про таблицы и ничего не знает про Telegram: на вход — соединение
aiosqlite и обычные значения, на выход — простые объекты Python. Настройки
пользователя живут в `app/users.py`, корпуса — в `app/buildings.py`, здесь же —
журналы будильника и всё хранилище заметок.

Даты (`lesson_date`, `due_date`) хранятся строками «ГГГГ-ММ-ДД», отметки времени —
строками с поясом («2026-09-04T10:15:00+03:00»). Naive-времени в базе быть не должно:
на сервере в UTC такие записи молча уезжают на три часа.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import aiosqlite

from app import clock

logger = logging.getLogger(__name__)

# Состояния заметки. Строками, а не Enum: их видно в базе и в логах как есть.
NOTE_OPEN = "open"
NOTE_DONE = "done"

# Виды напоминаний по заметке — ключ журнала `note_reminder_log`.
REMINDER_DAY_BEFORE = "day_before"
REMINDER_MORNING = "morning"

# Схема. Все запросы идемпотентны (IF NOT EXISTS), поэтому их можно гонять при каждом старте.
SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        telegram_id     INTEGER PRIMARY KEY,
        username        TEXT,
        latitude        REAL,
        longitude       REAL,
        transport_mode  TEXT CHECK(transport_mode IN ('car','public_transport')),
        prep_minutes    INTEGER,
        buffer_minutes  INTEGER,
        onboarded_at    TEXT,
        created_at      TEXT,
        updated_at      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS buildings (
        address     TEXT PRIMARY KEY,
        title       TEXT,
        latitude    REAL,
        longitude   REAL,
        source      TEXT,
        updated_at  TEXT
    )
    """,
    # Отметка «этому человеку будильник на эту дату уже уходил». Задачи планировщика
    # живут в памяти и пересчитываются при каждом старте бота, поэтому без такой записи
    # случайный перезапуск в 7 утра разбудил бы второй раз за то же утро.
    """
    CREATE TABLE IF NOT EXISTS alarm_log (
        telegram_id INTEGER NOT NULL,
        date        TEXT NOT NULL,
        sent_at     TEXT,
        PRIMARY KEY (telegram_id, date)
    )
    """,
    # Заметка привязана к конкретному занятию конкретного числа (lesson_id + lesson_date),
    # а не к предмету: «то, что задали в среду 2 сентября», а не «по Web-дизайну вообще».
    # due_date — дата следующего такого же занятия; NULL означает «срока нет»
    # (следующего занятия в пределах горизонта не нашлось), напоминаний тогда не будет.
    """
    CREATE TABLE IF NOT EXISTS notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        lesson_id   TEXT NOT NULL,
        lesson_date TEXT NOT NULL,
        due_date    TEXT,
        text        TEXT NOT NULL,
        status      TEXT NOT NULL CHECK(status IN ('open','done')),
        created_at  TEXT,
        closed_at   TEXT
    )
    """,
    # Отметка «про эту пару этого числа я у человека уже спрашивал». Задачи планировщика
    # живут в памяти и пересчитываются при старте, поэтому без журнала перезапуск бота
    # задал бы один и тот же вопрос дважды.
    """
    CREATE TABLE IF NOT EXISTS note_prompt_log (
        telegram_id INTEGER NOT NULL,
        lesson_id   TEXT NOT NULL,
        lesson_date TEXT NOT NULL,
        asked_at    TEXT,
        PRIMARY KEY (telegram_id, lesson_id, lesson_date)
    )
    """,
    # То же самое для напоминаний: «за сутки» и «утром» по каждой заметке уходят
    # ровно по одному разу. Это не то же, что закрытие заметки: незакрытая заметка
    # остаётся в списке, но повторно бомбить ею человека не нужно.
    """
    CREATE TABLE IF NOT EXISTS note_reminder_log (
        note_id INTEGER NOT NULL,
        kind    TEXT NOT NULL CHECK(kind IN ('day_before','morning')),
        sent_at TEXT,
        PRIMARY KEY (note_id, kind)
    )
    """,
)


def now_iso() -> str:
    """Отметка времени для полей created_at/updated_at/onboarded_at.

    Всегда московское время со смещением: «2026-09-04T10:15:00+03:00». Naive-строк
    в базе быть не должно — иначе на сервере в UTC записи начнут «уезжать» на три часа.
    """
    return clock.now().isoformat(timespec="seconds")


async def init_schema(conn: aiosqlite.Connection) -> None:
    """Создаёт таблицы, если их ещё нет."""
    for statement in SCHEMA:
        await conn.execute(statement)
    await conn.commit()


async def connect(db_path: Path | str) -> aiosqlite.Connection:
    """Открывает соединение с базой и настраивает его под наш сценарий.

    `row_factory = aiosqlite.Row` нужен, чтобы читать поля по имени, а не по индексу:
    так запросы не ломаются при добавлении столбцов.
    """
    path = Path(db_path)
    if path.name != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    # WAL спокойнее переживает одновременные чтение и запись (бот + ручной просмотр базы).
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.commit()
    return conn


async def open_database(db_path: Path | str) -> aiosqlite.Connection:
    """Открывает базу и сразу приводит её схему в порядок."""
    conn = await connect(db_path)
    await init_schema(conn)
    logger.info("База данных готова: %s", db_path)
    return conn


# --- Журнал отправленных будильников ------------------------------------------------


async def already_sent(
    conn: aiosqlite.Connection, telegram_id: int, day: date
) -> bool:
    """Уходил ли уже будильник этому человеку на эту дату."""
    async with conn.execute(
        "SELECT 1 FROM alarm_log WHERE telegram_id = ? AND date = ?",
        (telegram_id, day.isoformat()),
    ) as cursor:
        return await cursor.fetchone() is not None


async def mark_sent(conn: aiosqlite.Connection, telegram_id: int, day: date) -> None:
    """Отмечает, что будильник на эту дату отправлен.

    `INSERT OR IGNORE`: повторный вызов не должен ни падать, ни сдвигать время первой
    отправки — именно оно интересно, если потом разбираться, почему звонок был не вовремя.
    """
    await conn.execute(
        "INSERT OR IGNORE INTO alarm_log (telegram_id, date, sent_at) VALUES (?, ?, ?)",
        (telegram_id, day.isoformat(), now_iso()),
    )
    await conn.commit()


async def forget_sent(conn: aiosqlite.Connection, telegram_id: int, day: date) -> None:
    """Убирает отметку об отправке. Нужна тестам и ручной перепроверке будильника."""
    await conn.execute(
        "DELETE FROM alarm_log WHERE telegram_id = ? AND date = ?",
        (telegram_id, day.isoformat()),
    )
    await conn.commit()


# --- Заметки ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Note:
    """Строка таблицы `notes` — одна запись «что задали» по одной паре."""

    id: int
    telegram_id: int
    lesson_id: str
    lesson_date: date
    text: str
    status: str = NOTE_OPEN
    due_date: date | None = None
    created_at: str | None = None
    closed_at: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == NOTE_OPEN


def _parse_date(raw: object) -> date | None:
    """«2026-09-02» -> date. Мусор в базе не должен ронять список заметок."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.warning("В базе заметок лежит непонятная дата «%s», пропускаю её", raw)
        return None


def _row_to_note(row: aiosqlite.Row) -> Note:
    lesson_date = _parse_date(row["lesson_date"])
    if lesson_date is None:
        # lesson_date обязателен: без него заметку не к чему привязать.
        raise ValueError(f"Заметка {row['id']}: испорчена дата занятия «{row['lesson_date']}»")
    return Note(
        id=int(row["id"]),
        telegram_id=int(row["telegram_id"]),
        lesson_id=str(row["lesson_id"]),
        lesson_date=lesson_date,
        text=str(row["text"]),
        status=str(row["status"]),
        due_date=_parse_date(row["due_date"]),
        created_at=row["created_at"],
        closed_at=row["closed_at"],
    )


async def create_note(
    conn: aiosqlite.Connection,
    telegram_id: int,
    lesson_id: str,
    lesson_date: date,
    text: str,
    due_date: date | None = None,
) -> Note:
    """Создаёт заметку в статусе «открыта» и возвращает её целиком."""
    cursor = await conn.execute(
        "INSERT INTO notes "
        "(telegram_id, lesson_id, lesson_date, due_date, text, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            telegram_id,
            lesson_id,
            lesson_date.isoformat(),
            due_date.isoformat() if due_date else None,
            text,
            NOTE_OPEN,
            now_iso(),
        ),
    )
    await conn.commit()

    note_id = cursor.lastrowid
    await cursor.close()
    note = await get_note(conn, int(note_id or 0))
    assert note is not None  # только что вставили — строка обязана быть
    return note


async def get_note(conn: aiosqlite.Connection, note_id: int) -> Note | None:
    """Заметка по id или None, если её уже нет."""
    async with conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_note(row) if row else None


async def close_note(
    conn: aiosqlite.Connection, note_id: int, telegram_id: int | None = None
) -> Note | None:
    """Помечает заметку сделанной. Возвращает её свежую версию или None, если не нашлась.

    `telegram_id` — необязательная страховка: кнопка «Сделал» приходит с id заметки
    в callback_data, и чужую закрывать ею не должно быть возможности.
    """
    note = await get_note(conn, note_id)
    if note is None or (telegram_id is not None and note.telegram_id != telegram_id):
        return None
    if not note.is_open:
        # Уже закрыта: время закрытия не сдвигаем, повторное нажатие ничего не ломает.
        return note

    await conn.execute(
        "UPDATE notes SET status = ?, closed_at = ? WHERE id = ?",
        (NOTE_DONE, now_iso(), note_id),
    )
    await conn.commit()
    return await get_note(conn, note_id)


async def list_notes(
    conn: aiosqlite.Connection,
    telegram_id: int | None = None,
    *,
    status: str | None = None,
    since: date | None = None,
    until: date | None = None,
    with_due_date: bool = False,
) -> list[Note]:
    """Заметки по фильтрам, от старой пары к новой.

    `since`/`until` сравниваются с `lesson_date` — датой пары, после которой заметку
    завели. `with_due_date=True` оставляет только те, у которых есть срок: с ними
    работает планировщик напоминаний.
    """
    conditions: list[str] = []
    params: list[object] = []

    if telegram_id is not None:
        conditions.append("telegram_id = ?")
        params.append(telegram_id)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if since is not None:
        conditions.append("lesson_date >= ?")
        params.append(since.isoformat())
    if until is not None:
        conditions.append("lesson_date <= ?")
        params.append(until.isoformat())
    if with_due_date:
        conditions.append("due_date IS NOT NULL")

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    async with conn.execute(
        f"SELECT * FROM notes{where} ORDER BY lesson_date, id",  # noqa: S608 — условия собраны из констант
        params,
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_note(row) for row in rows]


async def list_open_notes(
    conn: aiosqlite.Connection, telegram_id: int | None = None, *, with_due_date: bool = False
) -> list[Note]:
    """Незакрытые заметки — главный экран /notes и вход в планировщик напоминаний."""
    return await list_notes(
        conn, telegram_id, status=NOTE_OPEN, with_due_date=with_due_date
    )


# --- Журнал вопросов про домашку -----------------------------------------------------


async def already_prompted(
    conn: aiosqlite.Connection, telegram_id: int, lesson_id: str, lesson_date: date
) -> bool:
    """Спрашивали ли уже этого человека про эту пару этого числа."""
    async with conn.execute(
        "SELECT 1 FROM note_prompt_log "
        "WHERE telegram_id = ? AND lesson_id = ? AND lesson_date = ?",
        (telegram_id, lesson_id, lesson_date.isoformat()),
    ) as cursor:
        return await cursor.fetchone() is not None


async def mark_prompted(
    conn: aiosqlite.Connection, telegram_id: int, lesson_id: str, lesson_date: date
) -> None:
    """Отмечает, что вопрос про домашку по этой паре уже задан."""
    await conn.execute(
        "INSERT OR IGNORE INTO note_prompt_log "
        "(telegram_id, lesson_id, lesson_date, asked_at) VALUES (?, ?, ?, ?)",
        (telegram_id, lesson_id, lesson_date.isoformat(), now_iso()),
    )
    await conn.commit()


# --- Журнал напоминаний по заметкам --------------------------------------------------


async def already_reminded(conn: aiosqlite.Connection, note_id: int, kind: str) -> bool:
    """Уходило ли уже напоминание такого вида по этой заметке."""
    async with conn.execute(
        "SELECT 1 FROM note_reminder_log WHERE note_id = ? AND kind = ?",
        (note_id, kind),
    ) as cursor:
        return await cursor.fetchone() is not None


async def mark_reminded(conn: aiosqlite.Connection, note_id: int, kind: str) -> None:
    """Отмечает отправленное напоминание. Заметку при этом не закрывает."""
    await conn.execute(
        "INSERT OR IGNORE INTO note_reminder_log (note_id, kind, sent_at) VALUES (?, ?, ?)",
        (note_id, kind, now_iso()),
    )
    await conn.commit()


__all__ = [
    "NOTE_DONE",
    "NOTE_OPEN",
    "REMINDER_DAY_BEFORE",
    "REMINDER_MORNING",
    "SCHEMA",
    "Note",
    "already_prompted",
    "already_reminded",
    "already_sent",
    "close_note",
    "connect",
    "create_note",
    "forget_sent",
    "get_note",
    "init_schema",
    "list_notes",
    "list_open_notes",
    "mark_prompted",
    "mark_reminded",
    "mark_sent",
    "now_iso",
    "open_database",
]
