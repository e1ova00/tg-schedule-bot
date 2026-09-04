"""Тесты хранилища: схема SQLite, кэш координат корпусов и выбор корпуса на день.

Сеть не нужна: координаты трёх корпусов зашиты константами в `app/buildings.py`,
а база создаётся временная — либо в памяти (фикстура `db`), либо в tmp-папке теста.

Источник истины для ожиданий — `CLAUDE.md`, правило 3: корпусов три, ехать надо до
корпуса первой очной пары конкретного дня, и во вторник он зависит от чётности недели.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from app.buildings import (
    BUILDINGS,
    BUILDINGS_BY_ADDRESS,
    REMOTE_ADDRESS,
    Building,
    building_coordinates,
    building_for_date,
    building_title,
    get_building,
    list_buildings,
    save_building,
    seed_buildings,
)
from app.db import connect, init_schema, now_iso, open_database
from app.schedule import load_lessons

# Календарь тот же, что в tests/test_schedule.py: 31.08–06.09.2026 — числитель.
MON_ODD = date(2026, 8, 31)
TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
THU_ODD = date(2026, 9, 3)
FRI_ODD = date(2026, 9, 4)

MON_EVEN = date(2026, 9, 7)
TUE_EVEN = date(2026, 9, 8)
FRI_EVEN = date(2026, 9, 11)
SAT_EVEN = date(2026, 9, 12)

VOZNESENSKY = "пр. Вознесенский, д. 46"
SADOVAYA = "ул. Садовая, д. 54"
BOLSHAYA_MORSKAYA = "ул. Большая Морская, д. 18"

# Санкт-Петербург целиком помещается в эту рамку. Проверка грубая, но ловит перепутанные
# местами широту и долготу и «уехавшую» на другой континент запятую.
SPB_LATITUDE = (59.6, 60.2)
SPB_LONGITUDE = (29.5, 30.8)


async def table_names(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
        return {str(row["name"]) for row in await cur.fetchall()}


async def count_rows(conn: aiosqlite.Connection, table: str) -> int:
    async with conn.execute(f"SELECT COUNT(*) AS n FROM {table}") as cur:  # noqa: S608
        row = await cur.fetchone()
    return int(row["n"])


# ======================================================================================
# 1. Схема базы
# ======================================================================================


async def test_schema_creates_both_tables(db: aiosqlite.Connection) -> None:
    assert {"users", "buildings"} <= await table_names(db)


async def test_init_schema_is_idempotent(db: aiosqlite.Connection) -> None:
    """Схема накатывается при каждом старте бота — второй прогон не должен падать."""
    await init_schema(db)
    await init_schema(db)

    assert {"users", "buildings"} <= await table_names(db)


async def test_open_database_creates_file_and_folders(tmp_path: Path) -> None:
    """Путь из .env может указывать на ещё не существующую папку — её создаём сами."""
    db_path = tmp_path / "новая-папка" / "bot.db"

    conn = await open_database(db_path)
    try:
        assert db_path.exists()
        assert {"users", "buildings"} <= await table_names(conn)
    finally:
        await conn.close()


async def test_data_survives_reopening_the_file(tmp_path: Path) -> None:
    """Перезапуск бота не должен терять настройки: файл открывается, а не создаётся заново."""
    db_path = tmp_path / "bot.db"

    first = await open_database(db_path)
    await seed_buildings(first)
    await first.close()

    second = await open_database(db_path)
    try:
        assert await count_rows(second, "buildings") == len(BUILDINGS)
    finally:
        await second.close()


async def test_rows_are_readable_by_column_name(db: aiosqlite.Connection) -> None:
    """row_factory настроен: код читает поля по имени, а не по порядку столбцов."""
    await seed_buildings(db)

    async with db.execute("SELECT address, latitude FROM buildings LIMIT 1") as cur:
        row = await cur.fetchone()

    assert row["address"]
    assert isinstance(row["latitude"], float)


async def test_connect_does_not_touch_disk_for_memory_database() -> None:
    """`:memory:` — особое имя SQLite: файла с таким именем на диске появиться не должно."""
    conn = await connect(":memory:")
    try:
        assert not Path(":memory:").exists()
    finally:
        await conn.close()


def test_now_iso_is_moscow_time_with_offset() -> None:
    """Naive-строк в базе быть не должно, иначе на сервере в UTC время «уедет» на 3 часа."""
    stamp = now_iso()
    parsed = datetime.fromisoformat(stamp)

    assert parsed.utcoffset() == timedelta(hours=3)
    assert parsed.microsecond == 0
    assert stamp.endswith("+03:00")


# ======================================================================================
# 2. Кэш координат корпусов: seed_buildings
# ======================================================================================


async def test_seed_buildings_writes_three_buildings(db: aiosqlite.Connection) -> None:
    added = await seed_buildings(db)

    assert added == 3
    assert await count_rows(db, "buildings") == 3
    addresses = {item.address for item in await list_buildings(db)}
    assert addresses == {VOZNESENSKY, SADOVAYA, BOLSHAYA_MORSKAYA}


async def test_seed_buildings_is_idempotent(db: aiosqlite.Connection) -> None:
    """Засев идёт при каждом старте: второй вызов не дублирует строки и не падает."""
    await seed_buildings(db)

    added_again = await seed_buildings(db)

    assert added_again == 0
    assert await count_rows(db, "buildings") == 3


async def test_seed_buildings_keeps_already_saved_coordinates(
    db: aiosqlite.Connection,
) -> None:
    """INSERT OR IGNORE: если координаты уточнил геокодер, засев их не затрёт."""
    await seed_buildings(db)
    await save_building(
        db,
        Building(address=SADOVAYA, title="Садовая, 54", latitude=59.9, longitude=30.3),
        source="geocoder",
    )

    await seed_buildings(db)

    saved = await get_building(db, SADOVAYA)
    assert saved is not None
    assert (saved.latitude, saved.longitude) == (59.9, 30.3)


async def test_save_building_updates_existing_row(db: aiosqlite.Connection) -> None:
    await seed_buildings(db)
    updated = Building(
        address=VOZNESENSKY, title="Вознесенский, 46", latitude=59.5, longitude=30.5
    )

    await save_building(db, updated)

    assert await count_rows(db, "buildings") == 3
    assert await get_building(db, VOZNESENSKY) == updated


async def test_get_building_returns_none_for_unknown_address(
    db: aiosqlite.Connection,
) -> None:
    await seed_buildings(db)

    assert await get_building(db, "ул. Несуществующая, д. 1") is None
    assert await get_building(db, REMOTE_ADDRESS) is None


async def test_remote_address_is_not_a_building() -> None:
    """«Дистанционное обучение» стоит в расписании вместо адреса — ехать туда некуда."""
    assert REMOTE_ADDRESS not in BUILDINGS_BY_ADDRESS
    assert building_coordinates(REMOTE_ADDRESS) is None


# ======================================================================================
# 3. Координаты: связь констант с расписанием
# ======================================================================================


def test_every_offline_address_from_schedule_has_coordinates() -> None:
    """Главная связка: адрес занятия — ключ к координатам. Опечатка сломает весь маршрут."""
    addresses = {
        lesson.building for lesson in load_lessons() if not lesson.is_remote
    }

    assert len(addresses) == 3
    for address in addresses:
        assert address in BUILDINGS_BY_ADDRESS, f"нет координат для адреса «{address}»"


@pytest.mark.parametrize("building", BUILDINGS, ids=lambda item: item.address)
def test_building_coordinates_are_in_saint_petersburg(building: Building) -> None:
    """Грубая проверка: точка должна лежать в пределах Петербурга, а не в океане."""
    assert SPB_LATITUDE[0] <= building.latitude <= SPB_LATITUDE[1]
    assert SPB_LONGITUDE[0] <= building.longitude <= SPB_LONGITUDE[1]


def test_buildings_are_three_distinct_points() -> None:
    points = {(item.latitude, item.longitude) for item in BUILDINGS}

    assert len(BUILDINGS) == 3
    assert len(points) == 3


def test_building_title_is_short_and_falls_back_to_address() -> None:
    assert building_title(SADOVAYA) == "Садовая, 54"
    assert building_title("ул. Неизвестная, д. 5") == "ул. Неизвестная, д. 5"
    assert building_title(None) == ""


# ======================================================================================
# 4. building_for_date: до какого корпуса ехать
# ======================================================================================


def test_tuesday_odd_goes_to_voznesensky() -> None:
    """CLAUDE.md, правило 3: во вторник по числителю первая пара на Вознесенском."""
    assert building_for_date(TUE_ODD) == VOZNESENSKY


def test_tuesday_even_goes_to_sadovaya() -> None:
    """А по знаменателю в тот же день недели — на Садовую."""
    assert building_for_date(TUE_EVEN) == SADOVAYA


def test_tuesday_destination_depends_on_parity() -> None:
    """Явно фиксируем требование: адрес назначения зависит от чётности недели."""
    assert building_for_date(TUE_ODD) != building_for_date(TUE_EVEN)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        pytest.param(MON_ODD, None, id="понедельник по числителю — весь дистант"),
        pytest.param(MON_EVEN, None, id="понедельник по знаменателю — весь дистант"),
        pytest.param(THU_ODD, None, id="четверг по числителю — пар нет"),
        pytest.param(FRI_EVEN, None, id="пятница по знаменателю — пар нет"),
        pytest.param(SAT_EVEN, None, id="суббота по знаменателю — пар нет"),
        pytest.param(date(2026, 9, 6), None, id="воскресенье"),
        pytest.param(WED_ODD, VOZNESENSKY, id="среда по числителю"),
        pytest.param(FRI_ODD, BOLSHAYA_MORSKAYA, id="пятница по числителю"),
    ],
)
def test_building_for_date_for_every_kind_of_day(day: date, expected: str | None) -> None:
    assert building_for_date(day) == expected


def test_building_for_date_accepts_custom_lessons() -> None:
    """Функцию можно звать со своим списком занятий — пригодится будильнику в тестах."""
    lessons = load_lessons()

    assert building_for_date(TUE_EVEN, lessons) == SADOVAYA


async def test_building_for_date_resolves_to_saved_coordinates(
    db: aiosqlite.Connection,
) -> None:
    """Полная цепочка этапа: дата → адрес корпуса → координаты из базы."""
    await seed_buildings(db)

    odd = await get_building(db, building_for_date(TUE_ODD) or "")
    even = await get_building(db, building_for_date(TUE_EVEN) or "")

    assert odd is not None and even is not None
    assert odd.address == VOZNESENSKY
    assert even.address == SADOVAYA
    assert (odd.latitude, odd.longitude) != (even.latitude, even.longitude)


async def test_all_destinations_of_two_weeks_are_known_to_database(
    db: aiosqlite.Connection,
) -> None:
    """Ни в один день двух недель бот не должен остаться без координат назначения."""
    await seed_buildings(db)

    for shift in range(14):
        day = MON_ODD + timedelta(days=shift)
        address = building_for_date(day)
        if address is None:
            continue
        assert await get_building(db, address) is not None, f"нет координат на {day}"


# ======================================================================================
# 5. Таблица users: ограничения на уровне базы
# ======================================================================================


async def test_transport_mode_check_rejects_unknown_value(
    db: aiosqlite.Connection,
) -> None:
    """CHECK в схеме — последняя защита: «на самокате» в базу не попадёт."""
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO users (telegram_id, transport_mode) VALUES (?, ?)",
            (1, "самокат"),
        )


@pytest.mark.parametrize("mode", ["car", "public_transport", None])
async def test_transport_mode_check_allows_known_values(
    db: aiosqlite.Connection, mode: str | None
) -> None:
    """None разрешён: пока человек не ответил, поле пустое."""
    await db.execute(
        "INSERT INTO users (telegram_id, transport_mode) VALUES (?, ?)", (1, mode)
    )

    assert await count_rows(db, "users") == 1


async def test_telegram_id_is_unique(db: aiosqlite.Connection) -> None:
    await db.execute("INSERT INTO users (telegram_id) VALUES (?)", (1,))

    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO users (telegram_id) VALUES (?)", (1,))
