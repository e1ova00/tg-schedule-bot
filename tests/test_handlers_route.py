"""Тесты команды `/route`: сколько ехать до корпуса ближайшей очной пары.

Telegram и сеть не поднимаются: сообщение — `FakeMessage` из `tests/fakes.py`, база —
временная в памяти, маршрутизатор передаётся аргументом (в бою это делает
`RouterMiddleware` под ключом `travel_router`).

«Сейчас» подменяется через `app.clock.now`, поэтому результат не зависит от реального дня.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone

import aiosqlite
import pytest
from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Chat, Message, User

from app import buildings, clock, texts, users, views
from app.clock import MOSCOW
from app.handlers.route import handle_route
from app.routing.base import Router as TravelRouter
from app.routing.base import SOURCE_DGIS, SOURCE_ORS, SOURCE_STUB, RouterError, TravelTime
from app.routing.stub import StubRouter
from app.schedule import Lesson, ScheduleError, clear_cache
from fakes import USER_ID, FakeMessage, FakeUser, make_onboarded_user

FAKE_TOKEN = "123456789:AAHfake-token-for-tests-only-000000000"

MON_ODD = date(2026, 8, 31)
TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
SUN_ODD = date(2026, 9, 6)
TUE_EVEN = date(2026, 9, 8)

VOZNESENSKY = "пр. Вознесенский, д. 46"
SADOVAYA = "ул. Садовая, д. 54"


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> Callable[[date, int, int], datetime]:
    """Подменяет «сейчас» по Москве, не трогая системные часы."""

    def _freeze(day: date, hour: int, minute: int = 0) -> datetime:
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)
        monkeypatch.setattr(clock, "now", lambda *args, **kwargs: moment)
        return moment

    return _freeze


class RecordingRouter(TravelRouter):
    """Маршрутизатор-свидетель: запоминает, о чём его спросили, и отдаёт заданный ответ."""

    def __init__(
        self,
        result: TravelTime | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or TravelTime(minutes=25, traffic_aware=True, source=SOURCE_DGIS)
        self.error = error
        self.calls: list[tuple[tuple[float, float], tuple[float, float], str, datetime]] = []

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        self.calls.append((origin, destination, mode, at))
        if self.error is not None:
            raise self.error
        return self.result


# ======================================================================================
# 1. Пользователь ещё не настроен
# ======================================================================================


async def test_route_without_onboarding_asks_to_start(db: aiosqlite.Connection) -> None:
    """Считать дорогу не от чего: точки старта нет."""
    message = FakeMessage(text="/route")

    await handle_route(message, db, StubRouter())  # type: ignore[arg-type]

    assert message.answers == [texts.ROUTE_NOT_READY]
    assert "/start" in message.last_answer


async def test_route_with_half_finished_onboarding_asks_to_start(
    db: aiosqlite.Connection,
) -> None:
    """Точка есть, но знакомство не закончено — считаем настройку незавершённой."""
    await users.save_user_fields(db, USER_ID, latitude=59.9386, longitude=30.3141)
    message = FakeMessage(text="/route")

    await handle_route(message, db, StubRouter())  # type: ignore[arg-type]

    assert message.answers == [texts.ROUTE_NOT_READY]


async def test_route_ignores_message_without_author(db: aiosqlite.Connection) -> None:
    """Сообщение без автора (канал) — просто молчим, а не падаем на None.id."""
    message = FakeMessage(text="/route", from_user=None)

    await handle_route(message, db, StubRouter())  # type: ignore[arg-type]

    assert message.answers == []


# ======================================================================================
# 2. Ехать некуда
# ======================================================================================


async def test_route_says_no_trips_on_weekend_before_remote_monday(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Воскресенье + дистанционный понедельник = ехать никуда не надо."""
    await make_onboarded_user(db)
    freeze_now(SUN_ODD, 9)
    router = RecordingRouter()
    message = FakeMessage(text="/route")

    await handle_route(message, db, router)  # type: ignore[arg-type]

    assert message.answers == [texts.ROUTE_NO_TRIPS]
    assert router.calls == [], "Без поездки маршрутизатор дёргать незачем"


