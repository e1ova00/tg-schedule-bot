"""Общие настройки для тестов: чтобы `import app` работал из любой папки запуска."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Чтобы `from fakes import ...` работал из любого тестового модуля.
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


@pytest.fixture(scope="session")
def root_router():
    """Корневой роутер, собранный ровно один раз за прогон.

    Роутеры хендлеров создаются на уровне модуля, и aiogram разрешает прикрепить
    такой роутер к родителю только однажды: второй вызов `build_root_router()`
    в том же процессе падает с RuntimeError «Router is already attached».
    В боевом коде функция вызывается один раз при старте, поэтому тесты тоже
    берут один общий экземпляр через эту фикстуру.
    """
    from app.handlers import build_root_router

    return build_root_router()


@pytest.fixture()
async def db() -> AsyncIterator[aiosqlite.Connection]:
    """Пустая база в оперативной памяти со схемой из `app/db.py`.

    Файл на диск не пишется, каждый тест получает свою чистую базу.
    """
    from app.db import connect, init_schema

    conn = await connect(":memory:")
    await init_schema(conn)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture()
def state() -> FSMContext:
    """Состояние диалога в памяти — тот же MemoryStorage, что и у боевого диспетчера."""
    from fakes import USER_ID

    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=USER_ID, user_id=USER_ID),
    )
