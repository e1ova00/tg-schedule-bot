"""Тесты планировщика заметок: app/notes_scheduler.py.

Устроено как `tests/test_scheduler.py` и по тем же причинам:

* APScheduler подменён `FakeScheduler` — он только запоминает поставленные задачи;
* бот подменён `FakeBot` — складывает отправленные сообщения и умеет «сломаться»
  выборочно, у одного пользователя из двух;
* «сейчас» всегда приходит параметром `moment`, системные часы не трогаем;
* база настоящая, но временная (в памяти), поэтому журналы `note_prompt_log`
  и `note_reminder_log` проверяются как есть.

Самое дорогое для живого человека здесь — три вещи: спросить про домашку дважды
(после каждого перезапуска бота), спросить про пару, которая кончилась три часа назад,
и молча потерять напоминание из-за одной неудачной отправки в Telegram.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import aiosqlite
import pytest

from app import clock, db as db_module, notes, texts, users, views
from app.clock import MOSCOW
from app.config import Config, build_config
from app.notes_scheduler import (
    PLANNING_JOB_ID,
    PROMPT_MISFIRE_GRACE_SECONDS,
    NotesScheduler,
    prompt_job_id,
    reminder_job_id,
)
from app.schedule import clear_cache, lesson_by_id
from app import scheduler as alarm_scheduler_module
from app.scheduler import AlarmScheduler
from fakes import USER_ID, make_onboarded_user

OTHER_USER_ID = 777

MON_ODD = date(2026, 8, 31)  # весь день дистанционный, но домашку задать могут
TUE_ODD = date(2026, 9, 1)  # L1216 11:40–13:05, L1218, L1220
WED_ODD = date(2026, 9, 2)  # L1221 10:05–11:30, L1222 11:40–13:05, L1223 13:45–15:10
THU_ODD = date(2026, 9, 3)  # пар нет
WED_EVEN = date(2026, 9, 9)
TUE_ODD_NEXT = date(2026, 9, 15)  # следующая L1216


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
    def prompt_jobs(self) -> list[FakeJob]:
        return [item for item in self.jobs if str(item.id).startswith("note-prompt:")]

    @property
    def reminder_jobs(self) -> list[FakeJob]:
        return [item for item in self.jobs if str(item.id).startswith("note-remind:")]


class FakeBot:
    """Замена aiogram.Bot: собирает сообщения и умеет падать на конкретных людях."""

    def __init__(
        self,
        error: BaseException | None = None,
        fail_for: set[int] | None = None,
    ) -> None:
        self.error = error
        self.fail_for = fail_for or set()
        self.sent: list[tuple[int, str]] = []
        self.markups: list[Any] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        if self.error is not None and not self.fail_for:
            raise self.error
        if chat_id in self.fail_for:
            raise self.error or RuntimeError("Telegram недоступен")
        self.sent.append((chat_id, text))
        self.markups.append(kwargs.get("reply_markup"))

    @property
    def recipients(self) -> list[int]:
        return [chat_id for chat_id, _ in self.sent]

    @property
    def last_text(self) -> str:
        return self.sent[-1][1]

    @property
    def last_markup(self) -> Any:
        return self.markups[-1]


def make_config(**env: str) -> Config:
    return build_config({"BOT_TOKEN": "123456:AAHfake-token", **env})


def make_notes_scheduler(
    db: aiosqlite.Connection,
    *,
    bot: FakeBot | None = None,
    config: Config | None = None,
    fake_scheduler: FakeScheduler | None = None,
) -> tuple[NotesScheduler, FakeBot, FakeScheduler]:
    """Собирает NotesScheduler на заглушках и отдаёт все части для проверок."""
    bot = bot or FakeBot()
    fake_scheduler = fake_scheduler or FakeScheduler()
    note_jobs = NotesScheduler(
        bot,  # type: ignore[arg-type]
        db,
        config or make_config(),
        fake_scheduler,  # type: ignore[arg-type]
    )
    return note_jobs, bot, fake_scheduler


async def make_note(
    db: aiosqlite.Connection,
    *,
    telegram_id: int = USER_ID,
    lesson_id: str = "L1216",
    lesson_date: date = TUE_ODD,
    due_date: date | None = TUE_ODD_NEXT,
    text: str = "дочитать главу 3",
) -> db_module.Note:
    return await db_module.create_note(
        db, telegram_id, lesson_id, lesson_date, text, due_date
    )


# ======================================================================================
# 1. Жизненный цикл: общий планировщик с будильником
# ======================================================================================


async def test_start_registers_daily_planning_job(db: aiosqlite.Connection) -> None:
    note_jobs, _, fake = make_notes_scheduler(
        db, config=make_config(ALARM_PLANNING_TIME="05:30")
    )

    note_jobs.start()

    job = fake.job(PLANNING_JOB_ID)
    assert job is not None
    assert job.trigger == "cron"
    assert (job.hour, job.minute) == (5, 30)
    assert job.func == note_jobs.plan_today
    assert job.extra.get("replace_existing") is True, "Иначе перезапуск удвоит пересчёт"


async def test_start_does_not_touch_the_shared_scheduler(
    db: aiosqlite.Connection,
) -> None:
    """Планировщик общий с будильником: запускает и останавливает его AlarmScheduler."""
    note_jobs, _, fake = make_notes_scheduler(db)

    note_jobs.start()

    assert fake.running is False, "Второй start() у общего планировщика не нужен"
    assert fake.shutdown_calls == []


async def test_scheduler_is_a_required_argument(db: aiosqlite.Connection) -> None:
    """Свой AsyncIOScheduler ради двух-трёх человек — вторая точка отказа."""
    with pytest.raises(TypeError):
        NotesScheduler(FakeBot(), db, make_config())  # type: ignore[call-arg]


async def test_notes_and_alarms_share_one_scheduler(db: aiosqlite.Connection) -> None:
    """В бою оба планировщика живут на одном APScheduler и не мешают друг другу."""
    fake = FakeScheduler()
    alarms = AlarmScheduler(
        FakeBot(),  # type: ignore[arg-type]
        db,
        None,  # type: ignore[arg-type]
        make_config(),
        scheduler=fake,  # type: ignore[arg-type]
    )
    note_jobs = NotesScheduler(FakeBot(), db, make_config(), alarms.scheduler)  # type: ignore[arg-type]

    assert note_jobs.scheduler is alarms.scheduler is fake


def test_job_ids_do_not_collide() -> None:
    """Id задач должны быть разными у разных пар, дат, заметок и видов напоминания."""
    assert prompt_job_id("L1216", TUE_ODD) == "note-prompt:L1216:2026-09-01"
    assert prompt_job_id("L1216", TUE_ODD) != prompt_job_id("L1216", WED_ODD)
    assert prompt_job_id("L1216", TUE_ODD) != prompt_job_id("L1218", TUE_ODD)
    assert reminder_job_id(7, db_module.REMINDER_DAY_BEFORE) == "note-remind:day_before:7"
    assert reminder_job_id(7, db_module.REMINDER_MORNING) == "note-remind:morning:7"
    assert not reminder_job_id(7, "morning").startswith("alarm:")


def test_two_planning_jobs_do_not_replace_each_other() -> None:
    """Планировщик один на двоих, поэтому одинаковые id молча убили бы один из пересчётов."""
    assert PLANNING_JOB_ID != alarm_scheduler_module.PLANNING_JOB_ID


async def test_daily_planning_jobs_of_both_schedulers_coexist(
    db: aiosqlite.Connection,
) -> None:
    """Проверка того же самого не на константах, а на настоящем общем планировщике."""
    fake = FakeScheduler()
    alarms = AlarmScheduler(
        FakeBot(),  # type: ignore[arg-type]
        db,
        None,  # type: ignore[arg-type]
        make_config(),
        scheduler=fake,  # type: ignore[arg-type]
    )
    note_jobs = NotesScheduler(FakeBot(), db, make_config(), fake)  # type: ignore[arg-type]

    alarms.start()
    note_jobs.start()

    assert fake.job(alarm_scheduler_module.PLANNING_JOB_ID) is not None
    assert fake.job(PLANNING_JOB_ID) is not None
    assert len([job for job in fake.jobs if job.trigger == "cron"]) == 2


# ======================================================================================
# 2. Вопрос «Есть / Нет» после пары
# ======================================================================================


async def test_prompts_are_planned_ten_minutes_after_every_lesson(
    db: aiosqlite.Connection,
) -> None:
    """Среда по числителю: три пары — три вопроса, каждый через 10 минут после звонка."""
    await make_onboarded_user(db)
    note_jobs, bot, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))

    assert planned == [
        prompt_job_id("L1221", WED_ODD),
        prompt_job_id("L1222", WED_ODD),
        prompt_job_id("L1223", WED_ODD),
    ]
    run_dates = {job.id: job.run_date for job in fake.prompt_jobs}
    assert run_dates[prompt_job_id("L1221", WED_ODD)] == msk(WED_ODD, 11, 40)
    assert run_dates[prompt_job_id("L1222", WED_ODD)] == msk(WED_ODD, 13, 15)
    assert run_dates[prompt_job_id("L1223", WED_ODD)] == msk(WED_ODD, 15, 20)
    assert bot.sent == [], "На этапе планирования писать человеку рано"


async def test_prompt_job_carries_lesson_and_date(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))

    job = fake.job(prompt_job_id("L1221", WED_ODD))
    assert job is not None
    assert job.trigger == "date"
    assert job.kwargs == {"lesson_id": "L1221", "day": WED_ODD}
    assert job.func == note_jobs.send_prompt
    assert job.extra.get("misfire_grace_time") == PROMPT_MISFIRE_GRACE_SECONDS


async def test_prompts_are_planned_for_remote_monday_too(
    db: aiosqlite.Connection,
) -> None:
    """Будильника в понедельник нет, а вопрос про домашку есть: задать могут и на дистанте."""
    await make_onboarded_user(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(MON_ODD, msk(MON_ODD, 5, 0))

    assert planned == [
        prompt_job_id("L1211", MON_ODD),
        prompt_job_id("L1213", MON_ODD),
        prompt_job_id("L1215", MON_ODD),
    ]


async def test_no_lessons_no_prompts(db: aiosqlite.Connection) -> None:
    """Четверг по числителю пустой — спрашивать не о чем."""
    await make_onboarded_user(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_prompts(THU_ODD, msk(THU_ODD, 5, 0)) == []
    assert fake.prompt_jobs == []


async def test_past_lessons_get_no_prompt_job(db: aiosqlite.Connection) -> None:
    """Бот включился в 14:00: про пару, которая кончилась в 11:30, уже не спрашиваем."""
    await make_onboarded_user(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 14, 0))

    assert planned == [prompt_job_id("L1223", WED_ODD)]
    assert [job.id for job in fake.prompt_jobs] == [prompt_job_id("L1223", WED_ODD)]


async def test_replanning_does_not_duplicate_prompt_jobs(
    db: aiosqlite.Connection,
) -> None:
    """Перезапуск бота обязан заменить задачу, а не поставить вторую такую же."""
    await make_onboarded_user(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))
    await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 10))

    assert len(fake.prompt_jobs) == 3


async def test_already_asked_lesson_is_not_planned_again(
    db: aiosqlite.Connection,
) -> None:
    """Полный сценарий перезапуска: вопрос уже задан, в журнале отметка — второго не будет."""
    await make_onboarded_user(db)
    await db_module.mark_prompted(db, USER_ID, "L1221", WED_ODD)
    note_jobs, _, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))

    assert prompt_job_id("L1221", WED_ODD) not in planned
    assert planned == [
        prompt_job_id("L1222", WED_ODD),
        prompt_job_id("L1223", WED_ODD),
    ]


async def test_prompt_is_still_planned_while_someone_has_not_been_asked(
    db: aiosqlite.Connection,
) -> None:
    """Одного спросили, второго нет — задача нужна, но повторно первого она не тронет."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    await db_module.mark_prompted(db, USER_ID, "L1221", WED_ODD)
    note_jobs, bot, _ = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))
    await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    assert prompt_job_id("L1221", WED_ODD) in planned
    assert bot.recipients == [OTHER_USER_ID]


