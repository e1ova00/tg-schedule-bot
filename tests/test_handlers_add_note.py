"""Тесты команды /addnote: заметка к любой паре, не дожидаясь вопроса после пары.

Telegram не поднимается: сообщение и нажатие — заглушки из `tests/fakes.py`, база
временная, «сегодня» подменяется через `app.clock.today`.

Главное в этой доработке — **один путь сохранения на два входа**. Кнопка выбора пары
присылает ровно тот же callback, что и кнопка «Есть» в вопросе после пары
(`note:has:L1216:2026-09-01`), поэтому дальше работает `app/handlers/notes.py`: то же
состояние `NoteEntry.text`, тот же расчёт срока, то же подтверждение. Второго пути
сохранения в проекте быть не должно, и это здесь проверяется отдельно.

Второе важное следствие: если заметка по паре уже создана через /addnote, автоматический
вопрос «Есть / Нет» после этой же пары задаваться не должен — человек уже всё написал.
Это проверяется настоящим `NotesScheduler` на подставном планировщике.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import aiosqlite
import pytest
from aiogram import Bot, Router
from aiogram.filters import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, Update
from aiogram.types import User as TgUser

from app import clock, db as db_module, keyboards, notes, texts, views
from app.handlers.add_note import (
    AddNote,
    handle_add_note,
    handle_add_note_cancel,
    handle_add_note_day,
    handle_add_note_day_button,
    offer_lessons,
)
from app.handlers.notes import NoteEntry, handle_note_has, handle_note_text
from app.handlers.onboarding import Onboarding
from app.notes_scheduler import prompt_job_id
from app.schedule import ScheduleError, clear_cache, lesson_by_id, lessons_on
from fakes import USER_ID, FakeCallback, FakeMessage, make_onboarded_user
from test_notes_scheduler import make_notes_scheduler, msk

OTHER_USER_ID = 777

MON_ODD = date(2026, 8, 31)  # весь день дистанционный, но домашку задать могут
TUE_ODD = date(2026, 9, 1)  # L1216 11:40, L1218 13:45, L1220 15:20
WED_ODD = date(2026, 9, 2)  # L1221 10:05, L1222 11:40, L1223 13:45 — все «каждую неделю»
THU_ODD = date(2026, 9, 3)  # пар нет
FRI_EVEN = date(2026, 9, 11)  # пар нет
TUE_ODD_NEXT = date(2026, 9, 15)  # следующая L1216 — срок заметки
WED_NEXT = date(2026, 9, 9)  # следующая L1223 — через неделю, пара «каждую неделю»


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def freeze_today(monkeypatch: pytest.MonkeyPatch):
    def _freeze(day: date) -> None:
        monkeypatch.setattr(clock, "today", lambda *args, **kwargs: day)

    return _freeze


def command(args: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command="addnote", args=args)


def day_button(day: date) -> str:
    return f"{keyboards.CB_ADD_NOTE}:{keyboards.ADD_NOTE_DAY}:{day.isoformat()}"


def buttons(markup: Any) -> list[tuple[str, str]]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [
        (button.text, button.callback_data or "")
        for row in markup.inline_keyboard
        for button in row
    ]


async def pick_lesson_and_write(
    db: aiosqlite.Connection,
    state: FSMContext,
    callback_data: str,
    text: str,
    telegram_id: int = USER_ID,
) -> FakeMessage:
    """Нажатие на пару и текст заметки — тот же путь, что и у кнопки «Есть» после пары."""
    from fakes import FakeUser

    await handle_note_has(FakeCallback(data=callback_data), state)  # type: ignore[arg-type]
    answer = FakeMessage(text=text, from_user=FakeUser(id=telegram_id))
    await handle_note_text(answer, state, db)  # type: ignore[arg-type]
    return answer


# ======================================================================================
# 1. Вход в диалог: /addnote с датой и без
# ======================================================================================


async def test_addnote_without_date_asks_for_a_day(
    state: FSMContext, freeze_today
) -> None:
    freeze_today(WED_ODD)
    message = FakeMessage(text="/addnote")

    await handle_add_note(message, command(), state)  # type: ignore[arg-type]

    assert message.answers == [texts.ADDNOTE_ASK_DATE]
    assert await state.get_state() == AddNote.day.state
    assert buttons(message.last_markup) == [
        (texts.BTN_DAY_TODAY, day_button(WED_ODD)),
        (texts.BTN_DAY_TOMORROW, day_button(date(2026, 9, 3))),
    ]


async def test_addnote_with_date_shows_lessons_right_away(
    state: FSMContext, freeze_today
) -> None:
    """/addnote 2026-09-01 — сразу список пар, без лишнего вопроса про дату."""
    freeze_today(WED_ODD)
    message = FakeMessage(text="/addnote 2026-09-01")

    await handle_add_note(message, command("2026-09-01"), state)  # type: ignore[arg-type]

    assert message.answers == [views.format_lesson_pick(TUE_ODD)]
    assert "Вторник" in message.answers[0]
    assert "1 сентября" in message.answers[0]
    assert await state.get_state() is None, "дальше диалогом управляют кнопки"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("вчера", id="слово вместо даты"),
        pytest.param("01.09.2026", id="точки вместо дефисов"),
        pytest.param("2026-02-29", id="29 февраля в невисокосном году"),
    ],
)
async def test_addnote_with_bad_date_explains_the_format(
    state: FSMContext, freeze_today, raw: str
) -> None:
    freeze_today(WED_ODD)
    message = FakeMessage(text=f"/addnote {raw}")

    await handle_add_note(message, command(raw), state)  # type: ignore[arg-type]

    assert message.answers == [texts.ADDNOTE_BAD_DATE.format(value=raw)]
    assert await state.get_state() is None


async def test_addnote_bad_date_is_escaped(state: FSMContext, freeze_today) -> None:
    freeze_today(WED_ODD)
    message = FakeMessage(text="/addnote <b>")

    await handle_add_note(message, command("<b>"), state)  # type: ignore[arg-type]

    assert "&lt;b&gt;" in message.answers[0]


# ======================================================================================
# 2. Выбор дня: текстом и кнопкой
# ======================================================================================


async def test_day_typed_as_text_shows_lessons(state: FSMContext, freeze_today) -> None:
    freeze_today(WED_ODD)
    await handle_add_note(FakeMessage(text="/addnote"), command(), state)  # type: ignore[arg-type]

    answer = FakeMessage(text="2026-09-01")
    await handle_add_note_day(answer, state)  # type: ignore[arg-type]

    assert answer.answers == [views.format_lesson_pick(TUE_ODD)]
    assert len(buttons(answer.last_markup)) == len(lessons_on(TUE_ODD))
    assert await state.get_state() is None


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        pytest.param(texts.BTN_DAY_TODAY, WED_ODD, id="кнопка «Сегодня»"),
        pytest.param(texts.BTN_DAY_TOMORROW, THU_ODD, id="кнопка «Завтра»"),
    ],
)
async def test_day_chosen_by_button(
    state: FSMContext, freeze_today, label: str, expected: date
) -> None:
    """Кнопки «Сегодня»/«Завтра» ведут в тот же путь, что и дата текстом."""
    freeze_today(WED_ODD)
    ask = FakeMessage(text="/addnote")
    await handle_add_note(ask, command(), state)  # type: ignore[arg-type]
    data = dict(buttons(ask.last_markup))[label]

    callback = FakeCallback(data=data)
    await handle_add_note_day_button(callback, state)  # type: ignore[arg-type]

    assert callback.message is not None
    assert callback.answered == [(None, False)]
    assert callback.message.edited_markups == 1, "кнопки выбора дня надо убрать"
    if lessons_on(expected):
        assert callback.message.answers == [views.format_lesson_pick(expected)]
    else:
        assert callback.message.answers == [views.format_no_lessons_to_note(expected)]
    assert await state.get_state() is None


async def test_today_button_matches_the_frozen_date(
    state: FSMContext, freeze_today
) -> None:
    """Кнопка «Сегодня» несёт именно сегодняшнюю дату по Москве, а не «какую-нибудь»."""
    freeze_today(TUE_ODD)
    message = FakeMessage(text="/addnote")

    await handle_add_note(message, command(), state)  # type: ignore[arg-type]

    assert dict(buttons(message.last_markup))[texts.BTN_DAY_TODAY] == day_button(TUE_ODD)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("послезавтра", id="слово вместо даты"),
        pytest.param("01.09.2026", id="точки вместо дефисов"),
        pytest.param("", id="пустое сообщение"),
        pytest.param("/notes", id="чужая команда"),
    ],
)
async def test_bad_day_keeps_the_dialog_alive(
    state: FSMContext, freeze_today, raw: str
) -> None:
    """Непонятная дата — вежливая просьба повторить, а не выход из диалога."""
    freeze_today(WED_ODD)
    await handle_add_note(FakeMessage(text="/addnote"), command(), state)  # type: ignore[arg-type]

    answer = FakeMessage(text=raw)
    await handle_add_note_day(answer, state)  # type: ignore[arg-type]

    assert answer.answers == [texts.ADDNOTE_BAD_DATE.format(value=raw.strip())]
    assert await state.get_state() == AddNote.day.state, "всё ещё ждём дату"


async def test_broken_day_button_is_handled_politely(state: FSMContext) -> None:
    callback = FakeCallback(
        data=f"{keyboards.CB_ADD_NOTE}:{keyboards.ADD_NOTE_DAY}:завтра"
    )

    await handle_add_note_day_button(callback, state)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]
    assert callback.message is not None
    assert callback.message.answers == []


async def test_cancel_during_day_choice(state: FSMContext, freeze_today) -> None:
    freeze_today(WED_ODD)
    await handle_add_note(FakeMessage(text="/addnote"), command(), state)  # type: ignore[arg-type]

    answer = FakeMessage(text="/cancel")
    await handle_add_note_cancel(answer, state)  # type: ignore[arg-type]

    assert answer.answers == [texts.ADDNOTE_CANCELLED]
    assert await state.get_state() is None


# ======================================================================================
# 3. Список пар дня
# ======================================================================================


async def test_lesson_buttons_cover_every_lesson_of_the_day(state: FSMContext) -> None:
    message = FakeMessage()

    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]

    day = lessons_on(WED_ODD)
    found = buttons(message.last_markup)
    assert len(found) == len(day) == 3
    for (label, data), lesson in zip(found, day, strict=True):
        assert label.startswith(lesson.start_text)
        assert data == f"{keyboards.CB_NOTES}:{keyboards.NOTE_HAS}:{lesson.id}:2026-09-02"


async def test_remote_lessons_can_also_be_chosen(state: FSMContext) -> None:
    """Понедельник полностью дистанционный, но домашку на дистанте задают точно так же."""
    message = FakeMessage()

    await offer_lessons(message, MON_ODD, state)  # type: ignore[arg-type]

    assert len(buttons(message.last_markup)) == len(lessons_on(MON_ODD)) == 3


async def test_long_subject_is_shortened_on_the_button(state: FSMContext) -> None:
    """Telegram режет длинные подписи сам и некрасиво — режем осмысленно."""
    message = FakeMessage()

    await offer_lessons(message, MON_ODD, state)  # type: ignore[arg-type]

    for label, _ in buttons(message.last_markup):
        assert len(label) <= keyboards.MAX_BUTTON_SUBJECT + len("00:00 · ")
    assert any("…" in label for label, _ in buttons(message.last_markup))


@pytest.mark.parametrize(
    "day",
    [
        pytest.param(THU_ODD, id="четверг по числителю"),
        pytest.param(FRI_EVEN, id="пятница по знаменателю"),
        pytest.param(date(2026, 9, 6), id="воскресенье"),
    ],
)
async def test_empty_day_ends_the_dialog_politely(
    db: aiosqlite.Connection, state: FSMContext, day: date
) -> None:
    """Пустой день — норма, а не ошибка: вежливо выходим и текста заметки не ждём."""
    message = FakeMessage()

    await offer_lessons(message, day, state)  # type: ignore[arg-type]

    assert message.answers == [views.format_no_lessons_to_note(day)]
    assert message.markups == [None], "кнопок выбора пары быть не должно"
    assert await state.get_state() is None
    assert await db_module.list_notes(db) == []


async def test_empty_day_message_suggests_trying_another_one(
    state: FSMContext,
) -> None:
    message = FakeMessage()

    await offer_lessons(message, THU_ODD, state)  # type: ignore[arg-type]

    assert "/addnote" in message.answers[0]
    assert "3 сентября" in message.answers[0]


async def test_broken_schedule_answers_politely(
    state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr("app.handlers.add_note.lessons_on", boom)
    await state.set_state(AddNote.day)
    message = FakeMessage()

    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]

    assert message.answers == [texts.SCHEDULE_ERROR]
    assert await state.get_state() is None


# ======================================================================================
# 4. Один путь сохранения на два входа
# ======================================================================================


def test_lesson_button_repeats_the_callback_of_the_yes_button() -> None:
    """Кнопка выбора пары в /addnote и кнопка «Есть» после пары — один и тот же callback.

    Именно из-за этого дальше работает уже существующий хендлер из `notes.py`, и второго
    места, где создаётся заметка, в проекте нет.
    """
    day_lessons = lessons_on(WED_ODD)
    from_addnote = [data for _, data in buttons(keyboards.lesson_choice(WED_ODD, day_lessons))]
    from_prompt = [
        keyboards.note_prompt(lesson.id, WED_ODD).inline_keyboard[0][0].callback_data
        for lesson in day_lessons
    ]

    assert from_addnote == from_prompt


async def test_full_cycle_saves_the_note_with_lesson_date_and_due_date(
    db: aiosqlite.Connection, state: FSMContext, freeze_today
) -> None:
    """Полный цикл: /addnote → день → пара → текст → запись в базе с правильным сроком."""
    freeze_today(WED_ODD)
    ask = FakeMessage(text="/addnote")
    await handle_add_note(ask, command(), state)  # type: ignore[arg-type]

    day = FakeMessage(text="2026-09-01")
    await handle_add_note_day(day, state)  # type: ignore[arg-type]
    chosen = next(
        data for label, data in buttons(day.last_markup) if label.startswith("11:40")
    )

    answer = await pick_lesson_and_write(db, state, chosen, "дочитать главу 3")

    saved = await db_module.list_notes(db)
    assert len(saved) == 1
    note = saved[0]
    assert note.telegram_id == USER_ID
    assert note.lesson_id == "L1216"
    assert note.lesson_date == TUE_ODD
    assert note.due_date == TUE_ODD_NEXT, "пара по числителю — следующая через две недели"
    assert note.text == "дочитать главу 3"
    assert note.is_open is True
    assert "15 сентября" in answer.last_answer
    assert await state.get_state() is None


async def test_due_date_matches_the_shared_formula(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Срок считает та же функция, что и в обычном сценарии, — расходиться нечему."""
    message = FakeMessage()
    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]
    _, chosen = buttons(message.last_markup)[-1]  # L1223, «каждую неделю»

    await pick_lesson_and_write(db, state, chosen, "макет главной")

    note = (await db_module.list_notes(db))[0]
    lesson = lesson_by_id("L1223")
    assert lesson is not None
    assert note.due_date == notes.next_lesson_occurrence(lesson, WED_ODD)
    assert note.due_date == WED_NEXT, "пара каждую неделю — срок ровно через неделю"


