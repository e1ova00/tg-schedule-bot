"""Тесты планировщика будильников: app/scheduler.py.

Ни настоящего APScheduler, ни Telegram, ни сети, ни ожидания реального утра:

* планировщик подменён `FakeScheduler` — он просто запоминает поставленные задачи;
* бот подменён `FakeBot` — он складывает отправленные сообщения в список
  и умеет «сломаться» по требованию;
* «сейчас» всегда передаётся параметром `moment`;
* база настоящая, но временная (в памяти), поэтому таблица `alarm_log` проверяется
  как есть, без подделки.

Самое важное здесь — две вещи, которые дороже всего стоят живому человеку:
не разбудить, когда надо (пропущенный будильник), и разбудить дважды за одно утро.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import aiosqlite
import pytest

from app import alarm, clock, db as db_module, scheduler as scheduler_module, texts, users
from app.clock import MOSCOW
from app.config import Config, build_config
from app.routing.base import SOURCE_DGIS, Router as TravelRouter, RouterError, TravelTime
from app.schedule import ScheduleError, clear_cache
from app.scheduler import PLANNING_JOB_ID, AlarmScheduler, alarm_job_id
from fakes import USER_ID, make_onboarded_user

MON_ODD = date(2026, 8, 31)
WED_ODD = date(2026, 9, 2)  # первая очная пара 10:05, Вознесенский
THU_ODD = date(2026, 9, 3)  # пар нет
THU_EVEN = date(2026, 9, 10)  # первая очная пара 10:05, Вознесенский

OTHER_USER_ID = 777


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


def msk(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)


class FakeJob:
    """Запись о задаче, которую бот поставил планировщику."""

    def __init__(self, func: Any, kwargs: dict[str, Any]) -> None:
        self.func = func
        self.trigger = kwargs.get("trigger")
        self.run_date = kwargs.get("run_date")
        self.kwargs = kwargs.get("kwargs") or {}
        self.id = kwargs.get("id")
        self.hour = kwargs.get("hour")
        self.minute = kwargs.get("minute")
        self.extra = kwargs


class FakeScheduler:
    """Замена AsyncIOScheduler: ничего не запускает, только запоминает вызовы."""

    def __init__(self) -> None:
        self.jobs: list[FakeJob] = []
        self.running = False
        self.shutdown_calls: list[bool] = []

    def add_job(self, func: Any, **kwargs: Any) -> FakeJob:
        job = FakeJob(func, kwargs)
        # replace_existing=True в бою заменяет задачу с тем же id — повторяем поведение,
        # иначе тест не заметил бы удвоения будильников.
        if job.id is not None and kwargs.get("replace_existing"):
            self.jobs = [item for item in self.jobs if item.id != job.id]
        self.jobs.append(job)
        return job

    def start(self) -> None:
        self.running = True

    def shutdown(self, wait: bool = True) -> None:
        self.running = False
        self.shutdown_calls.append(wait)

    def job(self, job_id: str) -> FakeJob | None:
        for item in self.jobs:
            if item.id == job_id:
                return item
        return None

    @property
    def alarm_jobs(self) -> list[FakeJob]:
        return [item for item in self.jobs if str(item.id).startswith("alarm:")]


class FakeBot:
    """Замена aiogram.Bot: собирает отправленные сообщения, при желании падает."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))

    @property
    def last_text(self) -> str:
        return self.sent[-1][1]


class CountingRouter(TravelRouter):
    """Маршрутизатор с заранее известным ответом и счётчиком вызовов."""

    def __init__(self, minutes: int = 25, error: BaseException | None = None) -> None:
        self.minutes = minutes
        self.error = error
        self.calls: list[datetime] = []

    async def travel_time(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        at: datetime,
    ) -> TravelTime:
        self.calls.append(at)
        if self.error is not None:
            raise self.error
        return TravelTime(minutes=self.minutes, traffic_aware=True, source=SOURCE_DGIS)


def make_config(**env: str) -> Config:
    return build_config({"BOT_TOKEN": "123456:AAHfake-token", **env})