async def test_users_without_onboarding_get_no_prompts(
    db: aiosqlite.Connection,
) -> None:
    await users.ensure_user(db, OTHER_USER_ID, "новичок")
    note_jobs, bot, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0)) == []
    assert await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40)) == 0
    assert bot.sent == []


async def test_late_start_within_grace_asks_right_away(db: aiosqlite.Connection) -> None:
    """Бот перезапустился на 5 минут позже момента вопроса — та же фора, что и у уже
    поставленной задачи (misfire_grace_time), а не полное молчание.

    L1222 на WED_ODD кончается в 13:05, вопрос должен был уйти в 13:15; бот стартовал
    в 13:20 — с опозданием в пределах получаса вопрос уходит сразу, а не откладывается
    ещё на сутки и не пропадает молча.
    """
    await make_onboarded_user(db)
    note_jobs, bot, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 13, 20))

    assert prompt_job_id("L1222", WED_ODD) not in planned, "Не отложенная задача — уже отправлено"
    assert bot.recipients == [USER_ID]
    assert fake.job(prompt_job_id("L1222", WED_ODD)) is None
    assert await db_module.already_prompted(db, USER_ID, "L1222", WED_ODD) is True


async def test_prompts_respect_allowed_user_ids(db: aiosqlite.Connection) -> None:
    """Бот приватный: вопрос про домашку тоже только своим."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    note_jobs, bot, _ = make_notes_scheduler(
        db, config=make_config(ALLOWED_USER_IDS=str(USER_ID))
    )

    asked = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    assert asked == 1
    assert bot.recipients == [USER_ID]


# ======================================================================================
# 3. send_prompt: текст, повторы, опоздание
# ======================================================================================


async def test_send_prompt_asks_everyone_and_marks_the_log(
    db: aiosqlite.Connection,
) -> None:
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    note_jobs, bot, _ = make_notes_scheduler(db)

    asked = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    assert asked == 2
    assert sorted(bot.recipients) == sorted([USER_ID, OTHER_USER_ID])
    assert "Прикладной дизайн" in bot.last_text
    assert "10:05–11:30" in bot.last_text
    buttons = [
        button.text for row in bot.last_markup.inline_keyboard for button in row
    ]
    assert buttons == [texts.BTN_NOTE_HAS, texts.BTN_NOTE_NONE]
    assert await db_module.already_prompted(db, USER_ID, "L1221", WED_ODD) is True
    assert await db_module.already_prompted(db, OTHER_USER_ID, "L1221", WED_ODD) is True


async def test_send_prompt_twice_asks_only_once(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    first = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))
    second = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 45))

    assert (first, second) == (1, 0)
    assert len(bot.sent) == 1


async def test_restart_does_not_ask_about_the_same_lesson_again(
    db: aiosqlite.Connection,
) -> None:
    """Вопрос ушёл, бота перезапустили — второго такого же вопроса быть не должно."""
    await make_onboarded_user(db)
    first_run, first_bot, _ = make_notes_scheduler(db)
    await first_run.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))
    assert len(first_bot.sent) == 1

    # Новый процесс: свой планировщик, свой бот, та же база.
    second_run, second_bot, fake = make_notes_scheduler(db)
    planned = await second_run.plan_prompts(WED_ODD, msk(WED_ODD, 11, 45))

    assert prompt_job_id("L1221", WED_ODD) not in planned
    assert second_bot.sent == []


async def test_late_prompt_is_not_sent_at_all(db: aiosqlite.Connection) -> None:
    """Задача сработала через 31 минуту после срока — «что задали?» уже только шум."""
    await make_onboarded_user(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    asked = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 12, 11))

    assert asked == 0
    assert bot.sent == []
    assert await db_module.already_prompted(db, USER_ID, "L1221", WED_ODD) is False


async def test_prompt_exactly_at_the_grace_boundary_still_goes(
    db: aiosqlite.Connection,
) -> None:
    """Граница: ровно 30 минут опоздания — вопрос ещё уместен."""
    await make_onboarded_user(db)
    note_jobs, bot, _ = make_notes_scheduler(db)
    late = notes.prompt_at(lesson_by_id("L1221"), WED_ODD) + timedelta(  # type: ignore[arg-type]
        seconds=PROMPT_MISFIRE_GRACE_SECONDS
    )

    assert await note_jobs.send_prompt("L1221", WED_ODD, moment=late) == 1
    assert len(bot.sent) == 1


def test_prompt_grace_is_half_an_hour() -> None:
    assert PROMPT_MISFIRE_GRACE_SECONDS == 30 * 60


async def test_failed_delivery_does_not_burn_the_question(
    db: aiosqlite.Connection,
) -> None:
    """Telegram не ответил одному — второй всё равно спрошен, а первого спросим позже."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    broken = FakeBot(error=RuntimeError("Telegram недоступен"), fail_for={USER_ID})
    note_jobs, _, _ = make_notes_scheduler(db, bot=broken)

    asked = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    assert asked == 1
    assert broken.recipients == [OTHER_USER_ID]
    assert await db_module.already_prompted(db, USER_ID, "L1221", WED_ODD) is False
    assert await db_module.already_prompted(db, OTHER_USER_ID, "L1221", WED_ODD) is True


