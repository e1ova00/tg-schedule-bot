"""Текущее время проекта. Всегда с часовым поясом, никаких naive datetime.

Отдельный модуль нужен, чтобы «сейчас» можно было подменить в тестах и чтобы
бизнес-логика принимала дату параметром, а не звала `datetime.now()` внутри себя.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import DEFAULT_TIMEZONE

MOSCOW = ZoneInfo(DEFAULT_TIMEZONE)


def now(tz: ZoneInfo = MOSCOW) -> datetime:
    """Текущий момент в нужном поясе (по умолчанию — Москва)."""
    return datetime.now(tz)


def today(tz: ZoneInfo = MOSCOW) -> date:
    """Сегодняшняя дата по московскому времени.

    Важно на сервере в UTC: с 21:00 UTC там уже «завтра» по Москве.
    """
    return now(tz).date()


__all__ = ["MOSCOW", "now", "today"]
