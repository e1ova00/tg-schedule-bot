"""Корпуса СПбГУПТД: адреса, координаты и выбор корпуса на конкретный день.

Координаты трёх корпусов зашиты константами и один раз «засеваются» в таблицу
`buildings` при старте бота — это и есть кэш геокодинга из ТЗ. Когда на этапе 4
появится ключ 2ГИС, автоматический геокодер сможет уточнять те же строки, механизм
не изменится.

Адрес — ключ связи «занятие → корпус → координаты», поэтому строки здесь обязаны
совпадать с полем `building` в `data/schedule_4md4.json` символ в символ.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import aiosqlite

from app.db import now_iso
from app.schedule import Lesson, first_offline_lesson

logger = logging.getLogger(__name__)

# В расписании дистант записан «адресом» — это не корпус, ехать туда не нужно.
REMOTE_ADDRESS = "Дистанционное обучение"

# Откуда взялись координаты, если однажды понадобится их пересчитать геокодером.
SOURCE_MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Building:
    """Учебный корпус: адрес как в расписании, человеческое имя и точка на карте."""

    address: str
    title: str
    latitude: float
    longitude: float


# Координаты домов в Санкт-Петербурге, снятые по адресам вручную (точность — до здания,
# для расчёта времени в пути этого достаточно).
BUILDINGS: tuple[Building, ...] = (
    Building(
        address="пр. Вознесенский, д. 46",
        title="Вознесенский, 46",
        latitude=59.9200,
        longitude=30.3033,
    ),
    Building(
        address="ул. Садовая, д. 54",
        title="Садовая, 54",
        latitude=59.9223,
        longitude=30.3009,
    ),
    Building(
        address="ул. Большая Морская, д. 18",
        title="Большая Морская, 18",
        latitude=59.9353,
        longitude=30.3155,
    ),
)

BUILDINGS_BY_ADDRESS: dict[str, Building] = {item.address: item for item in BUILDINGS}


def building_title(address: str | None) -> str:
    """Короткое читаемое название корпуса. Незнакомый адрес возвращаем как есть."""
    if not address:
        return ""
    known = BUILDINGS_BY_ADDRESS.get(address)
    return known.title if known else address


def building_for_date(d: date, lessons: Sequence[Lesson] | None = None) -> str | None:
    """Адрес корпуса, до которого нужно доехать в этот день.

    Берётся корпус первой очной пары: во вторник по знаменателю это Садовая,
    по числителю — Вознесенский. Если очных пар нет (пустой день или полный дистант) — None.
    """
    lesson = first_offline_lesson(d, lessons)
    return lesson.building if lesson is not None else None


def building_coordinates(address: str | None) -> tuple[float, float] | None:
    """Координаты корпуса из констант. Нужна как запасной путь, если базы под рукой нет."""
    known = BUILDINGS_BY_ADDRESS.get(address or "")
    return (known.latitude, known.longitude) if known else None


# --- Работа с таблицей buildings ----------------------------------------------------


def _row_to_building(row: aiosqlite.Row) -> Building:
    return Building(
        address=str(row["address"]),
        title=str(row["title"] or ""),
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
    )


async def seed_buildings(
    conn: aiosqlite.Connection,
    buildings: Sequence[Building] = BUILDINGS,
    *,
    source: str = SOURCE_MANUAL,
) -> int:
    """Записывает координаты корпусов в базу и возвращает число добавленных строк.

    `INSERT OR IGNORE`: уже сохранённые адреса не трогаем. Так засев не затрёт более
    точные координаты, если их когда-нибудь пропишет геокодер.
    """
    added = 0
    for item in buildings:
        cursor = await conn.execute(
            "INSERT OR IGNORE INTO buildings "
            "(address, title, latitude, longitude, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (item.address, item.title, item.latitude, item.longitude, source, now_iso()),
        )
        added += cursor.rowcount or 0
    await conn.commit()

    if added:
        logger.info("В таблицу корпусов добавлено записей: %d", added)
    return added


async def get_building(conn: aiosqlite.Connection, address: str) -> Building | None:
    """Корпус из базы по адресу или None, если такого адреса там нет."""
    async with conn.execute(
        "SELECT address, title, latitude, longitude FROM buildings WHERE address = ?",
        (address,),
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_building(row) if row else None


async def list_buildings(conn: aiosqlite.Connection) -> list[Building]:
    """Все корпуса из базы, по алфавиту адреса."""
    async with conn.execute(
        "SELECT address, title, latitude, longitude FROM buildings ORDER BY address"
    ) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_building(row) for row in rows]


async def building_point(
    conn: aiosqlite.Connection, address: str
) -> tuple[float, float] | None:
    """Координаты корпуса: сначала из базы (кэш геокодинга), потом из констант.

    Сбой базы здесь не должен ломать расчёт маршрута — константы всегда под рукой.
    """
    try:
        saved = await get_building(conn, address)
    except Exception:  # noqa: BLE001 — есть запасной путь, падать незачем
        logger.warning("Не удалось прочитать корпус «%s» из базы", address, exc_info=True)
        saved = None

    if saved is not None:
        return (saved.latitude, saved.longitude)
    return building_coordinates(address)


async def save_building(
    conn: aiosqlite.Connection,
    building: Building,
    *,
    source: str = SOURCE_MANUAL,
) -> None:
    """Создаёт или обновляет запись корпуса. Пригодится геокодеру на этапе 4."""
    await conn.execute(
        "INSERT INTO buildings (address, title, latitude, longitude, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(address) DO UPDATE SET "
        "title = excluded.title, latitude = excluded.latitude, "
        "longitude = excluded.longitude, source = excluded.source, "
        "updated_at = excluded.updated_at",
        (
            building.address,
            building.title,
            building.latitude,
            building.longitude,
            source,
            now_iso(),
        ),
    )
    await conn.commit()


__all__ = [
    "BUILDINGS",
    "BUILDINGS_BY_ADDRESS",
    "REMOTE_ADDRESS",
    "Building",
    "building_coordinates",
    "building_for_date",
    "building_point",
    "building_title",
    "get_building",
    "list_buildings",
    "save_building",
    "seed_buildings",
]
