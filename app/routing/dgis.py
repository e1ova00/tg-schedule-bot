"""Маршрутизатор на 2ГИС Routing API — основной вариант из ТЗ.

Два разных эндпоинта, потому что у 2ГИС это два разных сервиса:

* авто — Routing API (`/routing/7.0.0/global`), `traffic_mode=jam` включает пробки;
* общественный транспорт — Public Transport API (`/public_transport/2.0`), там пробок
  нет по определению: считаются расписания, интервалы и пересадки.

Отсюда и `traffic_aware`: True только для машины, чтобы бот не обещал пользователю
«с учётом пробок» там, где их никто не считал.

Формат ответа у сервиса менялся между версиями, поэтому разбор нарочно терпимый:
`parse_duration_seconds` понимает и `{"result": [...]}`, и голый список маршрутов, и
берёт из маршрута `total_duration` либо `duration`. Функция чистая — её можно проверить
тестами на сохранённом куске JSON, не поднимая сеть.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, ClassVar

from app.routing.base import (
    MODE_CAR,
    SOURCE_DGIS,
    RouterError,
    TravelTime,
    ensure_aware,
    ensure_mode,
    ensure_point,
    minutes_from_seconds,
)
from app.routing.http import HttpRouter

logger = logging.getLogger(__name__)

DRIVING_URL = "https://routing.api.2gis.com/routing/7.0.0/global"
PUBLIC_TRANSPORT_URL = "https://routing.api.2gis.com/public_transport/2.0"

# Виды городского транспорта, которые разрешаем сервису использовать в маршруте.
PUBLIC_TRANSPORT_KINDS: tuple[str, ...] = (
    "bus",
    "trolleybus",
    "tram",
    "metro",
    "shuttle_bus",
    "suburban_train",
)

_DURATION_KEYS = ("total_duration", "duration")


def _as_seconds(value: object) -> float | None:
    """Длительность из ответа сервиса. Строку «1234» тоже принимаем."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _route_duration_seconds(route: dict[str, Any]) -> float | None:
    """Длительность одного маршрута: `total_duration`, а `duration` — только запасной ключ.

    Раньше оба ключа мешались в одну кучу и бралось наименьшее значение из двух —
    если 2ГИС кладёт в один и тот же маршрут оба поля с разным смыслом, это могло
    незаметно занизить время в пути. Для будильника ошибка в эту сторону хуже, чем
    в другую: лучше разбудить чуть раньше, чем опоздать.
    """
    for key in _DURATION_KEYS:
        seconds = _as_seconds(route.get(key))
        if seconds is not None:
            return seconds
    return None


def parse_duration_seconds(payload: Any) -> float:
    """Достаёт длительность самого быстрого из предложенных маршрутов в секундах.

    Пустой список маршрутов — это не «ошибка сервиса», а «пути нет», но для бота разницы
    нет: считать нечего, значит `RouterError`.
    """
    routes: Any = payload
    if isinstance(payload, dict):
        status = payload.get("status")
        if status is not None and str(status).upper() not in ("OK", "SUCCESS"):
            message = payload.get("message") or payload.get("error") or status
            raise RouterError(f"2ГИС вернул статус «{status}»: {message}")
        routes = payload.get("result", payload.get("routes"))

    if not isinstance(routes, list):
        raise RouterError(f"2ГИС: в ответе нет списка маршрутов ({type(payload).__name__})")

    durations = [
        seconds
        for route in routes
        if isinstance(route, dict)
        if (seconds := _route_duration_seconds(route)) is not None
    ]
    if not durations:
        raise RouterError("2ГИС не нашёл ни одного маршрута между точками")
    return min(durations)


class DgisRouter(HttpRouter):
    """Время в пути по данным 2ГИС."""

    name: ClassVar[str] = SOURCE_DGIS
    service_title: ClassVar[str] = "2ГИС"

    def __init__(
        self,
        api_key: str,
        *,
        driving_url: str = DRIVING_URL,
        public_transport_url: str = PUBLIC_TRANSPORT_URL,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise RouterError("2ГИС: не задан ключ доступа (DGIS_API_KEY в .env)")
        self._api_key = api_key
        self._driving_url = driving_url
        self._public_transport_url = public_transport_url

    def _url(self, base: str) -> str:
        # Ключ 2ГИС передаётся параметром запроса; в логах он не появляется, потому что
        # логируем мы только текст RouterError, а туда URL не попадает.
        return f"{base}?key={self._api_key}"

    @staticmethod
    def driving_payload(
        origin: tuple[float, float], destination: tuple[float, float], at: datetime
    ) -> dict[str, Any]:
        """Тело запроса для авто. Отдельным методом — чтобы его можно было проверить тестом."""
        return {
            "points": [
                {"type": "stop", "lat": origin[0], "lon": origin[1]},
                {"type": "stop", "lat": destination[0], "lon": destination[1]},
            ],
            "locale": "ru",
            "transport": "driving",
            "route_mode": "fastest",
            "traffic_mode": "jam",  # именно это включает пробки
            "output": "summary",
            # Время отправления в UTC-секундах: сервис берёт прогноз пробок на этот момент.
            "utc": int(at.timestamp()),
        }

    @staticmethod
    def public_transport_payload(
        origin: tuple[float, float], destination: tuple[float, float], at: datetime
    ) -> dict[str, Any]:
        """Тело запроса для общественного транспорта."""
        return {
            "locale": "ru",
            "source": {"name": "Откуда", "point": {"lat": origin[0], "lon": origin[1]}},
            "target": {"name": "Куда", "point": {"lat": destination[0], "lon": destination[1]}},
            "transport": list(PUBLIC_TRANSPORT_KINDS),
            "utc": int(at.timestamp()),
        }

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        start = ensure_point(origin, "Точка отправления")
        finish = ensure_point(destination, "Корпус")
        moment = ensure_aware(at)
        by_car = ensure_mode(mode) == MODE_CAR

        if by_car:
            url = self._url(self._driving_url)
            payload = self.driving_payload(start, finish, moment)
        else:
            url = self._url(self._public_transport_url)
            payload = self.public_transport_payload(start, finish, moment)

        answer = await self.post_json(url, payload)
        seconds = parse_duration_seconds(answer)
        minutes = minutes_from_seconds(seconds)
        logger.info(
            "2ГИС: %s мин, режим=%s, пробки=%s", minutes, mode, by_car
        )
        return TravelTime(minutes=minutes, traffic_aware=by_car, source=SOURCE_DGIS)


__all__ = [
    "DRIVING_URL",
    "PUBLIC_TRANSPORT_URL",
    "DgisRouter",
    "parse_duration_seconds",
]