async def test_question_can_be_retried_after_failed_delivery(
    db: aiosqlite.Connection,
) -> None:
    """Продолжение предыдущего: повтор доходит до того, кому не дошло, и только до него."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    broken = FakeBot(error=RuntimeError("Telegram недоступен"), fail_for={USER_ID})
    failing, _, _ = make_notes_scheduler(db, bot=broken)
    await failing.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    retry, working_bot, _ = make_notes_scheduler(db)
    asked = await retry.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 50))

    assert asked == 1
    assert working_bot.recipients == [USER_ID]
    assert await db_module.already_prompted(db, USER_ID, "L1221", WED_ODD) is True


async def test_send_prompt_ignores_unknown_lesson(db: aiosqlite.Connection) -> None:
    """Заметка от старой версии расписания не должна ронять задачу."""
    await make_onboarded_user(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    assert await note_jobs.send_prompt("L9999", WED_ODD, moment=msk(WED_ODD, 11, 40)) == 0
    assert bot.sent == []


async def test_send_prompt_uses_clock_now_by_default(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В бою задача вызывается без moment — «сейчас» берётся из app.clock.now()."""
    await make_onboarded_user(db)
    monkeypatch.setattr(clock, "now", lambda *args, **kwargs: msk(WED_ODD, 11, 40))
    note_jobs, bot, _ = make_notes_scheduler(db)

    assert await note_jobs.send_prompt("L1221", WED_ODD) == 1
    assert len(bot.sent) == 1


