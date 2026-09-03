"""Точка входа: `python -m app`."""

from __future__ import annotations

import asyncio
import logging
import sys

from app.bot import run_bot
from app.config import Config, ConfigError, load_config
from app.logging_setup import ensure_utf8_console, setup_logging


def _fail(message: str) -> int:
    """Печатает понятную ошибку без трейсбека и возвращает код выхода."""
    print(f"\nОшибка: {message}\n", file=sys.stderr)
    return 1


def main() -> int:
    ensure_utf8_console()

    try:
        config: Config = load_config()
    except ConfigError as error:
        return _fail(str(error))

    setup_logging(level=config.log_level, tz=config.timezone)
    logger = logging.getLogger("app")
    logger.debug(
        "Настройки загружены: часовой пояс %s, база %s, разрешённых пользователей %d",
        config.timezone.key,
        config.db_path,
        len(config.allowed_user_ids),
    )

    try:
        asyncio.run(run_bot(config))
    except KeyboardInterrupt:
        # Ctrl+C — штатное завершение, трейсбек пользователю не нужен.
        logger.info("Получен Ctrl+C, выключаемся")
    except Exception as error:  # noqa: BLE001 — верхний уровень, дальше падать некуда
        logger.exception("Бот аварийно остановлен")
        return _fail(_explain(error))

    return 0


def _explain(error: Exception) -> str:
    """Переводит частые сбои на человеческий язык."""
    from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
    from aiogram.utils.token import TokenValidationError

    if isinstance(error, (TelegramUnauthorizedError, TokenValidationError)):
        return (
            "Telegram не принял BOT_TOKEN. Проверьте, что в .env вписан свежий токен "
            "от @BotFather целиком, без пробелов и кавычек."
        )
    if isinstance(error, TelegramNetworkError):
        return (
            "Не получилось достучаться до Telegram. Проверьте интернет и попробуйте "
            "запустить бота ещё раз."
        )
    return f"{type(error).__name__}: {error}. Подробности — в файле logs/bot.log."


if __name__ == "__main__":
    raise SystemExit(main())
