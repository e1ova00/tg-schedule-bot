"""Тесты логирования: app/logging_setup.py.

Проверяем то, что обещано в приёмке этапа 1: логи пишутся и в консоль, и в файл,
время — по Москве, русские буквы не ломаются, повторный запуск не дублирует строки.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.logging_setup import ConsoleFormatter, MoscowFormatter, setup_logging

MOSCOW = ZoneInfo("Europe/Moscow")


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """setup_logging переписывает корневой логгер — возвращаем его как было после теста."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


def make_record(message: str = "проверка") -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    # 2026-09-03 09:05:00 UTC = 12:05:00 по Москве.
    record.created = datetime(2026, 9, 3, 9, 5, 0, tzinfo=timezone.utc).timestamp()
    return record


def test_moscow_formatter_prints_moscow_time() -> None:
    """Сервер в UTC, а в логе должно стоять московское время."""
    formatted = MoscowFormatter(tz=MOSCOW).format(make_record())
    assert "2026-09-03 12:05:00" in formatted


def test_moscow_formatter_respects_other_timezone() -> None:
    formatted = MoscowFormatter(tz=ZoneInfo("UTC")).format(make_record())
    assert "2026-09-03 09:05:00" in formatted


def test_console_formatter_hides_traceback_but_keeps_it_for_file() -> None:
    """В консоли — одна строка, в файле — полный трейсбек той же записи."""
    try:
        raise ValueError("сломалось")
    except ValueError:
        import sys

        record = make_record("что-то пошло не так")
        record.exc_info = sys.exc_info()

    console_line = ConsoleFormatter(tz=MOSCOW).format(record)
    file_line = MoscowFormatter(tz=MOSCOW).format(record)

    assert "Traceback" not in console_line
    assert "что-то пошло не так" in console_line
    assert "Traceback" in file_line and "сломалось" in file_line


def test_setup_logging_creates_file_and_writes_russian(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "bot.log"

    setup_logging(level="INFO", tz=MOSCOW, log_file=log_file)
    logging.getLogger("app.test").info("Бот запущен")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_file.exists(), "Папка logs/ и файл bot.log должны создаваться сами"
    assert "Бот запущен" in log_file.read_text(encoding="utf-8")


def test_setup_logging_twice_does_not_duplicate_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "bot.log"

    setup_logging(level="INFO", tz=MOSCOW, log_file=log_file)
    setup_logging(level="INFO", tz=MOSCOW, log_file=log_file)
    logging.getLogger("app.test").info("однажды")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_file.read_text(encoding="utf-8").count("однажды") == 1


def test_setup_logging_applies_level(tmp_path: Path) -> None:
    """При LOG_LEVEL=WARNING отладочные строки в файл не попадают."""
    log_file = tmp_path / "logs" / "bot.log"

    setup_logging(level="WARNING", tz=MOSCOW, log_file=log_file)
    logger = logging.getLogger("app.test")
    logger.debug("мелочь")
    logger.warning("важное")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "мелочь" not in content
    assert "важное" in content