def make_scheduler(
    db: aiosqlite.Connection,
    *,
    bot: FakeBot | None = None,
    travel_router: TravelRouter | None = None,
    config: Config | None = None,
    fake_scheduler: FakeScheduler | None = None,
) -> tuple[AlarmScheduler, FakeBot, FakeScheduler, TravelRouter]:
    """Собирает AlarmScheduler на заглушках и отдаёт все части для проверок."""
    bot = bot or FakeBot()
    travel_router = travel_router or CountingRouter()
    fake_scheduler = fake_scheduler or FakeScheduler()
    alarms = AlarmScheduler(
        bot,  # type: ignore[arg-type]
        db,
        travel_router,
        config or make_config(),
        scheduler=fake_scheduler,  # type: ignore[arg-type]
    )
    return alarms, bot, fake_scheduler, travel_router


# ======================================================================================
# 1. Жизненный цикл: ежедневный пересчёт и остановка
# ======================================================================================


async def test_start_registers_daily_planning_job(db: aiosqlite.Connection) -> None:
    alarms, _, fake, _ = make_scheduler(db, config=make_config(ALARM_PLANNING_TIME="05:30"))

    alarms.start()

    job = fake.job(PLANNING_JOB_ID)
    assert job is not None
    assert job.trigger == "cron"
    assert (job.hour, job.minute) == (5, 30)
    assert job.func == alarms.plan_today
    assert job.extra.get("replace_existing") is True, "Иначе перезапуск удвоит пересчёт"
    assert fake.running is True


async def test_shutdown_stops_started_scheduler(db: aiosqlite.Connection) -> None:
    alarms, _, fake, _ = make_scheduler(db)
    alarms.start()

    await alarms.shutdown()

    assert fake.shutdown_calls == [False]
    assert fake.running is False


async def test_shutdown_without_start_does_nothing(db: aiosqlite.Connection) -> None:
    """Бот мог упасть до старта планировщика — второй раз падать в finally нельзя."""
    alarms, _, fake, _ = make_scheduler(db)

    await alarms.shutdown()

    assert fake.shutdown_calls == []


# ======================================================================================
# 2. Планирование на будущее: задача с правильным временем
# ======================================================================================