async def test_note_added_by_hand_appears_in_the_list(
    db: aiosqlite.Connection, state: FSMContext, freeze_today
) -> None:
    """Заметка из /addnote — обычная заметка: она видна в /notes среди незакрытых."""
    from app.handlers.notes import send_notes_list

    freeze_today(WED_ODD)
    message = FakeMessage()
    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]
    _, chosen = buttons(message.last_markup)[0]
    await pick_lesson_and_write(db, state, chosen, "принести флешку")

    listing = FakeMessage()
    await send_notes_list(listing, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]

    assert "принести флешку" in listing.all_text
    assert "Прикладной дизайн" in listing.all_text


async def test_addnote_does_not_create_a_second_saving_path(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Пока текст не пришёл, в базе не должно появляться ничего — даже заглушки."""
    message = FakeMessage()
    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]
    _, chosen = buttons(message.last_markup)[0]

    await handle_note_has(FakeCallback(data=chosen), state)  # type: ignore[arg-type]

    assert await db_module.list_notes(db) == []
    assert await state.get_state() == NoteEntry.text.state


# ======================================================================================
# 5. Заметка есть — автоматический вопрос после пары больше не нужен
# ======================================================================================


async def make_note_via_addnote(
    db: aiosqlite.Connection,
    state: FSMContext,
    day: date,
    lesson_id: str,
    text: str = "уже записал заранее",
) -> None:
    """Проходит /addnote целиком: день → пара → текст."""
    message = FakeMessage()
    await offer_lessons(message, day, state)  # type: ignore[arg-type]
    chosen = next(
        data
        for _, data in buttons(message.last_markup)
        if data.endswith(f":{lesson_id}:{day.isoformat()}")
    )
    await pick_lesson_and_write(db, state, chosen, text)


async def test_note_added_by_hand_marks_the_lesson_as_asked(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Заметка есть — значит спрашивать «Есть что записать?» по этой паре уже не о чем."""
    assert await db_module.already_prompted(db, USER_ID, "L1223", WED_ODD) is False

    await make_note_via_addnote(db, state, WED_ODD, "L1223")

    assert await db_module.already_prompted(db, USER_ID, "L1223", WED_ODD) is True


async def test_prompt_for_that_lesson_is_not_planned_anymore(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Самое важное в этой доработке.

    Пара L1223 в среду начинается в 13:45 и ещё не прошла. Человек добавил заметку
    утром через /addnote — значит вечернего вопроса «Есть / Нет» по ней быть не должно,
    а по остальным парам дня вопросы остаются.
    """
    await make_onboarded_user(db)
    await make_note_via_addnote(db, state, WED_ODD, "L1223")
    note_jobs, bot, fake = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))

    assert prompt_job_id("L1223", WED_ODD) not in planned
    assert planned == [
        prompt_job_id("L1221", WED_ODD),
        prompt_job_id("L1222", WED_ODD),
    ]
    assert fake.job(prompt_job_id("L1223", WED_ODD)) is None
    assert bot.sent == []


async def test_question_does_not_reach_the_author_of_the_note(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Второго человека спросить всё равно надо: заметка есть только у первого."""
    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    await make_note_via_addnote(db, state, WED_ODD, "L1223")
    note_jobs, bot, _ = make_notes_scheduler(db)

    planned = await note_jobs.plan_prompts(WED_ODD, msk(WED_ODD, 5, 0))
    asked = await note_jobs.send_prompt("L1223", WED_ODD, moment=msk(WED_ODD, 15, 20))

    assert prompt_job_id("L1223", WED_ODD) in planned, "второго-то ещё не спрашивали"
    assert asked == 1
    assert bot.recipients == [OTHER_USER_ID]


async def test_sending_the_question_skips_the_author_of_the_note(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Даже если задача осталась в очереди, сам вопрос автору заметки не уходит."""
    await make_onboarded_user(db)
    await make_note_via_addnote(db, state, WED_ODD, "L1223")
    note_jobs, bot, _ = make_notes_scheduler(db)

    asked = await note_jobs.send_prompt("L1223", WED_ODD, moment=msk(WED_ODD, 15, 20))

    assert asked == 0
    assert bot.sent == []


async def test_other_lessons_of_the_day_are_still_asked_about(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Отметка ставится только на выбранную пару, а не на весь день."""
    await make_onboarded_user(db)
    await make_note_via_addnote(db, state, WED_ODD, "L1223")
    note_jobs, bot, _ = make_notes_scheduler(db)

    asked = await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    assert asked == 1
    assert bot.recipients == [USER_ID]


async def test_the_same_lesson_on_another_date_is_still_asked_about(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Заметка привязана к паре И дате: через неделю та же пара спрашивается заново."""
    await make_onboarded_user(db)
    await make_note_via_addnote(db, state, WED_ODD, "L1223")
    note_jobs, bot, _ = make_notes_scheduler(db)

    asked = await note_jobs.send_prompt("L1223", WED_NEXT, moment=msk(WED_NEXT, 15, 20))

    assert asked == 1
    assert bot.recipients == [USER_ID]


# --- Симметричный случай: обычный путь тоже помечает журнал ---------------------------


async def test_usual_path_still_marks_the_prompt_log(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Вопрос → «Есть» → текст: отметка «уже спрошено» на месте, путь не сломался."""
    await make_onboarded_user(db)
    note_jobs, bot, _ = make_notes_scheduler(db)
    await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))
    assert len(bot.sent) == 1

    data = bot.last_markup.inline_keyboard[0][0].callback_data
    await pick_lesson_and_write(db, state, data, "доделать макет")

    assert await db_module.already_prompted(db, USER_ID, "L1221", WED_ODD) is True
    assert len(await db_module.list_notes(db)) == 1


async def test_repeated_marking_does_not_move_the_first_timestamp(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """`mark_prompted` зовётся дважды на обычном пути — второй раз должен быть безобидным.

    Интересно именно время первого вопроса: по нему потом разбираются, почему бот
    спросил не тогда, когда ожидалось.
    """
    await make_onboarded_user(db)
    note_jobs, bot, _ = make_notes_scheduler(db)
    await note_jobs.send_prompt("L1221", WED_ODD, moment=msk(WED_ODD, 11, 40))

    async with db.execute("SELECT asked_at FROM note_prompt_log") as cursor:
        before = [row["asked_at"] for row in await cursor.fetchall()]

    data = bot.last_markup.inline_keyboard[0][0].callback_data
    await pick_lesson_and_write(db, state, data, "доделать макет")

    async with db.execute("SELECT asked_at FROM note_prompt_log") as cursor:
        after = [row["asked_at"] for row in await cursor.fetchall()]

    assert len(after) == 1, "второй строки в журнале появиться не должно"
    assert after == before


async def test_marking_failure_does_not_lose_the_note(
    db: aiosqlite.Connection, state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Журнал — вещь служебная: если он не записался, заметка всё равно должна сохраниться."""

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("база подвела")

    message = FakeMessage()
    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]
    _, chosen = buttons(message.last_markup)[0]
    monkeypatch.setattr(db_module, "mark_prompted", boom)

    answer = await pick_lesson_and_write(db, state, chosen, "важная домашка")

    saved = await db_module.list_notes(db)
    assert [note.text for note in saved] == ["важная домашка"]
    assert answer.last_answer != texts.STALE_BUTTON


async def test_note_of_one_user_does_not_silence_the_question_for_another(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Журнал вопросов персональный: чужая заметка не должна отменять мой вопрос."""
    from fakes import FakeUser

    await make_onboarded_user(db, USER_ID)
    await make_onboarded_user(db, OTHER_USER_ID)
    message = FakeMessage()
    await offer_lessons(message, WED_ODD, state)  # type: ignore[arg-type]
    _, chosen = buttons(message.last_markup)[0]
    await handle_note_has(FakeCallback(data=chosen), state)  # type: ignore[arg-type]
    await handle_note_text(  # type: ignore[arg-type]
        FakeMessage(text="чужая домашка", from_user=FakeUser(id=OTHER_USER_ID)), state, db
    )

    assert await db_module.already_prompted(db, USER_ID, "L1221", WED_ODD) is False
    assert await db_module.already_prompted(db, OTHER_USER_ID, "L1221", WED_ODD) is True


# ======================================================================================
# 6. Маршрутизация: /addnote и его /cancel в общей цепочке роутеров
# ======================================================================================


class StubBot(Bot):
    """Bot с настоящим разбором апдейтов, но без сети."""

    def __init__(self) -> None:
        super().__init__(token="123456:AAHfake-token")
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: int | None = None) -> None:  # type: ignore[override]
        self.calls.append(method)
        return None

    @property
    def texts(self) -> list[str]:
        return [getattr(call, "text", "") or "" for call in self.calls]


@pytest.fixture()
async def stub_bot():
    bot = StubBot()
    try:
        yield bot
    finally:
        await bot.session.close()


@pytest.fixture()
def routed_state(stub_bot: StubBot) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=stub_bot.id, chat_id=USER_ID, user_id=USER_ID),
    )


async def route_message(
    router: Router,
    bot: StubBot,
    db: aiosqlite.Connection,
    state: FSMContext,
    text: str,
) -> None:
    message = Message(
        message_id=1,
        date=datetime(2026, 9, 2, 8, 0),
        chat=Chat(id=USER_ID, type="private"),
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="Тестер"),
        text=text,
    ).as_(bot)
    await router.propagate_event(
        "message",
        message,
        bot=bot,
        db=db,
        state=state,
        raw_state=await state.get_state(),
        event_update=Update(update_id=1, message=message),
        event_from_user=message.from_user,
    )


