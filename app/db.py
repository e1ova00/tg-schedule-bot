"""Хранилище SQLite: подключение и схема таблиц.

Модуль намеренно маленький: он умеет открыть базу, создать таблицы и подсказать
текущее время в виде строки. Всё остальное — в `app/users.py` и `app/buildings.py`,
чтобы их можно было проверить тестами на временной базе (`:memory:` или tmp-файл).
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from app import clock

logger = logging.getLogger(__name__)

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


__all__ = ["SCHEMA", "connect", "init_schema", "now_iso", "open_database"]
