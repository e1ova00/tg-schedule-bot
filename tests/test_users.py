"""Тесты пользователя и его настроек: `app/users.py`.

Ни Telegram, ни сети: разбор минут — чистые функции, всё остальное работает с временной
базой в памяти (фикстура `db` из conftest).

Главное требование этапа: кривой ввод («полчаса», «-5», пустая строка) не должен ронять
бота с голым исключением — функции возвращают None, а хендлер вежливо переспрашивает.
"""

from __future__ import annotations

import sqlite3

import aiosqlite
import pytest

from app import users
from app.users import (
    BUFFER_MAX_MINUTES,
    BUFFER_MIN_MINUTES,
    BUFFER_OPTIONS,
    PREP_MAX_MINUTES,
    PREP_MIN_MINUTES,
    PREP_OPTIONS,
    TRANSPORT_CAR,
    TRANSPORT_PUBLIC,
    User,
    ensure_user,
    get_user,
    is_onboarded,
    list_users,
    mark_onboarded,
    normalize_transport,
    parse_buffer_minutes,
    parse_minutes,
    parse_prep_minutes,
    save_buffer_minutes,
    save_location,
    save_prep_minutes,
    save_transport_mode,
    save_user_fields,
)
from fakes import USER_ID, make_onboarded_user


def full_user(**overrides: object) -> User:
    """Пользователь, у которого заполнено всё. Тесты гасят по одному полю."""
    defaults: dict[str, object] = {
        "telegram_id": USER_ID,
        "username": "tester",
        "latitude": 59.9386,
        "longitude": 30.3141,
        "transport_mode": TRANSPORT_CAR,
        "prep_minutes": 30,
        "buffer_minutes": 10,
        "onboarded_at": "2026-09-04T10:15:00+03:00",
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


# ======================================================================================
# 1. Создание и поиск пользователя
# ======================================================================================


async def test_get_user_returns_none_for_stranger(db: aiosqlite.Connection) -> None:
    assert await get_user(db, 12345) is None


async def test_ensure_user_creates_record(db: aiosqlite.Connection) -> None:
    user = await ensure_user(db, USER_ID, username="tester")

    assert user.telegram_id == USER_ID
    assert user.username == "tester"
    assert user.created_at and user.updated_at
    assert await get_user(db, USER_ID) == user


async def test_ensure_user_does_not_duplicate(db: aiosqlite.Connection) -> None:
    """Каждый /start зовёт ensure_user — второй раз должна остаться одна строка."""
    first = await ensure_user(db, USER_ID, username="tester")

    second = await ensure_user(db, USER_ID, username="tester")

    assert len(await list_users(db)) == 1
    assert second.created_at == first.created_at
    assert second.updated_at == first.updated_at  # ничего не менялось — время не дёргаем


async def test_ensure_user_keeps_saved_settings(db: aiosqlite.Connection) -> None:
    """Повторный /start не должен обнулять уже сохранённые ответы."""
    await save_prep_minutes(db, USER_ID, 45)

    user = await ensure_user(db, USER_ID, username="tester")

    assert user.prep_minutes == 45


async def test_ensure_user_updates_changed_username(db: aiosqlite.Connection) -> None:
    await ensure_user(db, USER_ID, username="старый")

    user = await ensure_user(db, USER_ID, username="новый")

    assert user.username == "новый"


async def test_ensure_user_survives_missing_username(db: aiosqlite.Connection) -> None:
    """У человека может не быть @username — это не повод падать."""
    user = await ensure_user(db, USER_ID, username=None)

    assert user.username is None
    assert (await ensure_user(db, USER_ID)).telegram_id == USER_ID


async def test_users_are_independent(db: aiosqlite.Connection) -> None:
    """Пользователей двое-трое, и настройки одного не должны трогать другого."""
    await save_prep_minutes(db, 1, 20)
    await save_prep_minutes(db, 2, 50)

    assert (await get_user(db, 1)).prep_minutes == 20  # type: ignore[union-attr]
    assert (await get_user(db, 2)).prep_minutes == 50  # type: ignore[union-attr]


# ======================================================================================
# 2. Запись полей: белый список
# ======================================================================================


async def test_save_user_fields_writes_allowed_fields(db: aiosqlite.Connection) -> None:
    user = await save_user_fields(
        db,
        USER_ID,
        latitude=59.9386,
        longitude=30.3141,
        transport_mode=TRANSPORT_PUBLIC,
        prep_minutes=25,
        buffer_minutes=5,
    )

    assert user.coordinates == (59.9386, 30.3141)
    assert user.transport_mode == TRANSPORT_PUBLIC
    assert (user.prep_minutes, user.buffer_minutes) == (25, 5)


async def test_save_user_fields_creates_missing_user(db: aiosqlite.Connection) -> None:
    """Сохранить ответ можно и до того, как кто-то позвал ensure_user явно."""
    user = await save_user_fields(db, 777, prep_minutes=15)

    assert user.telegram_id == 777
    assert user.prep_minutes == 15


@pytest.mark.parametrize(
    "field",
    [
        pytest.param("created_at", id="служебное поле"),
        pytest.param("updated_at", id="время правки бот ставит сам"),
        pytest.param("is_admin", id="поля вообще нет в таблице"),
        pytest.param("prep_minutes = 1, username", id="попытка подставить свой SQL"),
    ],
)
async def test_save_user_fields_rejects_unknown_field(
    db: aiosqlite.Connection, field: str
) -> None:
    """Имена полей подставляются в SQL, поэтому список закрытый — иначе это дыра."""
    with pytest.raises(ValueError) as exc:
        await save_user_fields(db, USER_ID, **{field: "что-нибудь"})

    assert field in str(exc.value)
    assert "Неизвестные поля" in str(exc.value)


async def test_save_user_fields_rejects_before_writing_anything(
    db: aiosqlite.Connection,
) -> None:
    """Запрещённое поле в списке — не пишем и разрешённые из того же вызова."""
    await save_prep_minutes(db, USER_ID, 30)

    with pytest.raises(ValueError):
        await save_user_fields(db, USER_ID, prep_minutes=99, is_admin=True)

    user = await get_user(db, USER_ID)
    assert user is not None
    assert user.prep_minutes == 30


async def test_save_user_fields_without_fields_returns_user(
    db: aiosqlite.Connection,
) -> None:
    user = await save_user_fields(db, USER_ID)

    assert user.telegram_id == USER_ID


async def test_save_user_fields_updates_timestamp(db: aiosqlite.Connection) -> None:
    created = await ensure_user(db, USER_ID)

    updated = await save_user_fields(db, USER_ID, prep_minutes=30)

    assert updated.created_at == created.created_at
    assert updated.updated_at is not None


# ======================================================================================
# 3. Отдельные настройки
# ======================================================================================


async def test_save_location_stores_coordinates(db: aiosqlite.Connection) -> None:
    user = await save_location(db, USER_ID, 59.85, 30.25)

    assert user.has_location is True
    assert user.coordinates == (59.85, 30.25)


async def test_save_location_overwrites_previous_point(db: aiosqlite.Connection) -> None:
    """Переехал — прислал новую точку, старая не должна остаться."""
    await save_location(db, USER_ID, 59.85, 30.25)

    user = await save_location(db, USER_ID, 60.0, 30.4)

    assert user.coordinates == (60.0, 30.4)


@pytest.mark.parametrize("mode", [TRANSPORT_CAR, TRANSPORT_PUBLIC])
async def test_save_transport_mode_accepts_known_modes(
    db: aiosqlite.Connection, mode: str
) -> None:
    user = await save_transport_mode(db, USER_ID, mode)

    assert user.transport_mode == mode


@pytest.mark.parametrize("mode", ["самокат", "CAR", "", "  ", "пешком"])
async def test_save_transport_mode_rejects_unknown(
    db: aiosqlite.Connection, mode: str
) -> None:
    with pytest.raises(ValueError):
        await save_transport_mode(db, USER_ID, mode)

    user = await get_user(db, USER_ID)
    assert user is None or user.transport_mode is None


async def test_database_check_guards_transport_mode(db: aiosqlite.Connection) -> None:
    """Даже в обход save_transport_mode мусор в поле не попадёт: в схеме стоит CHECK."""
    with pytest.raises(sqlite3.IntegrityError):
        await save_user_fields(db, USER_ID, transport_mode="самокат")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("car", TRANSPORT_CAR),
        ("public_transport", TRANSPORT_PUBLIC),
        (" car ", TRANSPORT_CAR),
        ("Car", None),
        ("велосипед", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_transport(raw: str | None, expected: str | None) -> None:
    assert normalize_transport(raw) == expected


async def test_save_minutes_round_trip(db: aiosqlite.Connection) -> None:
    await save_prep_minutes(db, USER_ID, 35)
    user = await save_buffer_minutes(db, USER_ID, 15)

    assert user.prep_minutes == 35
    assert user.buffer_minutes == 15


async def test_mark_onboarded_sets_stamp(db: aiosqlite.Connection) -> None:
    await save_user_fields(
        db,
        USER_ID,
        latitude=59.9,
        longitude=30.3,
        transport_mode=TRANSPORT_CAR,
        prep_minutes=30,
        buffer_minutes=10,
    )

    user = await mark_onboarded(db, USER_ID)

    assert user.onboarded_at
    assert user.onboarded_at.endswith("+03:00")
    assert is_onboarded(user) is True


# ======================================================================================
# 4. is_onboarded
# ======================================================================================


def test_is_onboarded_true_when_everything_filled() -> None:
    assert is_onboarded(full_user()) is True


def test_is_onboarded_false_for_unknown_user() -> None:
    assert is_onboarded(None) is False


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param({"onboarded_at": None}, id="диалог не доведён до конца"),
        pytest.param({"onboarded_at": ""}, id="пустая отметка о завершении"),
        pytest.param({"latitude": None}, id="нет широты"),
        pytest.param({"longitude": None}, id="нет долготы"),
        pytest.param({"transport_mode": None}, id="нет транспорта"),
        pytest.param({"prep_minutes": None}, id="нет времени на сборы"),
        pytest.param({"buffer_minutes": None}, id="нет запаса"),
    ],
)
def test_is_onboarded_false_while_something_is_missing(
    missing: dict[str, object],
) -> None:
    """Пустое поле = будильник не посчитать, значит знакомство ещё не закончено."""
    assert is_onboarded(full_user(**missing)) is False


