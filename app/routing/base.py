"""Единый интерфейс маршрутизатора: сколько ехать от точки до точки.

Всё, что ходит в сеть за маршрутом, прячется за классом `Router`. Хендлеры и будильник
знают только про него, поэтому в тестах вместо 2ГИС можно подставить `StubRouter` или
свою заглушку, а при отказе одного сервиса — переключиться на другой одной строкой в .env.

Ошибка у всех реализаций одна — `RouterError`. Её текст технический, для лога;
пользователю хендлер показывает свой, дружелюбный.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from app.users import TRANSPORT_CAR, TRANSPORT_PUBLIC

# Кто посчитал время в пути. Строка попадает в лог и решает, делать ли пометку
# «это грубая оценка» в ответе пользователю.
SOURCE_DGIS = "dgis"
SOURCE_ORS = "ors"
SOURCE_STUB = "stub"

# Способы передвижения берём из app.users, чтобы значения не разъехались с базой.
MODE_CAR = TRANSPORT_CAR
MODE_PUBLIC = TRANSPORT_PUBLIC
KNOWN_MODES: tuple[str, ...] = (MODE_CAR, MODE_PUBLIC)

# Ни один сервис не считает дорогу быстрее минуты — округляем вверх до неё.
MIN_TRAVEL_MINUTES = 1


class RouterError(Exception):
    """Маршрут посчитать не удалось: сеть, таймаут, чужой ключ, битый ответ.

    Одно исключение на все причины намеренно: хендлеру важно только то, что числа нет,
    а подробности уходят в лог.
    """


@dataclass(frozen=True, slots=True)
class TravelTime:
    """Время в пути и честность этой оценки."""

    minutes: int
    # Учтены ли настоящие пробки. False у OpenRouteService (там коэффициент часа пик)
    # и у заглушки.
    traffic_aware: bool
    source: str = SOURCE_STUB

    @property
    def is_rough(self) -> bool:
        """True, если это прикидка по прямой линии, а не настоящий маршрут."""
        return self.source == SOURCE_STUB


class Router(ABC):
    """Общий интерфейс всех маршрутизаторов."""

    #: Технический идентификатор реализации, он же `TravelTime.source`.
    name: ClassVar[str] = SOURCE_STUB

    @abstractmethod
    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        """Сколько ехать от `origin` до `destination`.

        `origin` и `destination` — пары (широта, долгота).
        `mode` — `users.TRANSPORT_CAR` или `users.TRANSPORT_PUBLIC`.
        `at` — момент отправления, обязательно с часовым поясом (Europe/Moscow).

        Любой сбой — `RouterError`.
        """

    async def close(self) -> None:
        """Отпустить сетевые ресурсы. У заглушки отпускать нечего."""
        return None


# --- Проверки входных данных --------------------------------------------------------
#
# Все нарушения превращаются в RouterError, а не в ValueError: у хендлера один блок
# except на весь расчёт маршрута, и бот не должен падать даже от кривых данных в базе.


def ensure_mode(mode: str) -> str:
    """Проверяет способ передвижения."""
    if mode not in KNOWN_MODES:
        raise RouterError(
            f"Неизвестный способ передвижения «{mode}», ожидался один из: "
            + ", ".join(KNOWN_MODES)
        )
    return mode


def ensure_point(point: tuple[float, float], label: str) -> tuple[float, float]:
    """Проверяет, что точка — это (широта, долгота) в допустимых пределах."""
    try:
        latitude, longitude = (float(point[0]), float(point[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise RouterError(f"{label}: ожидалась пара (широта, долгота), пришло {point!r}") from exc

    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise RouterError(
            f"{label}: координаты вне допустимых границ (широта={latitude}, долгота={longitude})"
        )
    return (latitude, longitude)


def ensure_aware(at: datetime) -> datetime:
    """Требует datetime с часовым поясом.

    Naive-время на сервере в UTC незаметно уехало бы на три часа и сдвинуло будильник,
    поэтому такой вызов считается ошибкой, а не поводом «догадаться».
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise RouterError(
            "Время отправления пришло без часового пояса. Используйте app.clock.now()."
        )
    return at


def minutes_from_seconds(seconds: float) -> int:
    """Секунды маршрута -> минуты, всегда вверх и не меньше одной."""
    if seconds < 0 or math.isnan(seconds) or math.isinf(seconds):
        raise RouterError(f"Сервис вернул неправдоподобную длительность: {seconds}")
    return max(MIN_TRAVEL_MINUTES, math.ceil(seconds / 60))


__all__ = [
    "KNOWN_MODES",
    "MIN_TRAVEL_MINUTES",
    "MODE_CAR",
    "MODE_PUBLIC",
    "SOURCE_DGIS",
    "SOURCE_ORS",
    "SOURCE_STUB",
    "Router",
    "RouterError",
    "TravelTime",
    "ensure_aware",
    "ensure_mode",
    "ensure_point",
    "minutes_from_seconds",
]