async def test_cancel_while_choosing_a_day_belongs_to_addnote(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Свой /cancel: общий из онбординга перехватить его не должен."""
    await routed_state.set_state(AddNote.day)

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.ADDNOTE_CANCELLED]
    assert texts.ONBOARDING_CANCELLED not in stub_bot.texts
    assert await routed_state.get_state() is None


async def test_cancel_during_onboarding_is_not_hijacked_by_addnote(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    await routed_state.set_state(Onboarding.prep)

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.ONBOARDING_CANCELLED]


async def test_date_typed_in_the_day_state_is_not_eaten_by_onboarding(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
    freeze_today,
) -> None:
    """Дата текстом должна дойти именно до /addnote, а не до fallback или онбординга."""
    freeze_today(WED_ODD)
    await routed_state.set_state(AddNote.day)

    await route_message(root_router, stub_bot, db, routed_state, "2026-09-02")

    assert stub_bot.texts == [views.format_lesson_pick(WED_ODD)]
    assert texts.UNKNOWN_COMMAND not in stub_bot.texts
    assert await routed_state.get_state() is None


def test_notes_router_comes_before_add_note(root_router: Router) -> None:
    """Кнопку выбора пары обязан обработать хендлер из notes — он один на два входа."""
    names = [child.name for child in root_router.sub_routers]

    assert names.index("notes") < names.index("add-note") < names.index("onboarding")