def test_is_onboarded_false_for_broken_transport_mode() -> None:
    """Если в базе руками поправили транспорт на мусор, настройки считаем неполными."""
    assert is_onboarded(full_user(transport_mode="самолёт")) is False


def test_is_onboarded_accepts_zero_minutes() -> None:
    """Ноль — законный ответ («собираюсь мгновенно»), а не «не отвечал»."""
    assert is_onboarded(full_user(prep_minutes=0, buffer_minutes=0)) is True


async def test_is_onboarded_walks_through_the_whole_dialog(
    db: aiosqlite.Connection,
) -> None:
    """Пошагово: True появляется только после последнего шага."""
    await ensure_user(db, USER_ID, username="tester")
    assert is_onboarded(await get_user(db, USER_ID)) is False

    await save_location(db, USER_ID, 59.9386, 30.3141)
    assert is_onboarded(await get_user(db, USER_ID)) is False

    await save_transport_mode(db, USER_ID, TRANSPORT_CAR)
    assert is_onboarded(await get_user(db, USER_ID)) is False

    await save_prep_minutes(db, USER_ID, 30)
    assert is_onboarded(await get_user(db, USER_ID)) is False

    await save_buffer_minutes(db, USER_ID, 10)
    assert is_onboarded(await get_user(db, USER_ID)) is False  # нет отметки о завершении

    await mark_onboarded(db, USER_ID)
    assert is_onboarded(await get_user(db, USER_ID)) is True


