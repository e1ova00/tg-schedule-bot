"""Пользователь и его настройки: чтение/запись в SQLite и разбор введённых чисел.

Про Telegram модуль ничего не знает: на вход — соединение aiosqlite и обычные значения,
на выход — датакласс `User`. Разбор минут — вообще чистые функции, их можно звать
без базы.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import aiosqlite

from app.db import now_iso

logger = logging.getLogger(__name__)

TRANSPORT_CAR = "car"
TRANSPORT_PUBLIC = "public_transport"
TRANSPORT_MODES: tuple[str, ...] = (TRANSPORT_CAR, TRANSPORT_PUBLIC)

# Разумные границы. Верхние взяты «с запасом»: они защищают от опечатки вроде 300 минут,
# а не диктуют пользователю, как ему собираться.
PREP_MIN_MINUTES = 0
PREP_MAX_MINUTES = 180
BUFFER_MIN_MINUTES = 0
BUFFER_MAX_MINUTES = 60

# Подсказки-кнопки в диалоге.
PREP_OPTIONS: tuple[int, ...] = (10, 20, 30, 45)
BUFFER_OPTIONS: tuple[int, ...] = (5, 10, 15, 20)

_UPDATABLE_FIELDS = frozenset(
    {
        "username",
        "latitude",
        "longitude",
        "transport_mode",
        "prep_minutes",
        "buffer_minutes",
        "onboarded_at",
    }
)

# Одно число в строке, вокруг него — любой текст без цифр («примерно 25 мин»).
# Минус в начало не пускаем: «-5» должно считаться ошибкой ввода, а не пятью минутами.
_NUMBER_RE = re.compile(r"^[^\d\-]*(\d{1,4})\D*$")


@dataclass(frozen=True, slots=True)
class User:
    """Строка таблицы `users`. Пустые поля означают «пользователь ещё не ответил»."""

    telegram_id: int
    username: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    transport_mode: str | None = None
    prep_minutes: int | None = None
    buffer_minutes: int | None = None
    onboarded_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def coordinates(self) -> tuple[float, float] | None:
        if self.latitude is None or self.longitude is None:
            return None
        return (self.latitude, self.longitude)


# --- Чистые функции -----------------------------------------------------------------


def is_onboarded(user: User | None) -> bool:
    """Прошёл ли пользователь настройку до конца.

    Проверяем не только отметку `onboarded_at`, но и сами данные: если кто-то руками
    почистит координаты в базе, будильник считать будет не от чего.
    """
    if user is None or not user.onboarded_at:
        return False
    return (
        user.has_location
        and user.transport_mode in TRANSPORT_MODES
        and user.prep_minutes is not None
        and user.buffer_minutes is not None
    )


def parse_minutes(raw: str | None, *, minimum: int, maximum: int) -> int | None:
    """Достаёт число минут из пользовательского текста или возвращает None.

    Принимает «30», « 30 », «30 минут», «примерно 25 мин». Всё остальное — None,
    чтобы хендлер мог вежливо переспросить, а не упасть.
    """
    if raw is None:
        return None
    match = _NUMBER_RE.match(raw.strip())
    if match is None:
        return None
    value = int(match.group(1))
    return value if minimum <= value <= maximum else None


def parse_prep_minutes(raw: str | None) -> int | None:
    """Время на сборы, 0–180 минут."""
    return parse_minutes(raw, minimum=PREP_MIN_MINUTES, maximum=PREP_MAX_MINUTES)


def parse_buffer_minutes(raw: str | None) -> int | None:
    """Запас на непредвиденное, 0–60 минут."""
    return parse_minutes(raw, minimum=BUFFER_MIN_MINUTES, maximum=BUFFER_MAX_MINUTES)


def normalize_transport(raw: str | None) -> str | None:
    """Приводит способ передвижения к одному из допустимых значений или None."""
    value = (raw or "").strip()
    return value if value in TRANSPORT_MODES else None


# --- Работа с таблицей users --------------------------------------------------------


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        telegram_id=int(row["telegram_id"]),
        username=row["username"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        transport_mode=row["transport_mode"],
        prep_minutes=row["prep_minutes"],
        buffer_minutes=row["buffer_minutes"],
        onboarded_at=row["onboarded_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def get_user(conn: aiosqlite.Connection, telegram_id: int) -> User | None:
    """Пользователь по telegram_id или None, если его ещё нет в базе."""
    async with conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_user(row) if row else None


async def ensure_user(
    conn: aiosqlite.Connection, telegram_id: int, username: str | None = None
) -> User:
    """Создаёт запись, если её нет, и обновляет username, если тот поменялся."""
    stamp = now_iso()
    await conn.execute(
        "INSERT INTO users (telegram_id, username, created_at, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(telegram_id) DO NOTHING",
        (telegram_id, username, stamp, stamp),
    )
    if username is not None:
        # Пишем только при реальном изменении, чтобы updated_at не дёргался на каждый /start.
        await conn.execute(
            "UPDATE users SET username = ?, updated_at = ? "
            "WHERE telegram_id = ? AND (username IS NULL OR username <> ?)",
            (username, stamp, telegram_id, username),
        )
    await conn.commit()

    user = await get_user(conn, telegram_id)
    assert user is not None  # только что вставили — строка обязана быть
    return user


async def save_user_fields(
    conn: aiosqlite.Connection, telegram_id: int, **fields: object
) -> User:
    """Обновляет перечисленные поля пользователя и возвращает свежую запись.

    Имена полей сверяются с белым списком: в SQL подставляются только они, значения —
    всегда через параметры.
    """
    unknown = sorted(set(fields) - _UPDATABLE_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля пользователя: {', '.join(unknown)}")

    await ensure_user(conn, telegram_id)
    if not fields:
        user = await get_user(conn, telegram_id)
        assert user is not None
        return user

    assignments = ", ".join(f"{name} = ?" for name in fields)
    await conn.execute(
        f"UPDATE users SET {assignments}, updated_at = ? WHERE telegram_id = ?",  # noqa: S608
        (*fields.values(), now_iso(), telegram_id),
    )
    await conn.commit()

    user = await get_user(conn, telegram_id)
    assert user is not None
    return user


async def save_location(
    conn: aiosqlite.Connection, telegram_id: int, latitude: float, longitude: float
) -> User:
    """Запоминает точку, от которой считаем дорогу до корпуса."""
    return await save_user_fields(
        conn, telegram_id, latitude=float(latitude), longitude=float(longitude)
    )


async def save_transport_mode(
    conn: aiosqlite.Connection, telegram_id: int, transport_mode: str
) -> User:
    mode = normalize_transport(transport_mode)
    if mode is None:
        raise ValueError(f"Недопустимый способ передвижения: {transport_mode!r}")
    return await save_user_fields(conn, telegram_id, transport_mode=mode)


async def save_prep_minutes(
    conn: aiosqlite.Connection, telegram_id: int, minutes: int
) -> User:
    return await save_user_fields(conn, telegram_id, prep_minutes=int(minutes))


async def save_buffer_minutes(
    conn: aiosqlite.Connection, telegram_id: int, minutes: int
) -> User:
    return await save_user_fields(conn, telegram_id, buffer_minutes=int(minutes))


async def mark_onboarded(conn: aiosqlite.Connection, telegram_id: int) -> User:
    """Ставит отметку о завершённой настройке."""
    return await save_user_fields(conn, telegram_id, onboarded_at=now_iso())


async def list_users(conn: aiosqlite.Connection) -> list[User]:
    """Все пользователи. Понадобится будильнику на этапе 5."""
    async with conn.execute("SELECT * FROM users ORDER BY telegram_id") as cursor:
        rows = await cursor.fetchall()
    return [_row_to_user(row) for row in rows]


__all__ = [
    "BUFFER_MAX_MINUTES",
    "BUFFER_MIN_MINUTES",
    "BUFFER_OPTIONS",
    "PREP_MAX_MINUTES",
    "PREP_MIN_MINUTES",
    "PREP_OPTIONS",
    "TRANSPORT_CAR",
    "TRANSPORT_MODES",
    "TRANSPORT_PUBLIC",
    "User",
    "ensure_user",
    "get_user",
    "is_onboarded",
    "list_users",
    "mark_onboarded",
    "normalize_transport",
    "parse_buffer_minutes",
    "parse_minutes",
    "parse_prep_minutes",
    "save_buffer_minutes",
    "save_location",
    "save_prep_minutes",
    "save_transport_mode",
    "save_user_fields",
]
