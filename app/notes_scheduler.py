"""Планировщик заметок: вопрос после пары и напоминания о незакрытой домашке.

Устроено так же, как будильник в `app/scheduler.py`, и намеренно живёт на **том же**
APScheduler: второй планировщик в одном процессе — это второй пул потоков и вторая
точка отказа ради двух-трёх пользователей. Объект планировщика передаётся в конструктор
(`AlarmScheduler.scheduler`), запускает и останавливает его тот, кто его создал.

Что ставится в очередь:

* **вопрос «Есть / Нет»** — через 10 минут после конца каждой пары дня, всем, кто прошёл
  знакомство (и попал в `ALLOWED_USER_IDS`, если список задан). Задачи живут в памяти
  и пересчитываются при старте бота и раз в сутки; чтобы перезапуск не спросил дважды,
  факт вопроса пишется в `note_prompt_log`. Если момент вопроса уже прошёл — вопрос не
  задаётся вообще: «а что там было на паре три часа назад» никому не помогает;
* **напоминание за сутки** до следующего такого же занятия и **утренний повтор** в день
  занятия. Оба вида отмечаются в `note_reminder_log` — это не закрытие заметки, а только
  «такое напоминание уже уходило».

«Сейчас» везде передаётся параметром `moment`, а не берётся из `clock.now()`: так тест
может проиграть любой день, не трогая часы и не дожидаясь срабатывания APScheduler.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import aiosqlite
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import clock, db as db_module, keyboards, notes, users, views
from app.clock import MOSCOW
from app.config import Config
from app.schedule import Lesson, ScheduleError, lesson_by_id, lessons_on

logger = logging.getLogger(__name__)

# Ежедневный пересчёт. Id фиксированный, чтобы повторный старт заменял задачу, а не плодил.
PLANNING_JOB_ID = "notes-planning"

# Опоздавшее напоминание всё ещё полезно (человек прочтёт его чуть позже), поэтому час форы.
MISFIRE_GRACE_SECONDS = 3600

# А вот опоздавший вопрос про домашку — нет: спрашивать «что задали?» через два часа
# после пары бессмысленно. Полчаса форы на занятый событийный цикл, дальше молчим.
PROMPT_MISFIRE_GRACE_SECONDS = 1800


def prompt_job_id(lesson_id: str, day: date) -> str:
    """Имя задачи «спросить про домашку по этой паре этого числа»."""
    return f"note-prompt:{lesson_id}:{day.isoformat()}"


def reminder_job_id(note_id: int, kind: str) -> str:
    """Имя задачи «напомнить про эту заметку так-то»."""
    return f"note-remind:{kind}:{note_id}"


class NotesScheduler:
    """Ставит вопросы про домашку и напоминания по заметкам."""

    def __init__(
        self,
        bot: Bot,
        db: aiosqlite.Connection,
        config: Config,
        scheduler: AsyncIOScheduler,
    ) -> None:
        self._bot = bot
        self._db = db
        self._config = config
        # Планировщик общий с будильником и приходит снаружи уже настроенным на Москву.
        self._scheduler = scheduler

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """Доступ к самому APScheduler — нужен тестам и отладке."""
        return self._scheduler

    # --- Жизненный цикл ---------------------------------------------------------

    def start(self) -> None:
        """Ставит ежедневный пересчёт.

        Сам APScheduler здесь не запускается и не останавливается: он общий с будильником,
        и владеет им `AlarmScheduler`. Пересчёт идёт в то же время, что и планирование
        будильников (`ALARM_PLANNING_TIME`) — заведомо раньше и первой пары, и утреннего
        напоминания.
        """
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
        logger.info(
            "Планировщик заметок запущен, пересчёт каждый день в %02d:%02d по Москве",
            planning.hour,
            planning.minute,
        )

    # --- Планирование -----------------------------------------------------------

    async def plan_today(self, moment: datetime | None = None) -> list[str]:
        """Пересчитывает вопросы на сегодня и напоминания по открытым заметкам.

        Возвращает id поставленных задач — по ним удобно и в логи посмотреть, и тесту
        проверить, что запланировано именно то, что нужно.
        """
        at = moment or clock.now()
        day = at.astimezone(MOSCOW).date()

        return [*await self.plan_prompts(day, at), *await self.plan_reminders(at)]

    async def plan_prompts(self, day: date, moment: datetime) -> list[str]:
        """Ставит вопрос «есть что записать?» после каждой пары этого дня."""
        try:
            day_lessons = lessons_on(day)
        except ScheduleError:
            logger.exception("Расписание не читается, вопросы про домашку на %s не ставлю", day)
            return []

        scheduled: list[str] = []
        for lesson in day_lessons:
            try:
                job_id = await self._plan_prompt(lesson, day, moment)
            except Exception:  # noqa: BLE001 — одна пара не должна ломать весь день
                logger.exception(
                    "Не удалось запланировать вопрос про домашку по паре %s на %s",
                    lesson.id,
                    day,
                )
                continue
            if job_id is not None:
                scheduled.append(job_id)
        return scheduled

    async def _plan_prompt(
        self, lesson: Lesson, day: date, moment: datetime
    ) -> str | None:
        """Одна пара: ставим задачу, спрашиваем сразу или молчим. None — задачи нет."""
        at = notes.prompt_at(lesson, day)
        late = moment - at

        if late > timedelta(seconds=PROMPT_MISFIRE_GRACE_SECONDS):
            # Опоздание больше той же форы, что и у уже поставленной задачи (см. ниже) —
            # момент вопроса прошёл давно, пока бот был выключен. Досылать не будем.
            logger.debug(
                "Вопрос про домашку по %s (%s) уже неактуален: момент был в %s",
                lesson.id,
                day,
                at.isoformat(timespec="minutes"),
            )
            return None

        pending = await self._pending_recipients(lesson.id, day)
        if not pending:
            logger.debug("Про пару %s (%s) уже спрошено у всех", lesson.id, day)
            return None

        if late > timedelta(0):
            # Момент уже наступил, но в пределах форы — как и опоздавшая задача, спрашиваем
            # прямо сейчас, а не молчим только из-за того, что бот перезапустился чуть позже.
            logger.info(
                "Опоздал с вопросом про домашку по %s (%s) на %s — спрашиваю сейчас",
                lesson.id,
                day,
                late,
            )
            await self.send_prompt(lesson.id, day, moment)
            return None

        job_id = prompt_job_id(lesson.id, day)
        self._scheduler.add_job(
            self.send_prompt,
            trigger="date",
            run_date=at,
            kwargs={"lesson_id": lesson.id, "day": day},
            id=job_id,
            replace_existing=True,
            misfire_grace_time=PROMPT_MISFIRE_GRACE_SECONDS,
        )
        logger.info(
            "Вопрос про домашку по паре %s (%s) поставлен на %s, ждут %d человек",
            lesson.id,
            day,
            at.isoformat(timespec="minutes"),
            len(pending),
        )
        return job_id

    async def plan_reminders(self, moment: datetime) -> list[str]:
        """Ставит напоминания по всем незакрытым заметкам, у которых есть срок."""
        try:
            open_notes = await db_module.list_open_notes(self._db, with_due_date=True)
        except Exception:  # noqa: BLE001 — база подвела, но бот должен жить дальше
            logger.exception("Не удалось прочитать заметки для планирования напоминаний")
            return []

        allowed = self._config.allowed_user_ids

        scheduled: list[str] = []
        for note in open_notes:
            if allowed and note.telegram_id not in allowed:
                continue
            try:
                scheduled.extend(self._plan_note_reminders_unsafe(note, moment))
            except Exception:  # noqa: BLE001 — одна заметка не ломает остальные
                logger.exception("Не удалось запланировать напоминания по заметке %s", note.id)
        return scheduled

    def _plan_note_reminders_unsafe(
        self, note: db_module.Note, moment: datetime
    ) -> list[str]:
        """Оба напоминания по одной заметке. Ошибки ловит вызывающий."""
        if note.due_date is None:
            return []

        lesson = lesson_by_id(note.lesson_id)
        if lesson is None:
            logger.warning(
                "Заметка %s ссылается на пару %s, которой нет в расписании — напоминаний не будет",
                note.id,
                note.lesson_id,
            )
            return []

        moments: tuple[tuple[str, datetime | None], ...] = (
            (
                db_module.REMINDER_DAY_BEFORE,
                notes.day_before_reminder_at(note.due_date, lesson),
            ),
            (
                db_module.REMINDER_MORNING,
                notes.morning_reminder_at(
                    note.due_date, lesson, self._config.notes_morning_time
                ),
            ),
        )

        scheduled: list[str] = []
        for kind, at in moments:
            # `at is None` — утренний повтор не нужен: до пары меньше часа.
            # `at <= moment` — момент прошёл, пока бот был выключен; догонять не будем,
            # иначе человек получил бы «завтра пара» в день самой пары.
            if at is None or at <= moment:
                continue
            job_id = reminder_job_id(note.id, kind)
            self._scheduler.add_job(
                self.send_reminder,
                trigger="date",
                run_date=at,
                kwargs={"note_id": note.id, "kind": kind},
                id=job_id,
                replace_existing=True,
                misfire_grace_time=MISFIRE_GRACE_SECONDS,
            )
            logger.info(
                "Напоминание «%s» по заметке %s поставлено на %s",
                kind,
                note.id,
                at.isoformat(timespec="minutes"),
            )
            scheduled.append(job_id)
        return scheduled

    # --- Отправка ---------------------------------------------------------------

    async def send_prompt(
        self, lesson_id: str, day: date, moment: datetime | None = None
    ) -> int:
        """Спрашивает про домашку по паре. Возвращает, скольким людям вопрос ушёл."""
        at = moment or clock.now()

        try:
            lesson = lesson_by_id(lesson_id)
        except ScheduleError:
            logger.exception("Расписание не читается, вопрос по паре %s не задаю", lesson_id)
            return 0
        if lesson is None:
            logger.warning("Пары %s нет в расписании, вопрос про домашку не задаю", lesson_id)
            return 0

        # Задача могла сработать сильно позже срока (бот был занят). Спрашивать
        # про давно закончившуюся пару не надо — это уже не помощь, а шум.
        late = at - notes.prompt_at(lesson, day)
        if late > timedelta(seconds=PROMPT_MISFIRE_GRACE_SECONDS):
            logger.warning(
                "Вопрос про домашку по %s (%s) опоздал на %s, пропускаю", lesson_id, day, late
            )
            return 0

        text = views.format_note_prompt(lesson)
        markup = keyboards.note_prompt(lesson_id, day)

        asked = 0
        for user in await self._recipients():
            try:
                if await db_module.already_prompted(self._db, user.telegram_id, lesson_id, day):
                    continue
                await self._bot.send_message(user.telegram_id, text, reply_markup=markup)
                # Отметку ставим только после успешной отправки: иначе перезапуск решит,
                # что человека уже спросили, и заметка потеряется.
                await db_module.mark_prompted(self._db, user.telegram_id, lesson_id, day)
                asked += 1
            except Exception:  # noqa: BLE001 — сбой на одном человеке не трогает остальных
                logger.exception(
                    "Не удалось спросить про домашку пользователя %s (пара %s)",
                    user.telegram_id,
                    lesson_id,
                )
        return asked

    async def send_reminder(
        self, note_id: int, kind: str, moment: datetime | None = None
    ) -> bool:
        """Отправляет напоминание по заметке. True, если сообщение ушло."""
        at = moment or clock.now()

        note = await db_module.get_note(self._db, note_id)
        if note is None:
            logger.info("Заметка %s исчезла, напоминание «%s» отменяю", note_id, kind)
            return False
        if not note.is_open:
            logger.info("Заметка %s уже закрыта, напоминание «%s» не нужно", note_id, kind)
            return False
        if await db_module.already_reminded(self._db, note_id, kind):
            logger.info("Напоминание «%s» по заметке %s уже уходило", kind, note_id)
            return False

        allowed = self._config.allowed_user_ids
        if allowed and note.telegram_id not in allowed:
            return False

        try:
            lesson = lesson_by_id(note.lesson_id)
        except ScheduleError:
            logger.exception("Расписание не читается, напоминание по заметке %s не шлю", note_id)
            return False
        if lesson is None:
            logger.warning(
                "Заметка %s ссылается на пару %s, которой нет в расписании", note_id, note.lesson_id
            )
            return False

        if note.due_date is not None and at >= notes.lesson_starts_at(lesson, note.due_date):
            # Задача сработала с большим опозданием: пара уже началась, напоминать поздно.
            logger.warning(
                "Напоминание «%s» по заметке %s сработало после начала пары, пропускаю",
                kind,
                note_id,
            )
            return False

        morning = kind == db_module.REMINDER_MORNING
        text = views.format_note_reminder(note, lesson, morning=morning)
        # «Не сделал» уместно только накануне: после него бот повторит утром. В утреннем
        # напоминании такой кнопки нет — повторять уже нечем.
        markup = keyboards.note_done(note.id, with_not_done=not morning)

        try:
            await self._bot.send_message(note.telegram_id, text, reply_markup=markup)
        except Exception:  # noqa: BLE001 — Telegram мог не ответить, повторим при рестарте
            logger.exception(
                "Не удалось отправить напоминание «%s» по заметке %s", kind, note_id
            )
            return False

        await db_module.mark_reminded(self._db, note_id, kind)
        logger.info("Напоминание «%s» по заметке %s отправлено", kind, note_id)
        return True

    # --- Кому писать ------------------------------------------------------------

    async def _recipients(self) -> list[users.User]:
        """Все, кто прошёл знакомство и кому вообще разрешено пользоваться ботом."""
        try:
            everyone = await users.list_users(self._db)
        except Exception:  # noqa: BLE001 — база упала, но бот должен остаться живым
            logger.exception("Не удалось прочитать список пользователей для заметок")
            return []

        allowed = self._config.allowed_user_ids
        return [
            user
            for user in everyone
            if users.is_onboarded(user)
            # Бот приватный: если список задан, вопросы про домашку тоже только своим.
            and (not allowed or user.telegram_id in allowed)
        ]

    async def _pending_recipients(
        self, lesson_id: str, day: date
    ) -> list[users.User]:
        """Те, кого про эту пару этого числа ещё не спрашивали."""
        pending: list[users.User] = []
        for user in await self._recipients():
            try:
                asked = await db_module.already_prompted(
                    self._db, user.telegram_id, lesson_id, day
                )
            except Exception:  # noqa: BLE001 — сбой на одном человеке не ломает остальных
                logger.exception(
                    "Не удалось проверить журнал вопросов для %s", user.telegram_id
                )
                continue
            if not asked:
                pending.append(user)
        return pending


__all__ = [
    "MISFIRE_GRACE_SECONDS",
    "PLANNING_JOB_ID",
    "PROMPT_MISFIRE_GRACE_SECONDS",
    "NotesScheduler",
    "prompt_job_id",
    "reminder_job_id",
]