async def test_future_alarm_is_scheduled_with_exact_wake_time(
    db: aiosqlite.Connection,
) -> None:
    """Пара 10:05, дорога 25, запас 10, сборы 30 → задача ровно на 09:00."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    alarms, bot, fake, _ = make_scheduler(db, travel_router=CountingRouter(minutes=25))
    user = await users.get_user(db, USER_ID)
    assert user is not None

    decision = await alarms.plan_for_user(user, THU_EVEN, msk(THU_EVEN, 5, 0))

    assert decision == alarm.DECISION_SCHEDULE
    job = fake.job(alarm_job_id(USER_ID, THU_EVEN))
    assert job is not None
    assert job.trigger == "date"
    assert job.run_date == msk(THU_EVEN, 9, 0)
    assert job.run_date.tzinfo is not None
    assert job.kwargs == {"telegram_id": USER_ID, "day": THU_EVEN}
    assert job.func == alarms.send_alarm
    assert bot.sent == [], "На этапе планирования писать человеку рано"
    assert await db_module.already_sent(db, USER_ID, THU_EVEN) is False


async def test_planning_twice_does_not_duplicate_the_job(db: aiosqlite.Connection) -> None:
    """Повторный пересчёт (например, после /start бота) заменяет задачу, а не плодит."""
    await make_onboarded_user(db)
    alarms, _, fake, _ = make_scheduler(db)

    await alarms.plan_today(msk(THU_EVEN, 5, 0))
    await alarms.plan_today(msk(THU_EVEN, 5, 10))

    assert len(fake.alarm_jobs) == 1


@pytest.mark.parametrize("day", [MON_ODD, THU_ODD], ids=["дистант", "пустой день"])
async def test_silent_days_produce_no_job_and_no_router_calls(
    db: aiosqlite.Connection, day: date
) -> None:
    await make_onboarded_user(db)
    travel_router = CountingRouter()
    alarms, bot, fake, _ = make_scheduler(db, travel_router=travel_router)

    decisions = await alarms.plan_today(msk(day, 5, 0))

    assert decisions == [alarm.DECISION_SILENT]
    assert fake.alarm_jobs == []
    assert bot.sent == []
    assert travel_router.calls == []


# ======================================================================================
# 3. Бот включился позже расчётного подъёма
# ======================================================================================


async def test_late_start_wakes_immediately(db: aiosqlite.Connection) -> None:
    """Сценарий из отчёта: подъём 06:40, выход 07:20, бот стартовал в 06:50."""
    # Пара в 10:05; дорога 150 + запас 15 → выход 07:20; сборы 40 → подъём 06:40.
    await make_onboarded_user(db, prep_minutes=40, buffer_minutes=15)
    alarms, bot, fake, _ = make_scheduler(db, travel_router=CountingRouter(minutes=150))
    user = await users.get_user(db, USER_ID)
    assert user is not None

    decision = await alarms.plan_for_user(user, WED_ODD, msk(WED_ODD, 6, 50))

    assert decision == alarm.DECISION_SEND_NOW
    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == USER_ID
    assert texts.ALARM_LATE_START in text
    assert "07:20" in text, "Человеку важно увидеть, во сколько выходить"
    assert fake.alarm_jobs == [], "Сообщение уже ушло, задачу ставить не на что"
    assert await db_module.already_sent(db, USER_ID, WED_ODD) is True


async def test_too_late_sends_nothing_and_does_not_crash(db: aiosqlite.Connection) -> None:
    """09:50 при выходе в 09:30 — будить незачем, но и падать не из-за чего."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    alarms, bot, fake, _ = make_scheduler(db, travel_router=CountingRouter(minutes=25))
    user = await users.get_user(db, USER_ID)
    assert user is not None

    decision = await alarms.plan_for_user(user, THU_EVEN, msk(THU_EVEN, 9, 50))

    assert decision == alarm.DECISION_TOO_LATE
    assert bot.sent == []
    assert fake.alarm_jobs == []
    assert await db_module.already_sent(db, USER_ID, THU_EVEN) is False


# ======================================================================================
# 4. plan_today: все пользователи, чужие ошибки, «сейчас» по умолчанию
# ======================================================================================


async def test_plan_today_skips_users_without_onboarding(
    db: aiosqlite.Connection,
) -> None:
    await make_onboarded_user(db)
    await users.ensure_user(db, OTHER_USER_ID, "новичок")
    alarms, _, fake, _ = make_scheduler(db)

    decisions = await alarms.plan_today(msk(THU_EVEN, 5, 0))

    assert decisions == [alarm.DECISION_SCHEDULE]
    assert [job.kwargs["telegram_id"] for job in fake.alarm_jobs] == [USER_ID]


async def test_plan_today_handles_two_users(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db, USER_ID, prep_minutes=30, buffer_minutes=10)
    await make_onboarded_user(db, OTHER_USER_ID, prep_minutes=60, buffer_minutes=10)
    alarms, _, fake, _ = make_scheduler(db, travel_router=CountingRouter(minutes=25))

    decisions = await alarms.plan_today(msk(THU_EVEN, 5, 0))

    assert decisions == [alarm.DECISION_SCHEDULE, alarm.DECISION_SCHEDULE]
    run_dates = {job.kwargs["telegram_id"]: job.run_date for job in fake.alarm_jobs}
    assert run_dates[USER_ID] == msk(THU_EVEN, 9, 0)
    assert run_dates[OTHER_USER_ID] == msk(THU_EVEN, 8, 30)


async def test_plan_today_respects_allowed_user_ids(db: aiosqlite.Connection) -> None:
    """Бот приватный: если список задан, будильник тоже только для своих.

    Раньше ALLOWED_USER_IDS фильтровал только входящие сообщения — убрать человека
    из списка не мешало ему по-прежнему получать утренние будильники.
    """
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    alarms, _, fake, _ = make_scheduler(db, config=make_config(ALLOWED_USER_IDS=str(USER_ID)))

    decisions = await alarms.plan_today(msk(THU_EVEN, 5, 0))

    assert decisions == [alarm.DECISION_SCHEDULE]
    assert [job.kwargs["telegram_id"] for job in fake.alarm_jobs] == [USER_ID]