# ======================================================================================
# 4. Планирование напоминаний по открытым заметкам
# ======================================================================================


async def test_open_note_gets_both_reminders(db: aiosqlite.Connection) -> None:
    """Следующая L1216 — 15 сентября в 11:40: напоминания 14-го в 11:40 и 15-го в 08:00."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_reminders(msk(WED_ODD, 5, 0))

    assert planned == [
        reminder_job_id(note.id, db_module.REMINDER_DAY_BEFORE),
        reminder_job_id(note.id, db_module.REMINDER_MORNING),
    ]
    run_dates = {job.id: job.run_date for job in fake.reminder_jobs}
    assert run_dates[planned[0]] == msk(date(2026, 9, 14), 11, 40)
    assert run_dates[planned[1]] == msk(TUE_ODD_NEXT, 8, 0)


async def test_reminder_job_carries_note_and_kind(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    await note_jobs.plan_reminders(msk(WED_ODD, 5, 0))

    job = fake.job(reminder_job_id(note.id, db_module.REMINDER_MORNING))
    assert job is not None
    assert job.trigger == "date"
    assert job.kwargs == {"note_id": note.id, "kind": db_module.REMINDER_MORNING}
    assert job.func == note_jobs.send_reminder


async def test_closed_note_gets_no_reminders(db: aiosqlite.Connection) -> None:
    """«Сделал» нажали — напоминаний по этой заметке больше не ставим."""
    await make_onboarded_user(db)
    note = await make_note(db)
    await db_module.close_note(db, note.id, USER_ID)
    note_jobs, _, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_reminders(msk(WED_ODD, 5, 0)) == []
    assert fake.reminder_jobs == []


async def test_note_without_due_date_gets_no_reminders(
    db: aiosqlite.Connection,
) -> None:
    """Срока нет — напоминать не о чем, но заметка остаётся в списке."""
    await make_onboarded_user(db)
    await make_note(db, due_date=None)
    note_jobs, _, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_reminders(msk(WED_ODD, 5, 0)) == []
    assert fake.reminder_jobs == []


async def test_note_about_unknown_lesson_gets_no_reminders(
    db: aiosqlite.Connection,
) -> None:
    """Заметка от старой версии расписания: без пары считать напоминание не от чего."""
    await make_onboarded_user(db)
    await make_note(db, lesson_id="L9999")
    note_jobs, _, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_reminders(msk(WED_ODD, 5, 0)) == []
    assert fake.reminder_jobs == []


async def test_late_start_skips_the_day_before_reminder(
    db: aiosqlite.Connection,
) -> None:
    """Бот включился вечером 14-го: «завтра пара» уже прошло, задним числом не шлём."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_reminders(msk(date(2026, 9, 14), 20, 0))

    assert planned == [reminder_job_id(note.id, db_module.REMINDER_MORNING)]
    assert bot.sent == [], "Догонять пропущенное напоминание нельзя"