async def test_helper_makes_fully_onboarded_user(db: aiosqlite.Connection) -> None:
    """Страховка для остальных тестов: вспомогательная функция и правда всё заполняет."""
    user = await make_onboarded_user(db)

    assert is_onboarded(user) is True


# ======================================================================================
# 5. Разбор минут: границы, мусор, пустая строка
# ======================================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("30", 30, id="просто число"),
        pytest.param(" 30 ", 30, id="лишние пробелы"),
        pytest.param("30 минут", 30, id="число со словом"),
        pytest.param("примерно 25 мин", 25, id="число внутри фразы"),
        pytest.param("0", 0, id="ноль — законный ответ"),
        pytest.param("180", PREP_MAX_MINUTES, id="верхняя граница включительно"),
    ],
)
def test_parse_prep_minutes_accepts_reasonable_input(raw: str, expected: int) -> None:
    assert parse_prep_minutes(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("181", id="чуть выше верхней границы"),
        pytest.param("300", id="явная опечатка"),
        pytest.param("-5", id="отрицательное число"),
        pytest.param("-0", id="минус ноль — тоже ввод с минусом"),
        pytest.param("скоро", id="слово вместо числа"),
        pytest.param("полчаса", id="человеческое «полчаса»"),
        pytest.param("тридцать", id="число прописью"),
        pytest.param("", id="пустая строка"),
        pytest.param("   ", id="одни пробелы"),
        pytest.param("30.5", id="дробное число"),
        pytest.param("20 30", id="два числа сразу"),
        pytest.param("🙂", id="эмодзи"),
        pytest.param(None, id="сообщение вообще без текста"),
    ],
)
def test_parse_prep_minutes_returns_none_for_bad_input(raw: str | None) -> None:
    """None вместо исключения: хендлер должен переспросить, а не уронить диалог."""
    assert parse_prep_minutes(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("0", 0, id="нижняя граница"),
        pytest.param("10", 10, id="обычный ответ"),
        pytest.param("60", BUFFER_MAX_MINUTES, id="верхняя граница включительно"),
    ],
)
def test_parse_buffer_minutes_accepts_range(raw: str, expected: int) -> None:
    assert parse_buffer_minutes(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["61", "90", "180", "-1", "чуть-чуть", "", None],
)
def test_parse_buffer_minutes_returns_none_for_bad_input(raw: str | None) -> None:
    assert parse_buffer_minutes(raw) is None