async def test_route_stays_silent_about_trips_on_remote_monday_evening(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Понедельник вечером: сегодня всё дистанционно, но завтра вторник — поездка есть."""
    await make_onboarded_user(db)
    freeze_now(MON_ODD, 18)
    message = FakeMessage(text="/route")

    await handle_route(message, db, RecordingRouter())  # type: ignore[arg-type]

    assert message.last_answer != texts.ROUTE_NO_TRIPS
    assert "Вознесенский, 46" in message.last_answer
    assert "завтра" in message.last_answer.lower()


# ======================================================================================
# 3. Успешный расчёт
# ======================================================================================


async def test_route_with_stub_router_warns_that_it_is_a_rough_guess(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Главный сценарий приёмки без ключей: число есть, но помечено как прикидка."""
    await make_onboarded_user(db, transport_mode="car")
    freeze_now(WED_ODD, 8)
    message = FakeMessage(text="/route")

    await handle_route(message, db, StubRouter())  # type: ignore[arg-type]

    answer = message.last_answer
    assert "Вознесенский, 46" in answer
    assert "10:05" in answer
    assert "на машине" in answer
    assert "6 минут" in answer  # по прямой от дома тестового пользователя
    assert texts.ROUTE_NOTE_ROUGH in answer
    assert "грубая прикидка" in answer


async def test_route_takes_the_next_lesson_and_its_own_building(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Среда, 12:00: ехать надо на пару в 13:45 и на Садовую, а не на Вознесенский."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 12)
    message = FakeMessage(text="/route")

    await handle_route(message, db, StubRouter())  # type: ignore[arg-type]

    answer = message.last_answer
    assert "Садовая, 54" in answer
    assert "13:45" in answer
    assert "Вознесенский" not in answer


async def test_route_destination_depends_on_week_parity(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Вторник по числителю — Вознесенский, по знаменателю — Садовая (правило 3 из ТЗ)."""
    await make_onboarded_user(db)

    freeze_now(TUE_ODD, 8)
    odd_message = FakeMessage(text="/route")
    await handle_route(odd_message, db, StubRouter())  # type: ignore[arg-type]

    freeze_now(TUE_EVEN, 8)
    even_message = FakeMessage(text="/route")
    await handle_route(even_message, db, StubRouter())  # type: ignore[arg-type]

    assert "Вознесенский, 46" in odd_message.last_answer
    assert "Садовая, 54" in even_message.last_answer


async def test_route_passes_aware_time_and_real_coordinates(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Маршрутизатор получает точку пользователя, координаты корпуса и время с поясом."""
    await make_onboarded_user(db, latitude=59.95, longitude=30.35, transport_mode="car")
    moment = freeze_now(WED_ODD, 8)
    router = RecordingRouter()

    await handle_route(FakeMessage(text="/route"), db, router)  # type: ignore[arg-type]

    assert len(router.calls) == 1
    origin, destination, mode, at = router.calls[0]
    assert origin == (59.95, 30.35)
    assert destination == buildings.building_coordinates(VOZNESENSKY)
    assert mode == "car"
    assert at == moment
    assert at.tzinfo is not None, "Naive время сломало бы расчёт на UTC-сервере"


async def test_route_prefers_coordinates_saved_in_database(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Кэш геокодинга главнее констант: уточнённые координаты корпуса должны применяться."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)
    await buildings.save_building(
        db,
        buildings.Building(
            address=VOZNESENSKY, title="Вознесенский, 46", latitude=59.91, longitude=30.31
        ),
    )
    router = RecordingRouter()

    await handle_route(FakeMessage(text="/route"), db, router)  # type: ignore[arg-type]

    _, destination, _, _ = router.calls[0]
    assert destination == (59.91, 30.31)


async def test_route_for_public_transport_uses_its_own_wording(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    await make_onboarded_user(db, transport_mode="public_transport")
    freeze_now(WED_ODD, 8)
    router = RecordingRouter()

    message = FakeMessage(text="/route")
    await handle_route(message, db, router)  # type: ignore[arg-type]

    assert router.calls[0][2] == "public_transport"
    assert "на общественном транспорте" in message.last_answer


@pytest.mark.parametrize(
    ("travel", "expected_note"),
    [
        pytest.param(
            TravelTime(minutes=25, traffic_aware=True, source=SOURCE_DGIS),
            texts.ROUTE_NOTE_TRAFFIC,
            id="2ГИС с пробками",
        ),
        pytest.param(
            TravelTime(minutes=25, traffic_aware=False, source=SOURCE_ORS),
            texts.ROUTE_NOTE_NO_TRAFFIC,
            id="ORS без пробок",
        ),
        pytest.param(
            TravelTime(minutes=25, traffic_aware=False, source=SOURCE_STUB),
            texts.ROUTE_NOTE_ROUGH,
            id="прикидка по прямой",
        ),
    ],
)
async def test_route_note_matches_the_source(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    travel: TravelTime,
    expected_note: str,
) -> None:
    """Оговорка в конце ответа должна соответствовать тому, кто считал дорогу."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)
    message = FakeMessage(text="/route")

    await handle_route(message, db, RecordingRouter(result=travel))  # type: ignore[arg-type]

    assert expected_note in message.last_answer
    assert "25 минут" in message.last_answer


# ======================================================================================
# 4. Сбои: бот отвечает, а не падает
# ======================================================================================


async def test_route_reports_router_failure(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Сервис маршрутов упал — человек получает понятный текст, бот продолжает работать."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)
    router = RecordingRouter(error=RouterError("2ГИС: ответ не пришёл за 10 с"))
    message = FakeMessage(text="/route")

    await handle_route(message, db, router)  # type: ignore[arg-type]

    assert message.answers == [texts.ROUTE_FAILED]
    # Технических подробностей в чат не приносим — они уходят в лог.
    assert "RouterError" not in message.last_answer
    assert "Traceback" not in message.last_answer


async def test_route_survives_unexpected_exception(
    db: aiosqlite.Connection, freeze_now: Callable[..., datetime]
) -> None:
    """Даже если маршрутизатор упал не тем исключением, /route не роняет бота."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)
    router = RecordingRouter(error=RuntimeError("что-то совсем неожиданное"))
    message = FakeMessage(text="/route")

    await handle_route(message, db, router)  # type: ignore[arg-type]

    assert message.answers == [texts.ROUTE_FAILED]


async def test_route_reports_broken_schedule(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)

    def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr("app.handlers.route.upcoming_offline_lesson", boom)
    message = FakeMessage(text="/route")

    await handle_route(message, db, RecordingRouter())  # type: ignore[arg-type]

    assert message.answers == [texts.SCHEDULE_ERROR]


async def test_route_reports_unknown_building(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Координат корпуса нет нигде — честный отказ вместо запроса в никуда."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)
    monkeypatch.setattr(buildings, "building_coordinates", lambda address: None)
    router = RecordingRouter()
    message = FakeMessage(text="/route")

    await handle_route(message, db, router)  # type: ignore[arg-type]

    assert message.answers == [texts.ROUTE_FAILED]
    assert router.calls == []


async def test_route_survives_broken_database(
    db: aiosqlite.Connection,
    freeze_now: Callable[..., datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кэш координат не читается — берём координаты из констант и всё равно отвечаем."""
    await make_onboarded_user(db)
    freeze_now(WED_ODD, 8)

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(buildings, "get_building", boom)
    message = FakeMessage(text="/route")

    await handle_route(message, db, StubRouter())  # type: ignore[arg-type]

    assert texts.ROUTE_FAILED not in message.answers
    assert "Вознесенский, 46" in message.last_answer


# ======================================================================================
# 5. Тексты и подключение команды
# ======================================================================================


@pytest.mark.parametrize(
    ("travel", "expected"),
    [
        pytest.param(
            TravelTime(minutes=10, traffic_aware=True, source=SOURCE_DGIS),
            texts.ROUTE_NOTE_TRAFFIC,
            id="настоящие пробки",
        ),
        pytest.param(
            TravelTime(minutes=10, traffic_aware=False, source=SOURCE_ORS),
            texts.ROUTE_NOTE_NO_TRAFFIC,
            id="без пробок",
        ),
        pytest.param(
            TravelTime(minutes=10, traffic_aware=False, source=SOURCE_STUB),
            texts.ROUTE_NOTE_ROUGH,
            id="прикидка",
        ),
    ],
)
def test_travel_note(travel: TravelTime, expected: str) -> None:
    assert views.travel_note(travel) == expected


def test_rough_note_is_visibly_a_warning() -> None:
    """Пользователь не программист: пометка должна читаться как предупреждение."""
    assert "грубая прикидка" in texts.ROUTE_NOTE_ROUGH
    assert "ключ" in texts.ROUTE_NOTE_ROUGH


def test_format_route_escapes_html() -> None:
    """Текст уходит с ParseMode.HTML — угловые скобки в данных не должны ломать сообщение."""
    lesson = Lesson(
        id="T1",
        weekday=3,
        start=time(10, 5),
        end=time(11, 30),
        parity="both",
        subject="Алгоритмы <A & B>",
        kind="Пр",
        teacher="Иванов",
        building=VOZNESENSKY,
        room="В 484",
        is_remote=False,
    )

    text = views.format_route(
        WED_ODD,
        lesson,
        TravelTime(minutes=10, traffic_aware=True, source=SOURCE_DGIS),
        "car",
        WED_ODD,
    )

    assert "&lt;A &amp; B&gt;" in text
    assert "Алгоритмы <A" not in text


def make_message(text: str) -> Message:
    """Настоящий объект aiogram Message — нужен фильтру Command."""
    return Message(
        message_id=1,
        date=datetime.now(tz=timezone.utc),
        chat=Chat(id=USER_ID, type="private"),
        from_user=User(id=USER_ID, is_bot=False, first_name="Тестер"),
        text=text,
    )


@pytest.fixture()
async def offline_bot():
    bot = Bot(token=FAKE_TOKEN)
    try:
        yield bot
    finally:
        await bot.session.close()


async def test_route_command_filter_matches(offline_bot: Bot) -> None:
    assert await Command("route")(make_message("/route"), bot=offline_bot)


@pytest.mark.parametrize("text", ["/routes", "route", "/rout"])
async def test_route_command_filter_ignores_lookalikes(offline_bot: Bot, text: str) -> None:
    assert not await Command("route")(make_message(text), bot=offline_bot)


def test_route_router_is_registered_before_fallback(root_router: Router) -> None:
    """Иначе /route поймает fallback и ответит «не знаю такой команды»."""
    names = [child.name for child in root_router.sub_routers]

    assert "route" in names
    assert names.index("route") < names.index("fallback")


def test_route_is_listed_in_help() -> None:
    """Команду должно быть видно человеку, а не только в коде."""
    assert "/route" in texts.HELP_TEXT


def test_fake_user_id_matches_fixtures() -> None:
    assert FakeUser().id == USER_ID
