"""Тесты этапа 4: пакет `app.routing` — сколько ехать от дома до корпуса.

Сеть здесь не поднимается ни разу. Всё, что ходит в HTTP, тестируется через подменённую
`session_factory`: вместо `aiohttp.ClientSession` подставляется `FakeSession`, которая
запоминает запросы и отдаёт заранее заготовленные ответы (или бросает сетевую ошибку).

Источник истины для ожиданий — ТЗ (`CLAUDE.md`, стек: 2ГИС основной, OpenRouteService
запасной) и решение этапа 4: **без ключа бот не падает и не молчит**, а считает по прямой
линии и честно помечает такой ответ как грубую прикидку (`TravelTime.is_rough`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import pytest

from app.clock import MOSCOW
from app.config import DEFAULT_ORS_PEAK_HOUR_FACTOR, Config, build_config
from app.routing import dgis as dgis_module
from app.routing import ors as ors_module
from app.routing.base import (
    KNOWN_MODES,
    MIN_TRAVEL_MINUTES,
    MODE_CAR,
    MODE_PUBLIC,
    SOURCE_DGIS,
    SOURCE_ORS,
    SOURCE_STUB,
    RouterError,
    TravelTime,
    ensure_aware,
    ensure_mode,
    ensure_point,
    minutes_from_seconds,
)
from app.routing.dgis import DRIVING_URL, PUBLIC_TRANSPORT_URL, DgisRouter
from app.routing.factory import create_router
from app.routing.http import DEFAULT_TIMEOUT_SECONDS, HttpRouter
from app.routing.ors import PROFILE_CAR, PROFILE_PUBLIC, OrsRouter, apply_peak_factor, is_peak_hour
from app.routing.stub import StubRouter, haversine_km, straight_line_minutes

# Координаты из этапа 3: дом тестового пользователя и три корпуса.
HOME = (59.9386, 30.3141)
VOZNESENSKY = (59.9200, 30.3033)
SADOVAYA = (59.9223, 30.3009)
BOLSHAYA_MORSKAYA = (59.9353, 30.3155)

# Среда 2 сентября 2026, 08:00 по Москве — обычное утро буднего дня.
WED_MORNING = datetime(2026, 9, 2, 8, 0, tzinfo=MOSCOW)
# То же число, но 13:00 — это уже не час пик.
WED_MIDDAY = datetime(2026, 9, 2, 13, 0, tzinfo=MOSCOW)


# ======================================================================================
# Заглушки вместо aiohttp
# ======================================================================================


@dataclass
class Call:
    """Один запрос, который маршрутизатор попытался отправить."""

    url: str
    payload: dict[str, Any] | None
    headers: dict[str, str] | None


class FakeResponse:
    """Ответ сервиса: асинхронный контекстный менеджер со `status` и `text()`."""

    def __init__(
        self, status: int = 200, body: str = "{}", error: BaseException | None = None
    ) -> None:
        self.status = status
        self._body = body
        self._error = error

    async def __aenter__(self) -> FakeResponse:
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def text(self) -> str:
        return self._body


@dataclass
class FakeSession:
    """Замена aiohttp.ClientSession: помнит запросы, отдаёт заготовленные ответы."""

    responses: list[FakeResponse] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    closed: bool = False
    close_calls: int = 0

    def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append(Call(url=url, payload=json, headers=headers))
        if not self.responses:
            raise AssertionError("Запросов больше, чем заготовленных ответов")
        return self.responses.pop(0)

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def session_with(*responses: FakeResponse) -> tuple[FakeSession, Any]:
    """Готовая фейковая сессия и фабрика, которая её отдаёт."""
    session = FakeSession(responses=list(responses))
    return session, lambda: session


def ok(payload: object) -> FakeResponse:
    """Успешный ответ сервиса с JSON-телом."""
    return FakeResponse(status=200, body=json.dumps(payload))


class ProbeRouter(HttpRouter):
    """Пустая реализация поверх HttpRouter: нужна только чтобы проверить обвязку."""

    service_title = "Тестовый сервис"

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:  # pragma: no cover — в этих тестах не вызывается
        raise NotImplementedError


def make_config(**env: str) -> Config:
    """Config из переменных окружения: токен обязателен, остальное — по вкусу теста."""
    values = {"BOT_TOKEN": "123456:AAHfake-token"}
    values.update(env)
    return build_config(values)


# ======================================================================================
# 1. TravelTime: честность оценки
# ======================================================================================


def test_travel_time_from_stub_is_rough() -> None:
    """Прикидка по прямой обязана признаваться грубой — на этом держится текст /route."""
    assert TravelTime(minutes=10, traffic_aware=False, source=SOURCE_STUB).is_rough is True


@pytest.mark.parametrize(
    ("source", "traffic_aware"),
    [
        pytest.param(SOURCE_DGIS, True, id="2ГИС с пробками"),
        pytest.param(SOURCE_DGIS, False, id="2ГИС без пробок (транспорт)"),
        pytest.param(SOURCE_ORS, False, id="OpenRouteService"),
    ],
)
def test_travel_time_from_real_service_is_not_rough(source: str, traffic_aware: bool) -> None:
    """Настоящий маршрут грубым не считается, даже если пробки в нём не учтены."""
    assert TravelTime(minutes=10, traffic_aware=traffic_aware, source=source).is_rough is False


def test_travel_time_source_defaults_to_stub() -> None:
    """Забыли указать источник — считаем это прикидкой, а не «точным» расчётом."""
    travel = TravelTime(minutes=10, traffic_aware=False)

    assert travel.source == SOURCE_STUB
    assert travel.is_rough is True


def test_travel_time_is_immutable() -> None:
    """Результат расчёта нельзя незаметно подправить по дороге к пользователю."""
    travel = TravelTime(minutes=10, traffic_aware=True, source=SOURCE_DGIS)
    with pytest.raises(Exception):
        travel.minutes = 1  # type: ignore[misc]


# ======================================================================================
# 2. Проверки входных данных: любое нарушение — RouterError
# ======================================================================================


@pytest.mark.parametrize("mode", KNOWN_MODES)
def test_ensure_mode_accepts_known_modes(mode: str) -> None:
    assert ensure_mode(mode) == mode


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("bike", id="незнакомый способ"),
        pytest.param("", id="пустая строка"),
        pytest.param("CAR", id="другой регистр"),
        pytest.param("машина", id="по-русски"),
        pytest.param(None, id="None вместо режима"),
    ],
)
def test_ensure_mode_rejects_unknown(mode: object) -> None:
    """Кривой transport_mode из базы не должен ронять бота голым ValueError."""
    with pytest.raises(RouterError) as exc_info:
        ensure_mode(mode)  # type: ignore[arg-type]

    # В тексте ошибки перечислены допустимые значения — это уходит в лог.
    assert MODE_CAR in str(exc_info.value)
    assert MODE_PUBLIC in str(exc_info.value)


def test_ensure_point_returns_floats() -> None:
    assert ensure_point((59, 30), "Дом") == (59.0, 30.0)


@pytest.mark.parametrize(
    "point",
    [
        pytest.param((91.0, 30.0), id="широта больше 90"),
        pytest.param((-91.0, 30.0), id="широта меньше -90"),
        pytest.param((59.9, 200.0), id="долгота больше 180"),
        pytest.param((59.9, -200.0), id="долгота меньше -180"),
        pytest.param((30.0, "тридцать"), id="строка вместо числа"),
        pytest.param((59.9,), id="одна координата вместо двух"),
        pytest.param((), id="пустая пара"),
        pytest.param(None, id="None вместо точки"),
        pytest.param((None, None), id="пустые координаты"),
    ],
)
def test_ensure_point_rejects_garbage(point: object) -> None:
    """Испорченные координаты — RouterError, а не TypeError/IndexError наружу."""
    with pytest.raises(RouterError) as exc_info:
        ensure_point(point, "Корпус")  # type: ignore[arg-type]

    assert "Корпус" in str(exc_info.value)


def test_ensure_aware_accepts_moscow_time() -> None:
    assert ensure_aware(WED_MORNING) == WED_MORNING


def test_ensure_aware_accepts_utc() -> None:
    moment = datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)
    assert ensure_aware(moment) == moment


def test_ensure_aware_rejects_naive_datetime() -> None:
    """Без пояса время на UTC-сервере уехало бы на три часа и сдвинуло будильник."""
    with pytest.raises(RouterError) as exc_info:
        ensure_aware(datetime(2026, 9, 2, 8, 0))

    assert "часового пояса" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        pytest.param(0, 1, id="ноль секунд — всё равно минута"),
        pytest.param(1, 1, id="секунда"),
        pytest.param(59, 1, id="меньше минуты"),
        pytest.param(60, 1, id="ровно минута"),
        pytest.param(61, 2, id="чуть больше минуты — округляем вверх"),
        pytest.param(1234, 21, id="20 минут 34 секунды"),
        pytest.param(3600, 60, id="час"),
    ],
)
def test_minutes_from_seconds(seconds: float, expected: int) -> None:
    """Округление всегда вверх: лучше выйти раньше, чем опоздать."""
    assert minutes_from_seconds(seconds) == expected
    assert minutes_from_seconds(seconds) >= MIN_TRAVEL_MINUTES


@pytest.mark.parametrize(
    "seconds",
    [
        pytest.param(-1, id="отрицательное время"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="бесконечность"),
    ],
)
def test_minutes_from_seconds_rejects_nonsense(seconds: float) -> None:
    with pytest.raises(RouterError):
        minutes_from_seconds(seconds)


# ======================================================================================
# 3. StubRouter: расчёт по прямой без сети
# ======================================================================================


async def test_stub_router_gives_plausible_minutes() -> None:
    """От дома до Садовой по прямой — единицы минут, а не ноль и не часы."""
    travel = await StubRouter().travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)

    assert isinstance(travel.minutes, int)
    assert 1 <= travel.minutes <= 30
    assert travel.source == SOURCE_STUB
    assert travel.is_rough is True


async def test_stub_router_never_promises_traffic() -> None:
    """Пробок заглушка не знает — обещать их пользователю нельзя."""
    travel = await StubRouter().travel_time(HOME, VOZNESENSKY, MODE_CAR, WED_MORNING)

    assert travel.traffic_aware is False


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(VOZNESENSKY, SADOVAYA, id="Вознесенский — Садовая"),
        pytest.param(VOZNESENSKY, BOLSHAYA_MORSKAYA, id="Вознесенский — Большая Морская"),
        pytest.param(SADOVAYA, BOLSHAYA_MORSKAYA, id="Садовая — Большая Морская"),
    ],
)
async def test_stub_router_between_buildings_is_not_absurd(
    first: tuple[float, float], second: tuple[float, float]
) -> None:
    """Соседние корпуса в центре Петербурга — это минуты, а не «полтора часа»."""
    travel = await StubRouter().travel_time(first, second, MODE_CAR, WED_MORNING)

    assert haversine_km(first, second) < 3.0, "Проверяем именно короткое расстояние"
    assert 1 <= travel.minutes <= 15


async def test_stub_router_car_is_faster_than_public_transport() -> None:
    """На одном и том же расстоянии машина должна выигрывать у транспорта с ожиданием."""
    stub = StubRouter()

    by_car = await stub.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)
    by_public = await stub.travel_time(HOME, SADOVAYA, MODE_PUBLIC, WED_MORNING)

    assert by_car.minutes < by_public.minutes


async def test_stub_router_longer_distance_takes_longer() -> None:
    """Дальний корпус не может оказаться ближе по времени, чем соседний."""
    stub = StubRouter()

    near = await stub.travel_time(HOME, BOLSHAYA_MORSKAYA, MODE_CAR, WED_MORNING)
    far = await stub.travel_time(HOME, VOZNESENSKY, MODE_CAR, WED_MORNING)

    assert haversine_km(HOME, BOLSHAYA_MORSKAYA) < haversine_km(HOME, VOZNESENSKY)
    assert near.minutes < far.minutes


async def test_stub_router_same_point_is_one_minute() -> None:
    """Нулевого времени в пути не бывает — минимум одна минута."""
    travel = await StubRouter().travel_time(HOME, HOME, MODE_CAR, WED_MORNING)

    assert travel.minutes == MIN_TRAVEL_MINUTES


async def test_stub_router_rejects_naive_datetime() -> None:
    """Заглушка проверяет время так же строго, как боевой маршрутизатор."""
    with pytest.raises(RouterError):
        await StubRouter().travel_time(HOME, SADOVAYA, MODE_CAR, datetime(2026, 9, 2, 8, 0))


async def test_stub_router_rejects_unknown_mode() -> None:
    with pytest.raises(RouterError):
        await StubRouter().travel_time(HOME, SADOVAYA, "самокат", WED_MORNING)


async def test_stub_router_rejects_broken_coordinates() -> None:
    with pytest.raises(RouterError):
        await StubRouter().travel_time(HOME, (999.0, 30.0), MODE_CAR, WED_MORNING)


async def test_stub_router_close_is_safe() -> None:
    """Закрывать у заглушки нечего, но интерфейс общий — падать нельзя."""
    assert await StubRouter().close() is None


def test_straight_line_minutes_accounts_for_detour() -> None:
    """Дорога не бывает прямой: коэффициент извилистости должен удлинять маршрут."""
    straight = straight_line_minutes(HOME, VOZNESENSKY, MODE_CAR, detour_factor=1.0)
    real = straight_line_minutes(HOME, VOZNESENSKY, MODE_CAR, detour_factor=1.3)

    assert real >= straight


def test_haversine_matches_known_distance() -> None:
    """Дом тестового пользователя и Вознесенский — около двух километров."""
    assert 1.5 < haversine_km(HOME, VOZNESENSKY) < 3.0


# ======================================================================================
# 4. HttpRouter: сессия, таймаут, коды ответа
# ======================================================================================


async def test_http_router_returns_parsed_json() -> None:
    session, factory = session_with(ok({"result": [{"total_duration": 60}]}))
    probe = ProbeRouter(session_factory=factory)

    answer = await probe.post_json("https://example.test/route", {"a": 1}, headers={"K": "v"})

    assert answer == {"result": [{"total_duration": 60}]}
    assert session.calls == [
        Call(url="https://example.test/route", payload={"a": 1}, headers={"K": "v"})
    ]


async def test_http_router_creates_session_lazily() -> None:
    """Пока никто не спросил маршрут, HTTP-сессии не существует."""
    created = 0

    def factory() -> FakeSession:
        nonlocal created
        created += 1
        return FakeSession(responses=[ok({}), ok({})])

    probe = ProbeRouter(session_factory=factory)
    assert created == 0

    await probe.post_json("https://example.test", {})
    await probe.post_json("https://example.test", {})

    assert created == 1, "Сессия должна быть одна на все запросы"


async def test_http_router_replaces_closed_session() -> None:
    """Если сессию закрыли снаружи, следующий запрос заводит новую, а не падает."""
    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        session = FakeSession(responses=[ok({})])
        sessions.append(session)
        return session

    probe = ProbeRouter(session_factory=factory)
    await probe.post_json("https://example.test", {})
    sessions[0].closed = True
    await probe.post_json("https://example.test", {})

    assert len(sessions) == 2


async def test_http_router_timeout_becomes_router_error() -> None:
    """Сервис молчит — /route отвечает текстом, а не висит и не роняет бота."""
    _, factory = session_with(FakeResponse(error=TimeoutError()))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError) as exc_info:
        await probe.post_json("https://example.test", {})

    assert "Тестовый сервис" in str(exc_info.value)


async def test_http_router_network_error_becomes_router_error() -> None:
    _, factory = session_with(FakeResponse(error=aiohttp.ClientError("нет соединения")))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError, match="сеть недоступна"):
        await probe.post_json("https://example.test", {})


async def test_http_router_os_error_becomes_router_error() -> None:
    """DNS не разрешился, сеть отвалилась — тоже RouterError."""
    _, factory = session_with(FakeResponse(error=OSError("сеть недоступна")))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError):
        await probe.post_json("https://example.test", {})


@pytest.mark.parametrize("status", [401, 403])
async def test_http_router_bad_key_mentions_env(status: int) -> None:
    """Чужой или просроченный ключ — подсказываем, что чинить в .env."""
    _, factory = session_with(FakeResponse(status=status, body="forbidden"))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError) as exc_info:
        await probe.post_json("https://example.test", {})

    assert ".env" in str(exc_info.value)
    assert str(status) in str(exc_info.value)


@pytest.mark.parametrize("status", [400, 429, 500, 502, 503])
async def test_http_router_non_200_becomes_router_error(status: int) -> None:
    _, factory = session_with(FakeResponse(status=status, body="что-то пошло не так"))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError) as exc_info:
        await probe.post_json("https://example.test", {})

    assert str(status) in str(exc_info.value)


async def test_http_router_broken_json_becomes_router_error() -> None:
    """Сервис отдал HTML-страницу вместо JSON — это не повод падать с JSONDecodeError."""
    _, factory = session_with(FakeResponse(status=200, body="<html>ошибка</html>"))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError, match="не JSON"):
        await probe.post_json("https://example.test", {})


async def test_http_router_shortens_long_body_in_error() -> None:
    """Тело ответа в логе обрезается: страницу целиком туда класть незачем."""
    _, factory = session_with(FakeResponse(status=500, body="ы" * 5000))
    probe = ProbeRouter(session_factory=factory)

    with pytest.raises(RouterError) as exc_info:
        await probe.post_json("https://example.test", {})

    assert len(str(exc_info.value)) < 1000
    assert "…" in str(exc_info.value)


async def test_http_router_close_releases_session() -> None:
    session, factory = session_with(ok({}))
    probe = ProbeRouter(session_factory=factory)
    await probe.post_json("https://example.test", {})

    await probe.close()

    assert session.close_calls == 1
    # Повторное закрытие ничего не делает и не падает — так закрывается бот по Ctrl+C.
    await probe.close()
    assert session.close_calls == 1


async def test_http_router_close_without_session_is_safe() -> None:
    """Бот стартовал, ключа нет, никто ничего не запрашивал — выключение всё равно тихое."""
    assert await ProbeRouter(session_factory=lambda: FakeSession()).close() is None


async def test_http_router_close_survives_broken_session(caplog: pytest.LogCaptureFixture) -> None:
    class AngrySession(FakeSession):
        async def close(self) -> None:
            raise RuntimeError("сессия сломалась")

    session = AngrySession(responses=[ok({})])
    probe = ProbeRouter(session_factory=lambda: session)
    await probe.post_json("https://example.test", {})

    with caplog.at_level(logging.WARNING, logger="app.routing.http"):
        await probe.close()  # не должно бросить наружу

    assert caplog.records


def test_http_router_default_timeout_is_ten_seconds() -> None:
    """Человек смотрит в экран — ждать дольше десяти секунд бессмысленно."""
    assert DEFAULT_TIMEOUT_SECONDS == 10.0


# ======================================================================================
# 5. 2ГИС: разбор ответа и запросы
# ======================================================================================


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"result": [{"total_duration": 1234}]}, 1234.0, id="обычный ответ"),
        pytest.param({"routes": [{"duration": 600}]}, 600.0, id="ключ routes и duration"),
        pytest.param([{"total_duration": 300}], 300.0, id="голый список маршрутов"),
        pytest.param(
            {"result": [{"total_duration": 900}, {"total_duration": 600}]},
            600.0,
            id="берём самый быстрый маршрут",
        ),
        pytest.param({"result": [{"total_duration": "780"}]}, 780.0, id="число строкой"),
        pytest.param(
            {"status": "OK", "result": [{"total_duration": 120}]}, 120.0, id="статус OK"
        ),
        pytest.param(
            {"result": [{"нет_длительности": 1}, {"duration": 480}]},
            480.0,
            id="лишний маршрут без длительности пропускаем",
        ),
        pytest.param(
            {"result": [{"total_duration": 900, "duration": 600}]},
            900.0,
            id="внутри одного маршрута total_duration главнее duration",
        ),
    ],
)
def test_dgis_parse_duration_seconds(payload: object, expected: float) -> None:
    assert dgis_module.parse_duration_seconds(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"result": []}, id="маршрутов нет"),
        pytest.param([], id="пустой список"),
        pytest.param({"result": [{"total_duration": None}]}, id="длительность None"),
        pytest.param({"result": [{"total_duration": "около часа"}]}, id="длительность словами"),
        pytest.param({"result": [{"total_duration": -5}]}, id="отрицательная длительность"),
        pytest.param({"result": [{"total_duration": True}]}, id="True вместо числа"),
        pytest.param({"result": "нет"}, id="вместо списка строка"),
        pytest.param({"чужой_формат": 1}, id="ни result, ни routes"),
        pytest.param("текст", id="ответ не JSON-объект"),
        pytest.param({"status": "ERROR", "message": "ключ не принят"}, id="статус ошибки"),
    ],
)
def test_dgis_parse_duration_seconds_rejects_bad_payload(payload: object) -> None:
    """Любой неожиданный ответ — RouterError, чтобы хендлер показал понятный текст."""
    with pytest.raises(RouterError):
        dgis_module.parse_duration_seconds(payload)


def test_dgis_parse_duration_zero_is_allowed_and_rounds_up_to_a_minute() -> None:
    """Нулевая длительность (точки совпали) — это не сбой: минимум одна минута."""
    seconds = dgis_module.parse_duration_seconds({"result": [{"total_duration": 0}]})

    assert seconds == 0.0
    assert minutes_from_seconds(seconds) == MIN_TRAVEL_MINUTES


def test_dgis_driving_payload_turns_on_traffic() -> None:
    """Пробки у 2ГИС включает именно traffic_mode=jam — без него это «пустой город»."""
    payload = DgisRouter.driving_payload(HOME, SADOVAYA, WED_MORNING)

    assert payload["traffic_mode"] == "jam"
    assert payload["transport"] == "driving"
    assert payload["route_mode"] == "fastest"
    assert payload["points"][0] == {"type": "stop", "lat": HOME[0], "lon": HOME[1]}
    assert payload["points"][1] == {"type": "stop", "lat": SADOVAYA[0], "lon": SADOVAYA[1]}
    assert payload["utc"] == int(WED_MORNING.timestamp())


def test_dgis_public_transport_payload_lists_transport_kinds() -> None:
    payload = DgisRouter.public_transport_payload(HOME, SADOVAYA, WED_MORNING)

    assert payload["source"]["point"] == {"lat": HOME[0], "lon": HOME[1]}
    assert payload["target"]["point"] == {"lat": SADOVAYA[0], "lon": SADOVAYA[1]}
    assert "metro" in payload["transport"]
    assert "bus" in payload["transport"]


async def test_dgis_car_route_is_traffic_aware() -> None:
    session, factory = session_with(ok({"result": [{"total_duration": 1234}]}))
    router = DgisRouter("ключ-2гис", session_factory=factory)

    travel = await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)

    assert travel == TravelTime(minutes=21, traffic_aware=True, source=SOURCE_DGIS)
    assert travel.is_rough is False
    assert session.calls[0].url.startswith(DRIVING_URL)
    assert "ключ-2гис" in session.calls[0].url


async def test_dgis_public_transport_uses_other_api_and_no_traffic() -> None:
    """Транспорт считает другой сервис 2ГИС, где пробок нет — обещать их нельзя."""
    session, factory = session_with(ok({"result": [{"total_duration": 2000}]}))
    router = DgisRouter("ключ-2гис", session_factory=factory)

    travel = await router.travel_time(HOME, SADOVAYA, MODE_PUBLIC, WED_MORNING)

    assert travel.minutes == 34
    assert travel.traffic_aware is False
    assert travel.source == SOURCE_DGIS
    assert session.calls[0].url.startswith(PUBLIC_TRANSPORT_URL)


async def test_dgis_without_key_refuses_to_start() -> None:
    """Пустой ключ — ошибка на месте, а не непонятный HTTP 401 через десять секунд."""
    with pytest.raises(RouterError, match="DGIS_API_KEY"):
        DgisRouter("")


async def test_dgis_no_route_becomes_router_error() -> None:
    _, factory = session_with(ok({"result": []}))
    router = DgisRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)


async def test_dgis_http_error_becomes_router_error() -> None:
    _, factory = session_with(FakeResponse(status=500, body="server error"))
    router = DgisRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)


async def test_dgis_timeout_becomes_router_error() -> None:
    _, factory = session_with(FakeResponse(error=TimeoutError()))
    router = DgisRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)


async def test_dgis_broken_json_becomes_router_error() -> None:
    _, factory = session_with(FakeResponse(status=200, body="<html>тех. работы</html>"))
    router = DgisRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)


@pytest.mark.parametrize(
    ("origin", "destination", "mode", "at"),
    [
        pytest.param(HOME, SADOVAYA, MODE_CAR, datetime(2026, 9, 2, 8, 0), id="naive время"),
        pytest.param(HOME, SADOVAYA, "самолёт", WED_MORNING, id="незнакомый транспорт"),
        pytest.param((1000.0, 0.0), SADOVAYA, MODE_CAR, WED_MORNING, id="кривая точка старта"),
    ],
)
async def test_dgis_checks_arguments_before_going_to_network(
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    at: datetime,
) -> None:
    """Кривые аргументы отсекаются до запроса — лишний поход в сеть не нужен."""
    session, factory = session_with(ok({"result": [{"total_duration": 60}]}))
    router = DgisRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(origin, destination, mode, at)

    assert session.calls == []


# ======================================================================================
# 6. OpenRouteService: час пик вместо пробок
# ======================================================================================


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        pytest.param(datetime(2026, 9, 2, 6, 59, tzinfo=MOSCOW), False, id="ср 06:59 — рано"),
        pytest.param(datetime(2026, 9, 2, 7, 0, tzinfo=MOSCOW), True, id="ср 07:00 — начало"),
        pytest.param(datetime(2026, 9, 2, 8, 30, tzinfo=MOSCOW), True, id="ср 08:30 — утро"),
        pytest.param(datetime(2026, 9, 2, 9, 59, tzinfo=MOSCOW), True, id="ср 09:59 — ещё пик"),
        pytest.param(datetime(2026, 9, 2, 10, 0, tzinfo=MOSCOW), False, id="ср 10:00 — уже нет"),
        pytest.param(datetime(2026, 9, 2, 13, 0, tzinfo=MOSCOW), False, id="ср 13:00 — день"),
        pytest.param(datetime(2026, 9, 2, 17, 0, tzinfo=MOSCOW), True, id="ср 17:00 — вечер"),
        pytest.param(datetime(2026, 9, 2, 19, 59, tzinfo=MOSCOW), True, id="ср 19:59 — ещё пик"),
        pytest.param(datetime(2026, 9, 2, 20, 0, tzinfo=MOSCOW), False, id="ср 20:00 — уже нет"),
        pytest.param(datetime(2026, 9, 5, 8, 30, tzinfo=MOSCOW), False, id="сб 08:30 — выходной"),
        pytest.param(datetime(2026, 9, 5, 18, 0, tzinfo=MOSCOW), False, id="сб 18:00 — выходной"),
        pytest.param(datetime(2026, 9, 6, 8, 30, tzinfo=MOSCOW), False, id="вс 08:30 — выходной"),
        pytest.param(datetime(2026, 9, 7, 8, 0, tzinfo=MOSCOW), True, id="пн 08:00 — будни"),
        pytest.param(datetime(2026, 9, 4, 18, 0, tzinfo=MOSCOW), True, id="пт 18:00 — будни"),
    ],
)
def test_is_peak_hour(moment: datetime, expected: bool) -> None:
    """Часы пик: будни 7–10 и 17–20 по Москве. Выходные не считаются."""
    assert is_peak_hour(moment) is expected


def test_is_peak_hour_converts_to_moscow() -> None:
    """05:00 UTC — это 08:00 в Москве, то есть утренний час пик."""
    assert is_peak_hour(datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)) is True
    # 08:00 UTC — уже 11:00 по Москве, час пик кончился.
    assert is_peak_hour(datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)) is False


def test_is_peak_hour_rejects_naive_datetime() -> None:
    with pytest.raises(RouterError):
        is_peak_hour(datetime(2026, 9, 2, 8, 0))


def test_apply_peak_factor_stretches_only_in_peak() -> None:
    assert apply_peak_factor(600, WED_MORNING, 1.5) == 900
    assert apply_peak_factor(600, WED_MIDDAY, 1.5) == 600


def test_apply_peak_factor_default_comes_from_config() -> None:
    """Коэффициент по умолчанию — тот же, что в .env.example (1.4)."""
    assert apply_peak_factor(600, WED_MORNING) == 600 * DEFAULT_ORS_PEAK_HOUR_FACTOR
    assert DEFAULT_ORS_PEAK_HOUR_FACTOR > 1.0


def test_apply_peak_factor_does_not_touch_weekend() -> None:
    saturday = datetime(2026, 9, 5, 15, 0, tzinfo=MOSCOW)
    assert apply_peak_factor(600, saturday, 2.0) == 600


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"routes": [{"summary": {"duration": 900.4}}]}, 900.4, id="обычный ответ"),
        pytest.param(
            {"features": [{"properties": {"summary": {"duration": 600}}}]},
            600.0,
            id="GeoJSON",
        ),
        pytest.param(
            {"routes": [{"summary": {"duration": 900}}, {"summary": {"duration": 700}}]},
            700.0,
            id="берём самый быстрый",
        ),
    ],
)
def test_ors_parse_duration_seconds(payload: object, expected: float) -> None:
    assert ors_module.parse_duration_seconds(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"routes": []}, id="маршрутов нет"),
        pytest.param({"routes": [{"summary": {}}]}, id="в сводке нет длительности"),
        pytest.param({"routes": [{"summary": {"duration": "долго"}}]}, id="длительность словами"),
        pytest.param({"error": {"message": "ключ не принят"}}, id="ошибка сервиса"),
        pytest.param({"error": "лимит запросов"}, id="ошибка строкой"),
        pytest.param({"что-то": "другое"}, id="ни routes, ни features"),
        pytest.param([1, 2, 3], id="ответ не объект"),
        pytest.param("текст", id="ответ строкой"),
    ],
)
def test_ors_parse_duration_seconds_rejects_bad_payload(payload: object) -> None:
    with pytest.raises(RouterError):
        ors_module.parse_duration_seconds(payload)


@pytest.mark.parametrize(
    ("mode", "profile"),
    [
        pytest.param(MODE_CAR, PROFILE_CAR, id="машина"),
        pytest.param(MODE_PUBLIC, PROFILE_PUBLIC, id="транспорт — велосипед как приближение"),
    ],
)
def test_ors_profile_for(mode: str, profile: str) -> None:
    """Городского транспорта в бесплатном ORS нет: вместо него cycling-regular."""
    assert OrsRouter.profile_for(mode) == profile


def test_ors_payload_uses_lon_lat_order() -> None:
    """У ORS координаты идут наоборот: сначала долгота. Перепутать — уехать в другой город."""
    payload = OrsRouter.payload(HOME, SADOVAYA)

    assert payload["coordinates"] == [[HOME[1], HOME[0]], [SADOVAYA[1], SADOVAYA[0]]]


async def test_ors_outside_peak_hour_returns_plain_duration() -> None:
    session, factory = session_with(ok({"routes": [{"summary": {"duration": 600}}]}))
    router = OrsRouter("ключ-ors", peak_hour_factor=2.0, session_factory=factory)

    travel = await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MIDDAY)

    assert travel == TravelTime(minutes=10, traffic_aware=False, source=SOURCE_ORS)
    assert travel.is_rough is False
    assert session.calls[0].url.endswith(PROFILE_CAR)
    assert session.calls[0].headers is not None
    assert session.calls[0].headers["Authorization"] == "ключ-ors"


async def test_ors_in_peak_hour_applies_factor() -> None:
    """Пробок ORS не знает, поэтому утро растягивается коэффициентом из .env."""
    _, factory = session_with(ok({"routes": [{"summary": {"duration": 600}}]}))
    router = OrsRouter("ключ-ors", peak_hour_factor=2.0, session_factory=factory)

    travel = await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MORNING)

    assert travel.minutes == 20
    # Но «пробки учтены» пользователю не обещаем: это оценка, а не настоящие пробки.
    assert travel.traffic_aware is False


async def test_ors_public_transport_uses_cycling_profile() -> None:
    session, factory = session_with(ok({"routes": [{"summary": {"duration": 900}}]}))
    router = OrsRouter("ключ-ors", session_factory=factory)

    travel = await router.travel_time(HOME, SADOVAYA, MODE_PUBLIC, WED_MIDDAY)

    assert travel.minutes == 15
    assert session.calls[0].url.endswith(PROFILE_PUBLIC)


async def test_ors_without_key_refuses_to_start() -> None:
    with pytest.raises(RouterError, match="ORS_API_KEY"):
        OrsRouter("")


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(FakeResponse(error=TimeoutError()), id="таймаут"),
        pytest.param(FakeResponse(status=403, body="forbidden"), id="ключ не принят"),
        pytest.param(FakeResponse(status=503, body="перегрузка"), id="сервис лежит"),
        pytest.param(FakeResponse(status=200, body="<html>"), id="битый JSON"),
        pytest.param(ok({"routes": []}), id="маршрута нет"),
    ],
)
async def test_ors_failures_become_router_error(response: FakeResponse) -> None:
    _, factory = session_with(response)
    router = OrsRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(HOME, SADOVAYA, MODE_CAR, WED_MIDDAY)


async def test_ors_rejects_naive_datetime_before_request() -> None:
    session, factory = session_with(ok({"routes": [{"summary": {"duration": 600}}]}))
    router = OrsRouter("ключ", session_factory=factory)

    with pytest.raises(RouterError):
        await router.travel_time(HOME, SADOVAYA, MODE_CAR, datetime(2026, 9, 2, 8, 0))

    assert session.calls == []


# ======================================================================================
# 7. Фабрика: кто считает дорогу
# ======================================================================================


def test_create_router_picks_dgis_when_key_is_set() -> None:
    router = create_router(make_config(ROUTER="dgis", DGIS_API_KEY="ключ-2гис"))

    assert isinstance(router, DgisRouter)


def test_create_router_picks_ors_when_key_is_set() -> None:
    router = create_router(
        make_config(ROUTER="ors", ORS_API_KEY="ключ-ors", ORS_PEAK_HOUR_FACTOR="1.6")
    )

    assert isinstance(router, OrsRouter)
    # Коэффициент часа пик должен доехать из .env до маршрутизатора.
    assert router._peak_hour_factor == pytest.approx(1.6)


def test_create_router_falls_back_to_stub_without_dgis_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ключа нет — бот не падает и не отключает /route, а считает грубо и предупреждает."""
    with caplog.at_level(logging.WARNING, logger="app.routing.factory"):
        router = create_router(make_config(ROUTER="dgis"))

    assert isinstance(router, StubRouter)
    assert caplog.records, "Ожидали предупреждение в логе про отсутствующий ключ"
    assert ".env" in caplog.text


