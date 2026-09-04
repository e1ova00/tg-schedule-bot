"""Маршруты: сколько ехать от дома до корпуса.

Наружу пакет отдаёт только интерфейс (`Router`, `TravelTime`, `RouterError`) и фабрику
`create_router`. Конкретные реализации (2ГИС, OpenRouteService, заглушка) подключаются
внутри пакета, поэтому хендлеры и будильник не знают, кто именно считает дорогу.
"""

from app.routing.base import (
    KNOWN_MODES,
    MODE_CAR,
    MODE_PUBLIC,
    SOURCE_DGIS,
    SOURCE_ORS,
    SOURCE_STUB,
    Router,
    RouterError,
    TravelTime,
)
from app.routing.dgis import DgisRouter
from app.routing.factory import create_router
from app.routing.ors import OrsRouter
from app.routing.stub import StubRouter

__all__ = [
    "KNOWN_MODES",
    "MODE_CAR",
    "MODE_PUBLIC",
    "SOURCE_DGIS",
    "SOURCE_ORS",
    "SOURCE_STUB",
    "DgisRouter",
    "OrsRouter",
    "Router",
    "RouterError",
    "StubRouter",
    "TravelTime",
    "create_router",
]