async def test_late_start_skips_both_reminders(db: aiosqlite.Connection) -> None:
    """Бот включился в день пары в 09:00: оба напоминания уже мимо, заметка просто живёт."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_reminders(msk(TUE_ODD_NEXT, 9, 0)) == []
    assert fake.reminder_jobs == []
    assert bot.sent == []
    still_open = await db_module.get_note(db, note.id)
    assert still_open is not None and still_open.is_open is True


async def test_morning_reminder_is_skipped_when_the_lesson_is_too_early(
    db: aiosqlite.Connection,
) -> None:
    """NOTES_MORNING_TIME=09:30 и пара в 10:05 — утренний повтор был бы просто давлением."""
    await make_onboarded_user(db)
    note = await make_note(db, lesson_id="L1221", lesson_date=WED_ODD, due_date=WED_EVEN)
    note_jobs, _, _ = make_notes_scheduler(
        db, config=make_config(NOTES_MORNING_TIME="09:30")
    )

    planned = await note_jobs.plan_reminders(msk(WED_ODD, 20, 0))

    assert planned == [reminder_job_id(note.id, db_module.REMINDER_DAY_BEFORE)]


async def test_reminders_respect_allowed_user_ids(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    mine = await make_note(db, telegram_id=USER_ID)
    await make_note(db, telegram_id=OTHER_USER_ID)
    note_jobs, _, _ = make_notes_scheduler(
        db, config=make_config(ALLOWED_USER_IDS=str(USER_ID))
    )

    planned = await note_jobs.plan_reminders(msk(WED_ODD, 5, 0))

    assert planned == [
        reminder_job_id(mine.id, db_module.REMINDER_DAY_BEFORE),
        reminder_job_id(mine.id, db_module.REMINDER_MORNING),
    ]


async def test_replanning_does_not_duplicate_reminders(
    db: aiosqlite.Connection,
) -> None:
    await make_onboarded_user(db)
    await make_note(db)
    note_jobs, _, fake = make_notes_scheduler(db)

    await note_jobs.plan_reminders(msk(WED_ODD, 5, 0))
    await note_jobs.plan_reminders(msk(WED_ODD, 5, 10))

    assert len(fake.reminder_jobs) == 2


async def test_plan_reminders_survives_broken_database(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> list[db_module.Note]:
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(db_module, "list_open_notes", boom)
    note_jobs, bot, fake = make_notes_scheduler(db)

    assert await note_jobs.plan_reminders(msk(WED_ODD, 5, 0)) == []
    assert fake.reminder_jobs == []
    assert bot.sent == []


# ======================================================================================
# 5. plan_today: вопросы и напоминания вместе
# ======================================================================================


async def test_plan_today_covers_prompts_and_reminders(
    db: aiosqlite.Connection,
) -> None:
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, _, _ = make_notes_scheduler(db)

    planned = await note_jobs.plan_today(msk(WED_ODD, 5, 0))

    assert planned == [
        prompt_job_id("L1221", WED_ODD),
        prompt_job_id("L1222", WED_ODD),
        prompt_job_id("L1223", WED_ODD),
        reminder_job_id(note.id, db_module.REMINDER_DAY_BEFORE),
        reminder_job_id(note.id, db_module.REMINDER_MORNING),
    ]


async def test_plan_today_uses_clock_now_when_moment_is_omitted(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_onboarded_user(db)
    monkeypatch.setattr(clock, "now", lambda *args, **kwargs: msk(WED_ODD, 5, 0))
    note_jobs, _, fake = make_notes_scheduler(db)

    await note_jobs.plan_today()

    assert [job.kwargs["day"] for job in fake.prompt_jobs] == [WED_ODD] * 3


# ======================================================================================
# 6. send_reminder: текст, кнопки, повторы, отказ Telegram
# ======================================================================================


async def test_day_before_reminder_text_and_buttons(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db)
    note = await make_note(db, text="дочитать главу 3")
    note_jobs, bot, _ = make_notes_scheduler(db)

    sent = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    assert sent is True
    assert bot.recipients == [USER_ID]
    assert "Завтра в 11:40" in bot.last_text
    assert "Безопасность жизнедеятельности" in bot.last_text
    assert "дочитать главу 3" in bot.last_text
    buttons = [
        button.text for row in bot.last_markup.inline_keyboard for button in row
    ]
    assert buttons == [texts.BTN_NOTE_DONE, texts.BTN_NOTE_NOT_DONE]
    assert await db_module.already_reminded(db, note.id, db_module.REMINDER_DAY_BEFORE)


async def test_morning_reminder_has_no_not_done_button(
    db: aiosqlite.Connection,
) -> None:
    """Утром повторять уже нечем: «Не сделал» была бы кнопкой в никуда."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    sent = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_MORNING, moment=msk(TUE_ODD_NEXT, 8, 0)
    )

    assert sent is True
    assert "Доброе утро" in bot.last_text
    buttons = [
        button.text for row in bot.last_markup.inline_keyboard for button in row
    ]
    assert buttons == [texts.BTN_NOTE_DONE]


