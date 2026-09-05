"""Планировщик утренних будильников поверх APScheduler.

Как это устроено (решение согласовано на этапе планирования этапа 5):

* Задачи живут только в памяти. Постоянное хранилище задач ради двух-трёх человек —
  лишняя тяжёлая зависимость, поэтому будильники пересчитываются заново при каждом
  старте бота и один раз в сутки рано утром (`ALARM_PLANNING_TIME`).
* Чтобы перезапуск бота не разбудил второй раз за то же утро, факт отправки пишется
  в таблицу `alarm_log`.
* Маршрут считается дважды: при планировании (чтобы вообще понять, во сколько будить)
  и ещё раз в момент отправки — пробки успевают измениться с пяти утра до семи.
* «Сейчас» во всех функциях — параметр `moment`, а не скрытый вызов `clock.now()`.
  Так тест может проиграть любое утро, не трогая системные часы и не ожидая реального
  срабатывания APScheduler.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

import aiosqlite
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import alarm, clock, db as db_module, users, views
from app.clock import MOSCOW
from app.config import Config
from app.routing.base import Router
from app.schedule import ScheduleError

logger = logging.getLogger(__name__)

# Идентификатор ежедневной задачи планирования. Фиксированный, чтобы повторный запуск
# заменял её, а не плодил копии.
PLANNING_JOB_ID = "alarm-planning"

# Если событийный цикл был занят, APScheduler по умолчанию просто пропускает задачу.
# Час форы: будильник, опоздавший на пару минут, всё ещё полезен, а send_alarm сам
# проверит, что выходить не поздно.
MISFIRE_GRACE_SECONDS = 3600


def alarm_job_id(telegram_id: int, day: date) -> str:
    """Имя одноразовой задачи «разбудить такого-то в такой-то день»."""
    return f"alarm:{telegram_id}:{day.isoformat()}"


class AlarmScheduler:
    """Считает будильники и отправляет их в Telegram."""

    def __init__(
        self,
        bot: Bot,
        db: aiosqlite.Connection,
        travel_router: Router,
        config: Config,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        self._bot = bot
        self._db = db
        self._router = travel_router
        self._config = config
        # Часовой пояс планировщика — Москва: cron-триггер должен срабатывать в 05:00
        # по Москве, а не по времени сервера.
        self._scheduler = scheduler or AsyncIOScheduler(timezone=MOSCOW)
        self._started = False

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """Доступ к самому APScheduler — нужен тестам и отладке."""
        return self._scheduler

    # --- Жизненный цикл ---------------------------------------------------------

    def start(self) -> None:
        """Запускает планировщик и ставит ежедневный пересчёт будильников."""
        planning = self._config.alarm_planning_time
        self._scheduler.add_job(
            self.plan_today,
            trigger="cron",
            hour=planning.hour,
            minute=planning.minute,
            id=PLANNING_JOB_ID,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        self._scheduler.start()
        self._started = True
        logger.info(
            "Планировщик будильников запущен, пересчёт каждый день в %02d:%02d по Москве",
            planning.hour,
            planning.minute,
        )

    async def shutdown(self) -> None:
        """Останавливает планировщик. Незапущенный останавливать не нужно."""
        if not self._started:
            return
        self._started = False
        self._scheduler.shutdown(wait=False)
        # AsyncIOScheduler.shutdown() только ставит остановку в очередь событийного цикла.
        # Даём циклу шаг, иначе будильник мог бы сработать уже после закрытия базы.
        await asyncio.sleep(0)
        logger.info("Планировщик будильников остановлен")

    # --- Планирование -----------------------------------------------------------

    async def plan_today(self, moment: datetime | None = None) -> list[str]:
        """Пересчитывает будильники на сегодня для всех, кто прошёл настройку.

        Возвращает список решений (по одному на пользователя) — удобно и для логов,
        и для тестов. Ошибка на одном человеке не должна мешать остальным.
        """
        at = moment or clock.now()
        day = at.astimezone(MOSCOW).date()

        try:
            everyone = await users.list_users(self._db)
        except Exception:  # noqa: BLE001 — база упала, но бот должен остаться живым
            logger.exception("Не удалось прочитать список пользователей для будильника")
            return []

        allowed = self._config.allowed_user_ids

        decisions: list[str] = []
        for user in everyone:
            if not users.is_onboarded(user):
                continue
            if allowed and user.telegram_id not in allowed:
                # Бот приватный — если список задан, будильник тоже только для своих.
                # Иначе убрать человека из ALLOWED_USER_IDS ничего бы не меняло для него.
                continue
            try:
                decisions.append(await self.plan_for_user(user, day, at))
            except Exception:  # noqa: BLE001 — один сломанный пользователь не ломает утро
                logger.exception(
                    "Не удалось запланировать будильник для %s", user.telegram_id
                )
        return decisions

    async def plan_for_user(
        self, user: users.User, day: date, moment: datetime
    ) -> str:
        """Решает, что делать с будильником этого человека на этот день.

        Возвращает одно из `alarm.DECISION_*`. `moment` обязателен и передаётся явно —
        именно он делает функцию проверяемой в тестах.
        """
        if await db_module.already_sent(self._db, user.telegram_id, day):
            logger.info(
                "Будильник для %s на %s уже отправлялся, второй раз не бужу",
                user.telegram_id,
                day,
            )
            return alarm.DECISION_SILENT

        try:
            plan = await alarm.build_alarm_plan(
                self._db,
                user,
                day,
                self._router,
                at=moment,
                fallback_travel_minutes=self._config.alarm_fallback_travel_minutes,
            )
        except ScheduleError:
            logger.exception("Расписание не читается, будильник на %s не поставлен", day)
            return alarm.DECISION_SILENT

        decision = alarm.decide(plan, moment)

        if decision == alarm.DECISION_SILENT:
            logger.info(
                "Будильник для %s на %s не нужен: %s", user.telegram_id, day, plan.reason
            )
            return decision

        if decision == alarm.DECISION_SCHEDULE and plan.wake_at is not None:
            self._scheduler.add_job(
                self.send_alarm,
                trigger="date",
                run_date=plan.wake_at,
                kwargs={"telegram_id": user.telegram_id, "day": day},
                id=alarm_job_id(user.telegram_id, day),
                replace_existing=True,
                misfire_grace_time=MISFIRE_GRACE_SECONDS,
            )
            logger.info(
                "Будильник для %s поставлен на %s (выход в %s)",
                user.telegram_id,
                plan.wake_at.isoformat(timespec="minutes"),
                plan.leave_at.isoformat(timespec="minutes") if plan.leave_at else "?",
            )
            return decision

        if decision == alarm.DECISION_SEND_NOW:
            # Бот запустился уже после расчётного подъёма, но выйти вовремя ещё можно.
            logger.info(
                "Время подъёма для %s (%s) уже прошло, бужу сразу",
                user.telegram_id,
                plan.wake_at.isoformat(timespec="minutes") if plan.wake_at else "?",
            )
            await self.send_alarm(user.telegram_id, day, moment=moment, late_start=True)
            return decision

        logger.warning(
            "Будильник для %s на %s пропущен: выходить надо было в %s, а сейчас %s. "
            "Похоже, бот был выключен всё утро.",
            user.telegram_id,
            day,
            plan.leave_at.isoformat(timespec="minutes") if plan.leave_at else "?",
            moment.isoformat(timespec="minutes"),
        )
        return decision

    # --- Отправка ---------------------------------------------------------------

    async def send_alarm(
        self,
        telegram_id: int,
        day: date,
        moment: datetime | None = None,
        late_start: bool = False,
    ) -> bool:
        """Считает маршрут заново и отправляет будильник. True, если сообщение ушло.

        Второй расчёт нужен потому, что план строился рано утром, а пробки к моменту
        подъёма уже другие. Если сервис маршрутов молчит — время в пути берётся из
        настроек, и об этом честно сказано в тексте.
        """
        at = moment or clock.now()

        if await db_module.already_sent(self._db, telegram_id, day):
            logger.info("Будильник для %s на %s уже отправлен, пропускаю", telegram_id, day)
            return False

        user = await users.get_user(self._db, telegram_id)
        if not users.is_onboarded(user) or user is None:
            logger.info("Пользователь %s не настроен, будильник не отправляю", telegram_id)
            return False

        try:
            plan = await alarm.build_alarm_plan(
                self._db,
                user,
                day,
                self._router,
                at=at,
                fallback_travel_minutes=self._config.alarm_fallback_travel_minutes,
            )
        except ScheduleError:
            logger.exception("Расписание не читается, будильник на %s не отправлен", day)
            return False

        if not plan.should_wake:
            logger.info(
                "К моменту отправки будильник для %s на %s стал не нужен: %s",
                telegram_id,
                day,
                plan.reason,
            )
            return False

        starts_at = plan.lesson_start_at
        if starts_at is not None and at >= starts_at:
            # Задача сработала с большим опозданием: пара уже началась, будить незачем.
            logger.warning(
                "Будильник для %s на %s сработал после начала пары, не отправляю",
                telegram_id,
                day,
            )
            return False

        text = views.format_alarm(plan, user, late_start=late_start)
        try:
            await self._bot.send_message(telegram_id, text)
        except Exception:  # noqa: BLE001 — Telegram мог не ответить, повторим при рестарте
            logger.exception("Не удалось отправить будильник пользователю %s", telegram_id)
            return False

        # Отметку ставим только после успешной отправки: иначе повторный запуск бота
        # решит, что человек уже разбужен, и промолчит.
        await db_module.mark_sent(self._db, telegram_id, day)
        logger.info("Будильник отправлен пользователю %s на %s", telegram_id, day)
        return True


__all__ = ["MISFIRE_GRACE_SECONDS", "PLANNING_JOB_ID", "AlarmScheduler", "alarm_job_id"]
