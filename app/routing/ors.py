"""Запасной маршрутизатор на OpenRouteService.

Нужен, если ключ 2ГИС не дали. Важное ограничение: **пробок ORS не знает** — он считает
дорогу по «пустому» городу. Поэтому базовое время домножается на коэффициент часа пик
из конфига (`ORS_PEAK_HOUR_FACTOR`), а `traffic_aware` всегда остаётся False: это
поправка «на глаз», а не настоящие пробки, и обещать пользователю обратное нечестно.

Про общественный транспорт: в бесплатном публичном ORS профиля городского транспорта
нет (профиль `public-transport` доступен только на своём сервере с загруженным GTFS).
Поэтому для `public_transport` берётся `cycling-regular` — его средняя скорость по
городу (15–18 км/ч) близка к скорости «от двери до двери» на автобусе с ожиданием
на остановке. Это осознанное приближение, а не настоящий маршрут с пересадками.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, ClassVar

from app.clock import MOSCOW
from app.config import DEFAULT_ORS_PEAK_HOUR_FACTOR
from app.routing.base import (
    MODE_CAR,
    SOURCE_ORS,
    RouterError,
    TravelTime,
    ensure_aware,
    ensure_mode,
    ensure_point,
    minutes_from_seconds,
)
from app.routing.http import HttpRouter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openrouteservice.org/v2/directions"

PROFILE_CAR = "driving-car"
# Приближение: настоящего городского транспорта в бесплатном ORS нет (см. docstring модуля).
PROFILE_PUBLIC = "cycling-regular"

# Часы пик по Москве: утренний и вечерний. Границы включают начало и не включают конец.
PEAK_HOURS: tuple[tuple[int, int], ...] = ((7, 10), (17, 20))


def is_peak_hour(at: datetime) -> bool:
    """Попадает ли момент в час пик по московскому времени.

    Выходные не считаем часом пик: в субботу у группы бывают пары, но пробок
    «на работу» в это время нет.
    """
    moment = ensure_aware(at).astimezone(MOSCOW)
    if moment.weekday() >= 5:  # 5 = суббота, 6 = воскресенье
        return False
    return any(start <= moment.hour < end for start, end in PEAK_HOURS)


def apply_peak_factor(
    seconds: float, at: datetime, factor: float = DEFAULT_ORS_PEAK_HOUR_FACTOR
) -> float:
    """Растягивает время в пути, если едем в час пик. Вне часа пик — без изменений."""
    return seconds * factor if is_peak_hour(at) else seconds


def _as_seconds(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def parse_duration_seconds(payload: Any) -> float:
    """Длительность самого быстрого маршрута ORS в секундах.

    Понимает обычный ответ (`routes[].summary.duration`) и GeoJSON
    (`features[].properties.summary.duration`) — ORS отдаёт разное в зависимости
    от заголовка Accept.
    """
    if not isinstance(payload, dict):
        raise RouterError(f"ORS: в ответе не объект, а {type(payload).__name__}")

    error = payload.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else error
        raise RouterError(f"ORS вернул ошибку: {message}")

    containers = payload.get("routes")
    if not isinstance(containers, list):
        containers = payload.get("features")
    if not isinstance(containers, list):
        raise RouterError("ORS: в ответе нет списка маршрутов")

    durations: list[float] = []
    for item in containers:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if not isinstance(summary, dict):
            properties = item.get("properties")
            summary = properties.get("summary") if isinstance(properties, dict) else None
        if isinstance(summary, dict):
            seconds = _as_seconds(summary.get("duration"))
            if seconds is not None:
                durations.append(seconds)

    if not durations:
        raise RouterError("ORS не нашёл маршрута между точками")
    return min(durations)


class OrsRouter(HttpRouter):
    """Время в пути по данным OpenRouteService, без пробок, с поправкой на час пик."""

    name: ClassVar[str] = SOURCE_ORS
    service_title: ClassVar[str] = "OpenRouteService"

    def __init__(
        self,
        api_key: str,
        *,
        peak_hour_factor: float = DEFAULT_ORS_PEAK_HOUR_FACTOR,
        base_url: str = BASE_URL,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise RouterError("ORS: не задан ключ доступа (ORS_API_KEY в .env)")
        self._api_key = api_key
        self._peak_hour_factor = peak_hour_factor
        self._base_url = base_url.rstrip("/")

    @staticmethod
    def profile_for(mode: str) -> str:
        return PROFILE_CAR if mode == MODE_CAR else PROFILE_PUBLIC

    @staticmethod
    def payload(
        origin: tuple[float, float], destination: tuple[float, float]
    ) -> dict[str, Any]:
        """Тело запроса ORS. Внимание: координаты здесь в порядке (долгота, широта)."""
        return {
            "coordinates": [
                [origin[1], origin[0]],
                [destination[1], destination[0]],
            ]
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
        profile = self.profile_for(ensure_mode(mode))

        answer = await self.post_json(
            f"{self._base_url}/{profile}",
            self.payload(start, finish),
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        seconds = parse_duration_seconds(answer)
        minutes = minutes_from_seconds(
            apply_peak_factor(seconds, moment, self._peak_hour_factor)
        )
        logger.info(
            "ORS: %s мин, профиль=%s, час пик=%s", minutes, profile, is_peak_hour(moment)
        )
        # traffic_aware=False всегда: пробок ORS не знает, коэффициент — это оценка.
        return TravelTime(minutes=minutes, traffic_aware=False, source=SOURCE_ORS)


__all__ = [
    "BASE_URL",
    "PEAK_HOURS",
    "PROFILE_CAR",
    "PROFILE_PUBLIC",
    "OrsRouter",
    "apply_peak_factor",
    "is_peak_hour",
    "parse_duration_seconds",
]