async def test_reminder_does_not_close_the_note(db: aiosqlite.Connection) -> None:
    """Отметка в журнале — это «напоминание ушло», а не «домашка сделана»."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, _, _ = make_notes_scheduler(db)

    await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    still_open = await db_module.get_note(db, note.id)
    assert still_open is not None and still_open.is_open is True


async def test_same_reminder_is_sent_only_once(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    first = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )
    second = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 45)
    )

    assert (first, second) == (True, False)
    assert len(bot.sent) == 1


async def test_morning_reminder_still_goes_after_the_day_before_one(
    db: aiosqlite.Connection,
) -> None:
    """Два разных вида напоминания — два отдельных сообщения, а не одно."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )
    await note_jobs.send_reminder(
        note.id, db_module.REMINDER_MORNING, moment=msk(TUE_ODD_NEXT, 8, 0)
    )

    assert len(bot.sent) == 2


async def test_closed_note_gets_no_reminder(db: aiosqlite.Connection) -> None:
    """Заметку закрыли между планированием и отправкой — сообщение уже не нужно."""
    await make_onboarded_user(db)
    note = await make_note(db)
    await db_module.close_note(db, note.id, USER_ID)
    note_jobs, bot, _ = make_notes_scheduler(db)

    sent = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    assert sent is False
    assert bot.sent == []


