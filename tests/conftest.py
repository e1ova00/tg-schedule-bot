"""Общие настройки для тестов: чтобы `import app` работал из любой папки запуска."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


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
