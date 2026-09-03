"""Настройка логов: одновременно в консоль и в файл logs/bot.log, время по Москве."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import DEFAULT_TIMEZONE, PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "bot.log"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Файл логов не растёт бесконечно: 1 МБ на файл, три архива.
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3


class MoscowFormatter(logging.Formatter):
    """Форматтер, который печатает время в заданном часовом поясе, а не в UTC сервера."""

    def __init__(
        self,
        fmt: str = LOG_FORMAT,
        datefmt: str = DATE_FORMAT,
        tz: ZoneInfo | None = None,
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._tz = tz or ZoneInfo(DEFAULT_TIMEZONE)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        # record.created — timestamp в UTC, переводим его в нужную зону (без naive datetime).
        moment = datetime.fromtimestamp(record.created, tz=self._tz)
        return moment.strftime(datefmt or DATE_FORMAT)


class ConsoleFormatter(MoscowFormatter):
    """В консоль пишем только сообщение: длинный трейсбек уезжает в файл.

    Пользователь бота — не программист, ему полезнее короткая строка,
    а полный разбор всегда лежит в logs/bot.log.
    """

    def format(self, record: logging.LogRecord) -> str:
        saved = (record.exc_info, record.exc_text, record.stack_info)
        record.exc_info, record.exc_text, record.stack_info = None, None, None
        try:
            return super().format(record)
        finally:
            # Возвращаем как было — эту же запись ещё будет форматировать файловый хендлер.
            record.exc_info, record.exc_text, record.stack_info = saved


def setup_logging(
    level: str = "INFO",
    tz: ZoneInfo | None = None,
    log_file: Path = LOG_FILE,
) -> None:
    """Включает вывод логов в консоль и в файл. Папку logs/ создаёт сама."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = MoscowFormatter(tz=tz)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(ConsoleFormatter(tz=tz))

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Повторный вызов не должен плодить дубли строк в логе.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # aiohttp на каждом long polling пишет служебные строки — они тут не нужны.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def ensure_utf8_console() -> None:
    """На Windows консоль часто в cp1251 — русские логи ломаются. Просим UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # Не критично: логи в файле всё равно в UTF-8.
            pass
