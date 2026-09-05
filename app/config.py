"""Чтение настроек из .env.

Модуль ничего не знает про Telegram и не ходит в сеть: на вход — переменные окружения,
на выход — объект `Config`. Так его легко проверить тестами.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

# Часовой пояс проекта. Сервер почти наверняка в UTC, поэтому Москва задана явно.
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_DB_PATH = "data/bot.db"
DEFAULT_LOG_LEVEL = "INFO"

# Маршрутизаторы. 2ГИС — основной (умеет пробки), OpenRouteService — запасной.
ROUTER_DGIS = "dgis"
ROUTER_ORS = "ors"
KNOWN_ROUTERS = (ROUTER_DGIS, ROUTER_ORS)
DEFAULT_ROUTER = ROUTER_DGIS

# Во сколько раз дорога дольше в час пик. Нужен только запасному маршрутизатору:
# пробок он не знает, поэтому «утренний час» закладывается коэффициентом.
DEFAULT_ORS_PEAK_HOUR_FACTOR = 1.4

# Будильник. Рано утром бот считает маршруты на сегодня и ставит задачи на точное время
# подъёма. Час должен быть заведомо раньше самого раннего подъёма: то, что уже прошло,
# планировщик может только досрочно отправить, но не «отмотать назад».
DEFAULT_ALARM_PLANNING_TIME = time(5, 0)

# Сколько минут считать «дорогой», если сервис маршрутов не ответил. Лучше разбудить по
# грубой оценке с честной оговоркой, чем не разбудить вовсе.
DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES = 60

# Во сколько утром напоминать про незакрытые заметки в день самой пары. Раньше первой
# пары (10:05) и заметно раньше самого позднего разумного выхода из дома.
DEFAULT_NOTES_MORNING_TIME = time(8, 0)

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
    # Маршруты. Ключей может не быть вовсе — тогда бот считает дорогу грубо, по прямой,
    # и честно предупреждает об этом в ответе. Падать из-за пустого ключа он не должен.
    router: str = DEFAULT_ROUTER
    dgis_api_key: str | None = None
    ors_api_key: str | None = None
    ors_peak_hour_factor: float = DEFAULT_ORS_PEAK_HOUR_FACTOR
    # Будильник: когда планировать утро и чем заменить время в пути, если сеть подвела.
    alarm_planning_time: time = DEFAULT_ALARM_PLANNING_TIME
    alarm_fallback_travel_minutes: int = DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES
    # Заметки: во сколько утром напомнить про домашку к сегодняшней паре.
    notes_morning_time: time = DEFAULT_NOTES_MORNING_TIME


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


def parse_router(raw: str | None) -> str:
    """Какой сервис маршрутов использовать: «dgis» или «ors». Пусто — 2ГИС."""
    value = (raw or "").strip().lower()
    if not value:
        return DEFAULT_ROUTER
    if value not in KNOWN_ROUTERS:
        raise ConfigError(
            f"Непонятное значение ROUTER=«{raw}». Допустимо только "
            + " или ".join(KNOWN_ROUTERS)
            + ": dgis — 2ГИС (умеет пробки), ors — OpenRouteService (запасной, без пробок)."
        )
    return value


def parse_api_key(raw: str | None) -> str | None:
    """Ключ внешнего сервиса. Пустая строка и пробелы — это «ключа нет», а не ошибка."""
    value = (raw or "").strip()
    return value or None


def parse_peak_hour_factor(raw: str | None) -> float:
    """Коэффициент часа пик для запасного маршрутизатора.

    Меньше или ровно 1.0 не имеет смысла: в час пик дорога не бывает быстрее обычной,
    а «1.0» просто молча отключил бы поправку и незаметно сдвинул будильник.
    """
    value = (raw or "").strip().replace(",", ".")
    if not value:
        return DEFAULT_ORS_PEAK_HOUR_FACTOR
    try:
        factor = float(value)
    except ValueError as exc:
        raise ConfigError(
            f"ORS_PEAK_HOUR_FACTOR=«{raw}» — это не число. Впишите, во сколько раз дорога "
            f"дольше в час пик, например {DEFAULT_ORS_PEAK_HOUR_FACTOR}."
        ) from exc
    if factor <= 1.0:
        raise ConfigError(
            f"ORS_PEAK_HOUR_FACTOR=«{raw}» должен быть больше 1.0: в час пик дорога дольше, "
            f"а не короче. Обычное значение — {DEFAULT_ORS_PEAK_HOUR_FACTOR}."
        )
    return factor


def _parse_clock_time(raw: str | None, variable: str, default: time) -> time:
    """Общий разбор настроек вида «ЧЧ:ММ» по Москве.

    Имя переменной подставляется в текст ошибки: пользователь не программист, ему нужно
    видеть, какую именно строку в .env исправлять.
    """
    value = (raw or "").strip()
    if not value:
        return default

    example = default.strftime("%H:%M")
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(
            f"{variable}=«{raw}» не похоже на время. Нужен формат ЧЧ:ММ, "
            f"например {example}."
        )
    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError as exc:
        raise ConfigError(
            f"{variable}=«{raw}» не похоже на время. Нужен формат ЧЧ:ММ "
            f"(часы 0–23, минуты 0–59), например {example}."
        ) from exc


def parse_alarm_planning_time(raw: str | None) -> time:
    """Время «ЧЧ:ММ» по Москве, когда бот пересчитывает будильники на день."""
    return _parse_clock_time(raw, "ALARM_PLANNING_TIME", DEFAULT_ALARM_PLANNING_TIME)


def parse_notes_morning_time(raw: str | None) -> time:
    """Время «ЧЧ:ММ» по Москве для утреннего напоминания про незакрытую заметку."""
    return _parse_clock_time(raw, "NOTES_MORNING_TIME", DEFAULT_NOTES_MORNING_TIME)


def parse_alarm_fallback_travel_minutes(raw: str | None) -> int:
    """Запасное время в пути, когда сервис маршрутов молчит.

    Ноль и отрицательные значения запрещены: с ними будильник посчитал бы, что дорога
    не занимает времени, и разбудил бы слишком поздно.
    """
    value = (raw or "").strip()
    if not value:
        return DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES
    try:
        minutes = int(value)
    except ValueError as exc:
        raise ConfigError(
            f"ALARM_FALLBACK_TRAVEL_MINUTES=«{raw}» — это не целое число минут. "
            f"Впишите, сколько обычно занимает дорога, например "
            f"{DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES}."
        ) from exc
    if minutes <= 0:
        raise ConfigError(
            f"ALARM_FALLBACK_TRAVEL_MINUTES=«{raw}» должно быть больше нуля: дорога до "
            f"корпуса не бывает мгновенной. Обычное значение — "
            f"{DEFAULT_ALARM_FALLBACK_TRAVEL_MINUTES}."
        )
    return minutes


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
        router=parse_router(env.get("ROUTER")),
        dgis_api_key=parse_api_key(env.get("DGIS_API_KEY")),
        ors_api_key=parse_api_key(env.get("ORS_API_KEY")),
        ors_peak_hour_factor=parse_peak_hour_factor(env.get("ORS_PEAK_HOUR_FACTOR")),
        alarm_planning_time=parse_alarm_planning_time(env.get("ALARM_PLANNING_TIME")),
        alarm_fallback_travel_minutes=parse_alarm_fallback_travel_minutes(
            env.get("ALARM_FALLBACK_TRAVEL_MINUTES")
        ),
        notes_morning_time=parse_notes_morning_time(env.get("NOTES_MORNING_TIME")),
    )


def load_config(env_file: Path | None = None) -> Config:
    """Читает .env (если он есть) и возвращает настройки приложения."""
    path = env_file if env_file is not None else PROJECT_ROOT / ".env"
    # override=False: если переменная уже задана в окружении (например, в Docker), она главнее.
    load_dotenv(dotenv_path=path, override=False)
    return build_config(os.environ)