def test_create_router_falls_back_to_stub_without_ors_key() -> None:
    assert isinstance(create_router(make_config(ROUTER="ors")), StubRouter)


def test_create_router_ignores_key_of_the_other_service() -> None:
    """Выбрали 2ГИС, а заполнили ключ ORS — считаем прикидкой, а не чужим сервисом."""
    config = make_config(ROUTER="dgis", ORS_API_KEY="ключ-ors")

    assert isinstance(create_router(config), StubRouter)


def test_create_router_default_is_dgis() -> None:
    """ROUTER в .env не заполнен — основным считается 2ГИС из ТЗ."""
    config = make_config(DGIS_API_KEY="ключ-2гис")

    assert config.router == "dgis"
    assert isinstance(create_router(config), DgisRouter)


async def test_stub_from_factory_still_answers() -> None:
    """Главное свойство запасного пути: без ключей /route всё равно даёт число."""
    router = create_router(make_config())

    travel = await router.travel_time(HOME, VOZNESENSKY, MODE_CAR, WED_MORNING)

    assert travel.minutes >= 1
    assert travel.is_rough is True
    await router.close()


def test_travel_time_of_every_router_fits_one_interface() -> None:
    """Все реализации возвращают один и тот же тип — хендлер не знает, кто считал."""
    for router in (
        StubRouter(),
        DgisRouter("ключ"),
        OrsRouter("ключ"),
    ):
        assert hasattr(router, "travel_time")
        assert hasattr(router, "close")


def test_moscow_is_three_hours_ahead_of_utc() -> None:
    """Опора всех расчётов времени: Москва = UTC+3 без переходов на летнее время."""
    assert WED_MORNING.utcoffset() == timedelta(hours=3)