def test_buffer_range_is_narrower_than_prep_range() -> None:
    """Запас — это подушка на неожиданности, а не второй сон: границы разные."""
    assert BUFFER_MAX_MINUTES < PREP_MAX_MINUTES
    assert parse_prep_minutes("90") == 90
    assert parse_buffer_minutes("90") is None


def test_parse_minutes_respects_custom_limits() -> None:
    assert parse_minutes("5", minimum=10, maximum=20) is None
    assert parse_minutes("15", minimum=10, maximum=20) == 15
    assert parse_minutes(None, minimum=0, maximum=10) is None


@pytest.mark.parametrize("value", PREP_OPTIONS)
def test_prep_buttons_offer_valid_values(value: int) -> None:
    """Кнопка-подсказка не должна предлагать то, что потом не примет разбор."""
    assert parse_prep_minutes(str(value)) == value
    assert PREP_MIN_MINUTES <= value <= PREP_MAX_MINUTES


@pytest.mark.parametrize("value", BUFFER_OPTIONS)
def test_buffer_buttons_offer_valid_values(value: int) -> None:
    assert parse_buffer_minutes(str(value)) == value
    assert BUFFER_MIN_MINUTES <= value <= BUFFER_MAX_MINUTES


def test_public_api_is_exported() -> None:
    """Хендлеры и будущий будильник берут эти имена из пакета."""
    for name in (
        "ensure_user",
        "get_user",
        "is_onboarded",
        "save_user_fields",
        "parse_prep_minutes",
        "parse_buffer_minutes",
        "list_users",
    ):
        assert hasattr(users, name)
