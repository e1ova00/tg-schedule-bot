"""Создание бота и запуск long polling."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.config import Config
from app.handlers import build_root_router

logger = logging.getLogger(__name__)


def create_bot(config: Config) -> Bot:
    # Без TELEGRAM_PROXY_URL сессия обычная: большинству прокси для Telegram не нужен.
    session = AiohttpSession(proxy=config.proxy_url) if config.proxy_url else None
    return Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(build_root_router())
    return dispatcher


async def run_bot(config: Config) -> None:
    """Поднимает бота и крутит long polling до Ctrl+C."""
    bot = create_bot(config)
    dispatcher = create_dispatcher()

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s (id=%s)", me.username, me.id)

        # Накопившиеся за время простоя апдейты не нужны: будильник всё равно уже не разбудит.
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:  # noqa: BLE001 — некритично, polling работает и без этого
            logger.warning("Не удалось сбросить webhook, продолжаем без этого", exc_info=True)

        await dispatcher.start_polling(bot, handle_signals=True)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен")
