"""Хендлеры aiogram. Тонкие: принять апдейт, дёрнуть логику, ответить."""

from aiogram import Router

from app.handlers import fallback, schedule, start

_root_router: Router | None = None


def build_root_router() -> Router:
    """Собирает все роутеры в один. Fallback подключается последним — он ловит остаток.

    Роутеры-источники — синглтоны на уровне модуля, а aiogram не даёт прикрепить один
    роутер к двум родителям сразу. Поэтому здесь кэшируем результат: повторный вызов
    (например, из нескольких тестов в одном процессе) возвращает тот же собранный router,
    а не падает с RuntimeError.
    """
    global _root_router
    if _root_router is None:
        router = Router(name="root")
        router.include_router(start.router)
        router.include_router(schedule.router)
        router.include_router(fallback.router)
        _root_router = router
    return _root_router


__all__ = ["build_root_router"]
