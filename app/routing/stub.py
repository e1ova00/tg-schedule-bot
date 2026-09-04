"""Заглушка маршрутизатора: считает дорогу по прямой, без сети.

Нужна в двух случаях: в тестах (результат детерминированный, интернет не требуется)
и в бою, когда ключа сервиса маршрутов ещё нет. Во втором случае бот не падает и не
молчит, но и не делает вид, что знает точное время: `TravelTime.is_rough` = True,
и хендлер добавляет к ответу честную оговорку.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import ClassVar

from app.routing.base import (
    MODE_CAR,
    SOURCE_STUB,
    Router,
    TravelTime,
    ensure_aware,
    ensure_mode,
    ensure_point,
    minutes_from_seconds,
)

EARTH_RADIUS_KM = 6371.0

# Средние скорости «от двери до двери» по Петербургу: машина стоит на светофорах и
# паркуется, общественный транспорт ещё и ждётся на остановке.
CAR_SPEED_KMH = 30.0
PUBLIC_SPEED_KMH = 18.0

# Дороги не прямые: реальный путь длиннее отрезка между точками примерно на треть.
DETOUR_FACTOR = 1.3


def haversine_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    """Расстояние по прямой между двумя точками (широта, долгота) в километрах."""
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    lat2, lon2 = math.radians(destination[0]), math.radians(destination[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def straight_line_minutes(
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    *,
    car_speed_kmh: float = CAR_SPEED_KMH,
    public_speed_kmh: float = PUBLIC_SPEED_KMH,
    detour_factor: float = DETOUR_FACTOR,
) -> int:
    """Грубая оценка времени в пути в минутах. Чистая функция, тестируется без сети."""
    speed = car_speed_kmh if mode == MODE_CAR else public_speed_kmh
    distance_km = haversine_km(origin, destination) * detour_factor
    return minutes_from_seconds(distance_km / speed * 3600)


class StubRouter(Router):
    """Маршрутизатор без сети: расстояние по прямой, делённое на среднюю скорость."""

    name: ClassVar[str] = SOURCE_STUB

    def __init__(
        self,
        *,
        car_speed_kmh: float = CAR_SPEED_KMH,
        public_speed_kmh: float = PUBLIC_SPEED_KMH,
        detour_factor: float = DETOUR_FACTOR,
    ) -> None:
        self._car_speed_kmh = car_speed_kmh
        self._public_speed_kmh = public_speed_kmh
        self._detour_factor = detour_factor

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        # `at` заглушке не нужен, но проверяем его так же строго: тесты хендлеров должны
        # ловить naive datetime и на StubRouter, а не только на боевом маршрутизаторе.
        ensure_aware(at)
        minutes = straight_line_minutes(
            ensure_point(origin, "Точка отправления"),
            ensure_point(destination, "Корпус"),
            ensure_mode(mode),
            car_speed_kmh=self._car_speed_kmh,
            public_speed_kmh=self._public_speed_kmh,
            detour_factor=self._detour_factor,
        )
        return TravelTime(minutes=minutes, traffic_aware=False, source=SOURCE_STUB)


__all__ = [
    "CAR_SPEED_KMH",
    "DETOUR_FACTOR",
    "PUBLIC_SPEED_KMH",
    "StubRouter",
    "haversine_km",
    "straight_line_minutes",
]
