"""Чтение настроек из .env.

Модуль ничего не знает про Telegram и не ходит в сеть: на вход — переменные окружения,
на выход — объект `Config`. Так его легко проверить тестами.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

# Часовой пояс проекта. Сервер почти наверняка в UTC, поэтому Москва задана явно.
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_DB_PATH = "data/bot.db"
DEFAULT_LOG_LEVEL = "INFO"

_KNOWN_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
_KNOWN_PROXY_SCHEMES = ("socks4://", "socks5://", "http://", "https://")

# Корень проекта: app/config.py -> app -> tg-schedule-bot
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Ошибка настроек, которую пользователю нужно исправить руками в .env."""


@dataclass(frozen=True, slots=True)
class Config:
    """Настройки приложения, собранные в одном месте."""

    bot_token: str
    log_level: str
    db_path: Path
    timezone: ZoneInfo
    allowed_user_ids: tuple[int, ...]
    proxy_url: str | None


def parse_user_ids(raw: str | None) -> tuple[int, ...]:
    """Разбирает `ALLOWED_USER_IDS` вида "123, 456" в кортеж чисел.

    Пустое значение — не ошибка: разграничение доступа появится на этапе 3.
    """
    if not raw or not raw.strip():
        return ()

    user_ids: list[int] = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            user_ids.append(int(item))
        except ValueError as exc:
            raise ConfigError(
                f"В ALLOWED_USER_IDS попало значение «{item}», а там должны быть только "
                "числовые Telegram ID через запятую. Свой ID можно узнать у бота @userinfobot."
            ) from exc
    return tuple(user_ids)


def parse_log_level(raw: str | None) -> str:
    """Приводит LOG_LEVEL к валидному имени уровня логирования."""
    level = (raw or "").strip().upper()
    if not level:
        return DEFAULT_LOG_LEVEL
    if level not in _KNOWN_LOG_LEVELS:
        raise ConfigError(
            f"Непонятный LOG_LEVEL=«{raw}». Допустимые значения: "
            + ", ".join(_KNOWN_LOG_LEVELS)
            + "."
        )
    return level


def parse_timezone(raw: str | None) -> ZoneInfo:
    """Возвращает часовой пояс из TZ, при проблемах — Europe/Moscow."""
    name = (raw or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logging.getLogger(__name__).warning(
            "Часовой пояс «%s» из TZ не найден, работаем по %s.", name, DEFAULT_TIMEZONE
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def parse_proxy_url(raw: str | None) -> str | None:
    """Адрес прокси для соединения с Telegram. Пусто — прокси не используем.

    Нужен людям, у кого Telegram открывается только через прокси/VPN — тот же адрес,
    что настроен в системе для обычного Telegram, обычно подходит и здесь.
    """
    value = (raw or "").strip()
    if not value:
        return None
    if not value.lower().startswith(_KNOWN_PROXY_SCHEMES):
        raise ConfigError(
            f"Не понимаю адрес прокси «{raw}». Начните его с socks5://, socks4://, "
            "http:// или https://, например socks5://127.0.0.1:10808."
        )
    return value


def parse_db_path(raw: str | None, project_root: Path = PROJECT_ROOT) -> Path:
    """Путь к файлу SQLite. Относительный путь считается от корня проекта."""
    value = (raw or "").strip() or DEFAULT_DB_PATH
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def build_config(env: Mapping[str, str]) -> Config:
    """Собирает `Config` из словаря переменных окружения (без чтения файлов)."""
    token = (env.get("BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "Не найден BOT_TOKEN. Скопируйте .env.example в .env и впишите токен от @BotFather."
        )

    return Config(
        bot_token=token,
        log_level=parse_log_level(env.get("LOG_LEVEL")),
        db_path=parse_db_path(env.get("DB_PATH")),
        timezone=parse_timezone(env.get("TZ")),
        allowed_user_ids=parse_user_ids(env.get("ALLOWED_USER_IDS")),
        proxy_url=parse_proxy_url(env.get("TELEGRAM_PROXY_URL")),
    )


def load_config(env_file: Path | None = None) -> Config:
    """Читает .env (если он есть) и возвращает настройки приложения."""
    path = env_file if env_file is not None else PROJECT_ROOT / ".env"
    # override=False: если переменная уже задана в окружении (например, в Docker), она главнее.
    load_dotenv(dotenv_path=path, override=False)
    return build_config(os.environ)
