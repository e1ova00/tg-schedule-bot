"""Хендлеры aiogram. Тонкие: принять апдейт, дёрнуть логику, ответить."""

from aiogram import Router

from app.handlers import fallback, schedule, start


def build_root_router() -> Router:
    """Собирает все роутеры в один. Fallback подключается последним — он ловит остаток."""
    router = Router(name="root")
    router.include_router(start.router)
    router.include_router(schedule.router)
    router.include_router(fallback.router)
    return router


__all__ = ["build_root_router"]
