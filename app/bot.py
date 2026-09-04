"""Создание бота и запуск long polling."""

from __future__ import annotations

import logging

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.buildings import seed_buildings
from app.config import Config
from app.db import open_database
from app.handlers import build_root_router
from app.middlewares import AccessMiddleware, DatabaseMiddleware

logger = logging.getLogger(__name__)

# Показывается в Telegram по кнопке ☰ рядом с полем ввода — весь список команд сразу,
# без необходимости помнить их или искать в /help.
BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand(command="today", description="Пары на сегодня"),
    BotCommand(command="tomorrow", description="Пары на завтра"),
    BotCommand(command="settings", description="Посмотреть и поменять настройки"),
    BotCommand(command="help", description="Что я умею"),
    BotCommand(command="start", description="Поздороваться / начать знакомство"),
    BotCommand(command="cancel", description="Прервать текущий диалог"),
)


def create_bot(config: Config) -> Bot:
    # Без TELEGRAM_PROXY_URL сессия обычная: большинству прокси для Telegram не нужен.
    session = AiohttpSession(proxy=config.proxy_url) if config.proxy_url else None
    return Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    config: Config, db: aiosqlite.Connection | None = None
) -> Dispatcher:
    """Диспетчер с памятью для диалогов и двумя middleware на уровне Update.

    Порядок middleware: сначала проверка доступа (чужим даже базу открывать незачем),
    потом выдача соединения с SQLite хендлерам.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(AccessMiddleware(config.allowed_user_ids))
    if db is not None:
        dispatcher.update.outer_middleware(DatabaseMiddleware(db))
    dispatcher.include_router(build_root_router())
    return dispatcher


async def run_bot(config: Config) -> None:
    """Поднимает бота и крутит long polling до Ctrl+C."""
    bot = create_bot(config)
    db = await open_database(config.db_path)
    # Координаты корпусов кладём в базу один раз: дальше их берёт расчёт маршрута.
    await seed_buildings(db)

    if config.allowed_user_ids:
        logger.info("Доступ к боту открыт для %d пользователей", len(config.allowed_user_ids))
    else:
        logger.warning(
            "ALLOWED_USER_IDS пуст — боту может писать кто угодно. "
            "Впишите свой Telegram ID в .env, когда закончите настройку."
        )

    dispatcher = create_dispatcher(config, db)

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s (id=%s)", me.username, me.id)

        await bot.set_my_commands(list(BOT_COMMANDS))

        # Накопившиеся за время простоя апдейты не нужны: будильник всё равно уже не разбудит.
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:  # noqa: BLE001 — некритично, polling работает и без этого
            logger.warning("Не удалось сбросить webhook, продолжаем без этого", exc_info=True)

        await dispatcher.start_polling(bot, handle_signals=True)
    finally:
        await db.close()
        await bot.session.close()
        logger.info("Бот остановлен")
