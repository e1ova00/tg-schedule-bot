"""Выбор маршрутизатора по настройкам из .env.

Одно место, где решается, кто считает дорогу. Если ключа выбранного сервиса нет,
бот **не падает и не отключает /route**: он берёт `StubRouter` и считает по прямой,
а хендлер честно подписывает такой ответ как грубую прикидку. Молчать в этой ситуации
нельзя — бот всё-таки будильник, — но и выдавать прикидку за точный расчёт тоже.
"""

from __future__ import annotations

import logging

from app.config import ROUTER_DGIS, ROUTER_ORS, Config
from app.routing.base import Router
from app.routing.dgis import DgisRouter
from app.routing.ors import OrsRouter
from app.routing.stub import StubRouter

logger = logging.getLogger(__name__)


def create_router(config: Config) -> Router:
    """Маршрутизатор по конфигу. Сеть здесь не трогается — только выбор класса."""
    if config.router == ROUTER_DGIS and config.dgis_api_key:
        logger.info("Маршруты считает 2ГИС (с учётом пробок)")
        return DgisRouter(config.dgis_api_key)

    if config.router == ROUTER_ORS and config.ors_api_key:
        logger.info(
            "Маршруты считает OpenRouteService, пробок нет, коэффициент часа пик %.2f",
            config.ors_peak_hour_factor,
        )
        return OrsRouter(
            config.ors_api_key, peak_hour_factor=config.ors_peak_hour_factor
        )

    logger.warning(
        "Ключ маршрутизатора «%s» не задан, использую грубую оценку по прямой. "
        "Впишите ключ в .env, чтобы бот считал настоящую дорогу.",
        config.router,
    )
    return StubRouter()


__all__ = ["create_router"]