async def test_plan_today_with_empty_allow_list_wakes_everyone(
    db: aiosqlite.Connection,
) -> None:
    """Пустой ALLOWED_USER_IDS — старое поведение: бот открыт всем онбордившимся."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    alarms, _, fake, _ = make_scheduler(db, config=make_config(ALLOWED_USER_IDS=""))

    decisions = await alarms.plan_today(msk(THU_EVEN, 5, 0))

    assert decisions == [alarm.DECISION_SCHEDULE, alarm.DECISION_SCHEDULE]
    assert {job.kwargs["telegram_id"] for job in fake.alarm_jobs} == {USER_ID, OTHER_USER_ID}


async def test_plan_today_uses_clock_now_when_moment_is_omitted(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В бою moment не передают: день и время берутся из app.clock.now()."""
    await make_onboarded_user(db)
    moment = msk(THU_EVEN, 5, 0)
    monkeypatch.setattr(clock, "now", lambda *args, **kwargs: moment)
    alarms, _, fake, _ = make_scheduler(db)

    decisions = await alarms.plan_today()

    assert decisions == [alarm.DECISION_SCHEDULE]
    assert fake.alarm_jobs[0].kwargs["day"] == THU_EVEN


async def test_one_broken_user_does_not_break_the_rest(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Утро остальных не должно зависеть от сбоя на одном человеке."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)

    async def selective_boom(conn: Any, telegram_id: int, day: date) -> bool:
        if telegram_id == USER_ID:
            raise RuntimeError("строка в базе испорчена")
        return False

    monkeypatch.setattr(db_module, "already_sent", selective_boom)
    alarms, _, fake, _ = make_scheduler(db)

    decisions = await alarms.plan_today(msk(THU_EVEN, 5, 0))

    assert decisions == [alarm.DECISION_SCHEDULE]
    assert [job.kwargs["telegram_id"] for job in fake.alarm_jobs] == [OTHER_USER_ID]


async def test_plan_today_survives_unreadable_user_list(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> list[users.User]:
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(users, "list_users", boom)
    alarms, bot, fake, _ = make_scheduler(db)

    assert await alarms.plan_today(msk(THU_EVEN, 5, 0)) == []
    assert bot.sent == []
    assert fake.alarm_jobs == []


async def test_broken_schedule_file_silences_planning(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_onboarded_user(db)

    async def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr(alarm, "build_alarm_plan", boom)
    alarms, bot, fake, _ = make_scheduler(db)
    user = await users.get_user(db, USER_ID)
    assert user is not None

    decision = await alarms.plan_for_user(user, THU_EVEN, msk(THU_EVEN, 5, 0))

    assert decision == alarm.DECISION_SILENT
    assert bot.sent == []
    assert fake.alarm_jobs == []


# ======================================================================================
# 5. send_alarm: текст, повторы и отказ Telegram
# ======================================================================================


async def test_send_alarm_sends_useful_text_and_marks_the_log(
    db: aiosqlite.Connection,
) -> None:
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    alarms, bot, _, _ = make_scheduler(db, travel_router=CountingRouter(minutes=25))

    sent = await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 0))

    assert sent is True
    text = bot.last_text
    assert texts.ALARM_GREETING in text
    assert texts.ALARM_LATE_START not in text
    assert "10:05" in text  # время пары
    assert "09:30" in text  # время выхода
    assert "Вознесенский, 46" in text
    assert "25 минут" in text
    assert await db_module.already_sent(db, USER_ID, THU_EVEN) is True


async def test_send_alarm_recomputes_the_route(db: aiosqlite.Connection) -> None:
    """Пробки к подъёму другие, чем в пять утра: маршрут считается заново перед отправкой."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    travel_router = CountingRouter(minutes=25)
    alarms, bot, _, _ = make_scheduler(db, travel_router=travel_router)
    user = await users.get_user(db, USER_ID)
    assert user is not None

    await alarms.plan_for_user(user, THU_EVEN, msk(THU_EVEN, 5, 0))
    travel_router.minutes = 40
    await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 0))

    assert len(travel_router.calls) == 2
    assert travel_router.calls[1] == msk(THU_EVEN, 9, 0)
    assert "40 минут" in bot.last_text
    assert "09:15" in bot.last_text, "Выход должен сдвинуться из-за новых пробок"


async def test_send_alarm_twice_sends_only_once(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db)
    alarms, bot, _, _ = make_scheduler(db)

    first = await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 0))
    second = await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 5))

    assert (first, second) == (True, False)
    assert len(bot.sent) == 1


async def test_restart_does_not_wake_a_second_time(db: aiosqlite.Connection) -> None:
    """Будильник ушёл, бота перезапустили — второй звонок за то же утро недопустим."""
    await make_onboarded_user(db, prep_minutes=40, buffer_minutes=15)
    first_run, first_bot, _, _ = make_scheduler(db, travel_router=CountingRouter(minutes=150))
    await first_run.plan_today(msk(WED_ODD, 6, 50))
    assert len(first_bot.sent) == 1

    # Новый процесс: свой планировщик, свой бот, та же база.
    second_run, second_bot, fake, _ = make_scheduler(
        db, travel_router=CountingRouter(minutes=150)
    )
    decisions = await second_run.plan_today(msk(WED_ODD, 6, 55))

    assert decisions == [alarm.DECISION_SILENT]
    assert second_bot.sent == []
    assert fake.alarm_jobs == []


async def test_failed_telegram_delivery_is_not_marked_as_sent(
    db: aiosqlite.Connection,
) -> None:
    """Если сообщение не ушло, отметки быть не должно — иначе будильник пропадёт молча."""
    await make_onboarded_user(db)
    broken_bot = FakeBot(error=RuntimeError("Telegram недоступен"))
    alarms, _, _, _ = make_scheduler(db, bot=broken_bot)

    sent = await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 0))

    assert sent is False
    assert await db_module.already_sent(db, USER_ID, THU_EVEN) is False


async def test_alarm_can_be_retried_after_failed_delivery(
    db: aiosqlite.Connection,
) -> None:
    """Продолжение предыдущего: после перезапуска бот пробует ещё раз и будит человека."""
    await make_onboarded_user(db)
    broken_bot = FakeBot(error=RuntimeError("Telegram недоступен"))
    failing, _, _, _ = make_scheduler(db, bot=broken_bot)
    await failing.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 0))

    retry, working_bot, _, _ = make_scheduler(db)
    sent = await retry.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 5))

    assert sent is True
    assert len(working_bot.sent) == 1
    assert await db_module.already_sent(db, USER_ID, THU_EVEN) is True


async def test_send_alarm_is_silent_after_the_lesson_started(
    db: aiosqlite.Connection,
) -> None:
    """Задача сработала с большим опозданием: пара уже идёт, будить бессмысленно."""
    await make_onboarded_user(db)
    alarms, bot, _, _ = make_scheduler(db)

    sent = await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 10, 30))

    assert sent is False
    assert bot.sent == []
    assert await db_module.already_sent(db, USER_ID, THU_EVEN) is False


async def test_send_alarm_exactly_at_lesson_start_is_silent(
    db: aiosqlite.Connection,
) -> None:
    """Граница: ровно 10:05 — пара уже началась."""
    await make_onboarded_user(db)
    alarms, bot, _, _ = make_scheduler(db)

    assert await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 10, 5)) is False
    assert bot.sent == []


async def test_send_alarm_a_minute_before_the_lesson_still_goes(
    db: aiosqlite.Connection,
) -> None:
    """Обратная граница: 10:04 — сообщение всё ещё уходит (лучше поздно, чем никогда)."""
    await make_onboarded_user(db)
    alarms, bot, _, _ = make_scheduler(db)

    assert await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 10, 4)) is True
    assert len(bot.sent) == 1


@pytest.mark.parametrize("day", [MON_ODD, THU_ODD], ids=["дистант", "пустой день"])
async def test_send_alarm_stays_silent_on_days_without_offline_lessons(
    db: aiosqlite.Connection, day: date
) -> None:
    await make_onboarded_user(db)
    alarms, bot, _, _ = make_scheduler(db)

    assert await alarms.send_alarm(USER_ID, day, moment=msk(day, 7, 0)) is False
    assert bot.sent == []


async def test_send_alarm_ignores_unknown_user(db: aiosqlite.Connection) -> None:
    alarms, bot, _, _ = make_scheduler(db)

    assert await alarms.send_alarm(12345, THU_EVEN, moment=msk(THU_EVEN, 9, 0)) is False
    assert bot.sent == []


async def test_send_alarm_ignores_half_configured_user(db: aiosqlite.Connection) -> None:
    await users.save_user_fields(db, USER_ID, latitude=59.9, longitude=30.3)
    alarms, bot, _, _ = make_scheduler(db)

    assert await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 9, 0)) is False
    assert bot.sent == []


async def test_send_alarm_uses_clock_now_by_default(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В бою задача вызывается без moment — «сейчас» берётся из app.clock.now()."""
    await make_onboarded_user(db)
    monkeypatch.setattr(clock, "now", lambda *args, **kwargs: msk(THU_EVEN, 9, 0))
    alarms, bot, _, _ = make_scheduler(db)

    assert await alarms.send_alarm(USER_ID, THU_EVEN) is True
    assert len(bot.sent) == 1


# ======================================================================================
# 6. Отказ сервиса маршрутов: будильник всё равно приходит
# ======================================================================================


async def test_alarm_is_sent_even_when_router_is_down(db: aiosqlite.Connection) -> None:
    """Правило этапа: упавший маршрутизатор — не повод не будить человека."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    alarms, bot, _, _ = make_scheduler(
        db,
        travel_router=CountingRouter(error=RouterError("2ГИС молчит")),
        config=make_config(ALARM_FALLBACK_TRAVEL_MINUTES="45"),
    )

    sent = await alarms.send_alarm(USER_ID, THU_EVEN, moment=msk(THU_EVEN, 8, 0))

    assert sent is True
    text = bot.last_text
    assert "45 минут" in text
    assert texts.ALARM_NOTE_FALLBACK in text
    assert "09:10" in text  # 10:05 − 45 дороги − 10 запаса


async def test_planning_with_broken_router_still_schedules(
    db: aiosqlite.Connection,
) -> None:
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    alarms, _, fake, _ = make_scheduler(
        db,
        travel_router=CountingRouter(error=TimeoutError("сеть отвалилась")),
        config=make_config(ALARM_FALLBACK_TRAVEL_MINUTES="45"),
    )

    decisions = await alarms.plan_today(msk(THU_EVEN, 5, 0))

    assert decisions == [alarm.DECISION_SCHEDULE]
    assert fake.alarm_jobs[0].run_date == msk(THU_EVEN, 8, 40)


# ======================================================================================
# 7. Мелочи, которые легко сломать
# ======================================================================================


def test_alarm_job_id_is_unique_per_user_and_day() -> None:
    assert alarm_job_id(1, THU_EVEN) == "alarm:1:2026-09-10"
    assert alarm_job_id(1, THU_EVEN) != alarm_job_id(2, THU_EVEN)
    assert alarm_job_id(1, THU_EVEN) != alarm_job_id(1, THU_EVEN + timedelta(days=1))


def test_misfire_grace_gives_an_hour() -> None:
    """Задача, опоздавшая на пару минут, всё ещё полезна — APScheduler не должен её съесть."""
    assert scheduler_module.MISFIRE_GRACE_SECONDS >= 600


async def test_scheduler_property_exposes_the_underlying_scheduler(
    db: aiosqlite.Connection,
) -> None:
    alarms, _, fake, _ = make_scheduler(db)

    assert alarms.scheduler is fake