async def test_missing_note_does_not_crash_the_job(db: aiosqlite.Connection) -> None:
    note_jobs, bot, _ = make_notes_scheduler(db)

    assert (
        await note_jobs.send_reminder(
            999, db_module.REMINDER_MORNING, moment=msk(TUE_ODD_NEXT, 8, 0)
        )
        is False
    )
    assert bot.sent == []


async def test_reminder_after_the_lesson_started_is_skipped(
    db: aiosqlite.Connection,
) -> None:
    """Задача сработала с большим опозданием: пара идёт, напоминать поздно."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, _ = make_notes_scheduler(db)

    sent = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_MORNING, moment=msk(TUE_ODD_NEXT, 11, 40)
    )

    assert sent is False
    assert bot.sent == []
    assert await db_module.already_reminded(db, note.id, db_module.REMINDER_MORNING) is False


async def test_failed_reminder_is_not_marked_and_can_be_retried(
    db: aiosqlite.Connection,
) -> None:
    """Telegram не ответил — отметки нет, значит напоминание не потеряно."""
    await make_onboarded_user(db)
    note = await make_note(db)
    broken = FakeBot(error=RuntimeError("Telegram недоступен"))
    failing, _, _ = make_notes_scheduler(db, bot=broken)

    sent = await failing.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    assert sent is False
    assert await db_module.already_reminded(db, note.id, db_module.REMINDER_DAY_BEFORE) is False

    retry, working_bot, _ = make_notes_scheduler(db)
    assert (
        await retry.send_reminder(
            note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 45)
        )
        is True
    )
    assert len(working_bot.sent) == 1


async def test_reminder_escapes_html_in_the_note_text(
    db: aiosqlite.Connection,
) -> None:
    """Сообщения уходят с parse_mode=HTML: «<3» в домашке не должно ломать отправку."""
    await make_onboarded_user(db)
    note = await make_note(db, text="сделать <b>тег</b> & сравнить a<b")
    note_jobs, bot, _ = make_notes_scheduler(db)

    await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    assert "&lt;b&gt;тег&lt;/b&gt;" in bot.last_text
    assert "&amp;" in bot.last_text
    assert "a&lt;b" in bot.last_text


async def test_reminder_respects_allowed_user_ids(db: aiosqlite.Connection) -> None:
    await make_onboarded_user(db, OTHER_USER_ID)
    note = await make_note(db, telegram_id=OTHER_USER_ID)
    note_jobs, bot, _ = make_notes_scheduler(
        db, config=make_config(ALLOWED_USER_IDS=str(USER_ID))
    )

    sent = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    assert sent is False
    assert bot.sent == []


async def test_send_reminder_uses_clock_now_by_default(
    db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_onboarded_user(db)
    note = await make_note(db)
    monkeypatch.setattr(clock, "now", lambda *args, **kwargs: msk(TUE_ODD_NEXT, 8, 0))
    note_jobs, bot, _ = make_notes_scheduler(db)

    assert await note_jobs.send_reminder(note.id, db_module.REMINDER_MORNING) is True
    assert len(bot.sent) == 1


# ======================================================================================
# 7. Полный цикл на одной заметке
# ======================================================================================


async def test_full_cycle_from_question_to_closed_note(
    db: aiosqlite.Connection,
) -> None:
    """Вопрос после пары → заметка → напоминание за сутки → «Сделал» → утром тишина."""
    await make_onboarded_user(db)
    note_jobs, bot, fake = make_notes_scheduler(db)

    # 1. Днём пары бот ставит вопрос и задаёт его через 10 минут после звонка.
    await note_jobs.plan_today(msk(TUE_ODD, 5, 0))
    assert prompt_job_id("L1216", TUE_ODD) in [job.id for job in fake.prompt_jobs]
    assert await note_jobs.send_prompt("L1216", TUE_ODD, moment=msk(TUE_ODD, 13, 15)) == 1

    # 2. Человек ответил «Есть» и прислал текст — это делает хендлер, здесь просто база.
    note = await make_note(db, text="доделать отчёт по лабе")

    # 3. Накануне следующей такой пары уходит напоминание.
    planned = await note_jobs.plan_reminders(msk(date(2026, 9, 14), 5, 0))
    assert planned == [
        reminder_job_id(note.id, db_module.REMINDER_DAY_BEFORE),
        reminder_job_id(note.id, db_module.REMINDER_MORNING),
    ]
    assert await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )
    assert "доделать отчёт по лабе" in bot.last_text

    # 4. Нажали «Сделал».
    closed = await db_module.close_note(db, note.id, USER_ID)
    assert closed is not None and closed.is_open is False

    # 5. Утром в день пары бот молчит: закрытая заметка больше не напоминает о себе.
    messages_before = len(bot.sent)
    assert await note_jobs.plan_reminders(msk(TUE_ODD_NEXT, 5, 0)) == []
    assert (
        await note_jobs.send_reminder(
            note.id, db_module.REMINDER_MORNING, moment=msk(TUE_ODD_NEXT, 8, 0)
        )
        is False
    )
    assert len(bot.sent) == messages_before


async def test_not_done_keeps_the_note_and_lets_the_morning_reminder_go(
    db: aiosqlite.Connection,
) -> None:
    """«Не сделал» ничего не закрывает — значит утренний повтор обязан прийти."""
    await make_onboarded_user(db)
    note = await make_note(db)
    note_jobs, bot, _ = make_notes_scheduler(db)
    await note_jobs.send_reminder(
        note.id, db_module.REMINDER_DAY_BEFORE, moment=msk(date(2026, 9, 14), 11, 40)
    )

    # Кнопку «Не сделал» обрабатывает хендлер: он не трогает ни заметку, ни журнал.
    sent = await note_jobs.send_reminder(
        note.id, db_module.REMINDER_MORNING, moment=msk(TUE_ODD_NEXT, 8, 0)
    )

    assert sent is True
    assert len(bot.sent) == 2
    assert views.format_note_reminder(note, lesson_by_id("L1216"), morning=True) == bot.last_text
