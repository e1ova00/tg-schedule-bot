"""Тесты хендлеров заметок: app/handlers/notes.py.

Критерий приёмки этапа 6 — «полный цикл: создать заметку, увидеть в списке, получить
напоминание, закрыть кнопкой». Здесь проигран весь этот цикл, только без Telegram:
вместо `Message` и `CallbackQuery` — заглушки из `tests/fakes.py`, база временная.

Отдельно проверяются две вещи, которые легко сломать незаметно:

* **чужую заметку закрыть нельзя** — id заметки лежит прямо в callback_data кнопки;
* **`/cancel` во время ввода текста** должен отменить именно заметку, а не диалог
  знакомства. Для этого есть тест на настоящем корневом роутере: он проверяет не «какой
  хендлер мы позвали руками», а «какой из них выберет aiogram».
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import aiosqlite
import pytest
from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, InlineKeyboardMarkup, Message, Update
from aiogram.types import User as TgUser

from app import clock, db as db_module, keyboards, texts, views
from app.handlers.notes import (
    LESSON_DATE_KEY,
    LESSON_ID_KEY,
    MAX_NOTES_SHOWN,
    NoteEntry,
    handle_note_cancel,
    handle_note_done,
    handle_note_has,
    handle_note_none,
    handle_note_not_done,
    handle_note_text,
    handle_notes,
    handle_notes_period,
    send_notes_list,
)
from app.handlers.onboarding import Onboarding
from app.schedule import clear_cache, lesson_by_id
from fakes import USER_ID, FakeCallback, FakeMessage, FakeUser

OTHER_USER_ID = 777

ODD_TUE = "L1216"  # вторник, числитель, 11:40–13:05, Безопасность жизнедеятельности
BOTH_WED = "L1221"  # среда, каждую неделю, 10:05–11:30, Прикладной дизайн

TUE_ODD = date(2026, 9, 1)
WED_ODD = date(2026, 9, 2)
SUN_ODD = date(2026, 9, 6)
TUE_ODD_NEXT = date(2026, 9, 15)  # следующая L1216 — срок заметки
LAST_WEEK = date(2026, 8, 26)  # среда прошлой недели


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


def cb_data(step: str, lesson_id: str = ODD_TUE, day: date = TUE_ODD) -> str:
    return f"{keyboards.CB_NOTES}:{step}:{lesson_id}:{day.isoformat()}"


def button_texts(markup: Any) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.text for row in markup.inline_keyboard for button in row]


async def add_note(
    db: aiosqlite.Connection,
    *,
    telegram_id: int = USER_ID,
    lesson_id: str = ODD_TUE,
    lesson_date: date = TUE_ODD,
    due_date: date | None = TUE_ODD_NEXT,
    text: str = "дочитать главу 3",
    closed: bool = False,
) -> db_module.Note:
    note = await db_module.create_note(
        db, telegram_id, lesson_id, lesson_date, text, due_date
    )
    if closed:
        closed_note = await db_module.close_note(db, note.id, telegram_id)
        assert closed_note is not None
        return closed_note
    return note


# ======================================================================================
# 1. Вопрос после пары: «Нет»
# ======================================================================================


async def test_no_answer_creates_nothing(db: aiosqlite.Connection) -> None:
    callback = FakeCallback(data=cb_data(keyboards.NOTE_NONE))

    await handle_note_none(callback)  # type: ignore[arg-type]

    assert callback.message is not None
    assert callback.message.answers == [texts.NOTES_NOTHING_TO_SAVE]
    assert await db_module.list_notes(db) == []


async def test_no_answer_is_short_and_hides_the_buttons(db: aiosqlite.Connection) -> None:
    """Ответ на «Нет» — одна строка: развивать разговор не о чем."""
    callback = FakeCallback(data=cb_data(keyboards.NOTE_NONE))

    await handle_note_none(callback)  # type: ignore[arg-type]

    assert callback.answered == [(None, False)]
    assert callback.message is not None
    assert callback.message.edited_markups == 1
    assert len(callback.message.last_answer) < 60


# ======================================================================================
# 2. Вопрос после пары: «Есть» → текст заметки
# ======================================================================================


async def test_yes_answer_waits_for_the_text(state: FSMContext) -> None:
    callback = FakeCallback(data=cb_data(keyboards.NOTE_HAS))

    await handle_note_has(callback, state)  # type: ignore[arg-type]

    assert await state.get_state() == NoteEntry.text.state
    data = await state.get_data()
    assert data[LESSON_ID_KEY] == ODD_TUE
    assert data[LESSON_DATE_KEY] == TUE_ODD.isoformat()
    assert callback.message is not None
    assert callback.message.last_answer == texts.NOTES_ASK_TEXT
    assert callback.message.edited_markups == 1


async def test_text_is_saved_with_lesson_date_and_due_date(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Полный сценарий: «Есть» → текст → заметка привязана к паре, дате и сроку."""
    await handle_note_has(FakeCallback(data=cb_data(keyboards.NOTE_HAS)), state)  # type: ignore[arg-type]

    answer = FakeMessage(text="сделать лабу 2 и принести флешку")
    await handle_note_text(answer, state, db)  # type: ignore[arg-type]

    saved = await db_module.list_notes(db)
    assert len(saved) == 1
    note = saved[0]
    assert note.telegram_id == USER_ID
    assert note.lesson_id == ODD_TUE
    assert note.lesson_date == TUE_ODD
    assert note.due_date == TUE_ODD_NEXT, "Срок — следующая такая же пара, через две недели"
    assert note.text == "сделать лабу 2 и принести флешку"
    assert note.is_open is True
    assert "15 сентября" in answer.last_answer
    assert await state.get_state() is None


async def test_weekly_lesson_due_date_is_in_seven_days(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Пара «каждую неделю» — срок ровно через неделю, а не через две."""
    await handle_note_has(
        FakeCallback(data=cb_data(keyboards.NOTE_HAS, BOTH_WED, WED_ODD)), state  # type: ignore[arg-type]
    )

    await handle_note_text(FakeMessage(text="макет главной"), state, db)  # type: ignore[arg-type]

    note = (await db_module.list_notes(db))[0]
    assert note.due_date == date(2026, 9, 9)


async def test_note_about_unknown_lesson_is_saved_without_due_date(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Пара пропала из расписания — заметку всё равно сохраняем, просто без напоминаний."""
    await handle_note_has(
        FakeCallback(data=cb_data(keyboards.NOTE_HAS, "L9999", TUE_ODD)), state  # type: ignore[arg-type]
    )

    answer = FakeMessage(text="что-то важное")
    await handle_note_text(answer, state, db)  # type: ignore[arg-type]

    note = (await db_module.list_notes(db))[0]
    assert note.due_date is None
    assert answer.last_answer == texts.NOTES_SAVED_NO_DUE


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="пустое сообщение"),
        pytest.param("   ", id="одни пробелы"),
        pytest.param("/today", id="команда вместо текста"),
    ],
)
async def test_bad_text_does_not_create_a_note(
    db: aiosqlite.Connection, state: FSMContext, text: str
) -> None:
    await handle_note_has(FakeCallback(data=cb_data(keyboards.NOTE_HAS)), state)  # type: ignore[arg-type]

    answer = FakeMessage(text=text)
    await handle_note_text(answer, state, db)  # type: ignore[arg-type]

    assert await db_module.list_notes(db) == []
    assert answer.last_answer == texts.NOTES_TEXT_RETRY
    assert await state.get_state() == NoteEntry.text.state, "Ждём текст дальше"


async def test_broken_state_does_not_create_a_note(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Бот обновился посреди диалога — не молчим и не сохраняем мусор."""
    await state.set_state(NoteEntry.text)
    await state.update_data(**{LESSON_ID_KEY: ODD_TUE, LESSON_DATE_KEY: "позавчера"})

    answer = FakeMessage(text="домашка")
    await handle_note_text(answer, state, db)  # type: ignore[arg-type]

    assert await db_module.list_notes(db) == []
    assert answer.last_answer == texts.STALE_BUTTON
    assert await state.get_state() is None


# ======================================================================================
# 3. /cancel во время ввода текста
# ======================================================================================


async def test_cancel_during_entry_saves_nothing(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await handle_note_has(FakeCallback(data=cb_data(keyboards.NOTE_HAS)), state)  # type: ignore[arg-type]

    answer = FakeMessage(text="/cancel")
    await handle_note_cancel(answer, state)  # type: ignore[arg-type]

    assert await db_module.list_notes(db) == []
    assert answer.last_answer == texts.NOTES_ENTRY_CANCELLED
    assert await state.get_state() is None


async def test_note_states_do_not_overlap_with_onboarding(state: FSMContext) -> None:
    """FSM-состояния взаимоисключающие: в диалоге заметки шага знакомства нет."""
    await state.set_state(NoteEntry.text)
    assert await state.get_state() == NoteEntry.text.state
    assert await state.get_state() != Onboarding.prep.state

    await state.set_state(Onboarding.prep)
    assert await state.get_state() != NoteEntry.text.state


# --- Тот же /cancel, но через настоящий корневой роутер -------------------------------


class StubBot(Bot):
    """Bot с настоящим разбором апдейтов, но без сети: любой вызов API просто запоминаем."""

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


def telegram_message(text: str, bot: Bot, user_id: int = USER_ID) -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 9, 1, 13, 20),
        chat=Chat(id=user_id, type="private"),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Тестер"),
        text=text,
    ).as_(bot)


async def route_message(
    router: Router,
    bot: StubBot,
    db: aiosqlite.Connection,
    state: FSMContext,
    text: str,
) -> None:
    """Отдаёт сообщение настоящему корневому роутеру — как это делает диспетчер в бою.

    Так проверяется не «какой хендлер мы позвали руками», а «какой из них выберет aiogram»:
    именно это и решает, чей `/cancel` сработает первым.
    """
    message = telegram_message(text, bot)
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


@pytest.fixture()
def routed_state(stub_bot: StubBot) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=stub_bot.id, chat_id=USER_ID, user_id=USER_ID),
    )


async def test_cancel_in_note_entry_is_handled_by_notes_not_onboarding(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Главная причина, по которой роутер заметок подключён раньше онбординга."""
    await routed_state.set_state(NoteEntry.text)
    await routed_state.update_data(
        **{LESSON_ID_KEY: ODD_TUE, LESSON_DATE_KEY: TUE_ODD.isoformat()}
    )

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.NOTES_ENTRY_CANCELLED]
    assert texts.ONBOARDING_CANCELLED not in stub_bot.texts
    assert await routed_state.get_state() is None
    assert await db_module.list_notes(db) == []


async def test_cancel_outside_note_entry_still_belongs_to_onboarding(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Обратная сторона: вне диалога заметки /cancel по-прежнему отвечает как раньше."""
    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.NOTHING_TO_CANCEL]


async def test_cancel_during_onboarding_is_not_hijacked_by_notes(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """И третья сторона: знакомство отменяется своим текстом, а не текстом заметок."""
    await routed_state.set_state(Onboarding.prep)

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.ONBOARDING_CANCELLED]


async def test_plain_text_in_note_entry_becomes_a_note(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Обычный текст в состоянии заметки не должен утечь в онбординг или в fallback."""
    await routed_state.set_state(NoteEntry.text)
    await routed_state.update_data(
        **{LESSON_ID_KEY: ODD_TUE, LESSON_DATE_KEY: TUE_ODD.isoformat()}
    )

    await route_message(root_router, stub_bot, db, routed_state, "прочитать конспект")

    saved = await db_module.list_notes(db)
    assert len(saved) == 1
    assert saved[0].text == "прочитать конспект"
    assert texts.UNKNOWN_COMMAND not in stub_bot.texts


def test_notes_router_comes_before_onboarding(root_router: Router) -> None:
    """Порядок роутеров — это и есть механизм, который проверяют тесты выше."""
    names = [child.name for child in root_router.sub_routers]

    assert names.index("notes") < names.index("onboarding") < names.index("fallback")


# ======================================================================================
# 4. /notes: три периода
# ======================================================================================


async def test_notes_command_shows_open_notes_by_default(
    db: aiosqlite.Connection, state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_note(db, text="дочитать главу 3")
    monkeypatch.setattr(clock, "today", lambda *args, **kwargs: WED_ODD)
    message = FakeMessage(text="/notes")

    await handle_notes(message, state, db)  # type: ignore[arg-type]

    assert texts.NOTES_TITLES["open"] in message.answers
    assert "дочитать главу 3" in message.all_text


@pytest.mark.parametrize(
    "period",
    [keyboards.PERIOD_OPEN, keyboards.PERIOD_WEEK, keyboards.PERIOD_ALL],
)
async def test_empty_period_has_its_own_wording(
    db: aiosqlite.Connection, period: str
) -> None:
    """У каждого периода своё «пусто»: «всё сделано» и «заметок не было» — разные новости."""
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, period, WED_ODD)  # type: ignore[arg-type]

    assert message.answers == [views.notes_title(period), views.notes_empty(period)]
    assert message.last_answer == texts.NOTES_EMPTY[period]


def test_empty_texts_of_three_periods_differ() -> None:
    assert len(set(texts.NOTES_EMPTY.values())) == 3
    assert len(set(texts.NOTES_TITLES.values())) == 3


async def test_open_period_hides_closed_notes(db: aiosqlite.Connection) -> None:
    await add_note(db, text="ещё не сделано")
    await add_note(db, text="давно сделано", closed=True)
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]

    assert "ещё не сделано" in message.all_text
    assert "давно сделано" not in message.all_text


async def test_all_period_shows_closed_notes_with_a_mark(
    db: aiosqlite.Connection,
) -> None:
    await add_note(db, text="давно сделано", closed=True)
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_ALL, WED_ODD)  # type: ignore[arg-type]

    assert "давно сделано" in message.all_text
    assert texts.NOTE_ITEM_DONE_MARK in message.all_text
    assert texts.NOTE_ITEM_CLOSED in message.all_text


async def test_week_period_filters_by_lesson_date(db: aiosqlite.Connection) -> None:
    """«За неделю» — это неделя пары, от понедельника до воскресенья."""
    await add_note(db, lesson_date=TUE_ODD, text="на этой неделе")
    await add_note(db, lesson_date=LAST_WEEK, text="на прошлой неделе")
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_WEEK, WED_ODD)  # type: ignore[arg-type]

    assert "на этой неделе" in message.all_text
    assert "на прошлой неделе" not in message.all_text


async def test_week_period_includes_sunday(db: aiosqlite.Connection) -> None:
    """Воскресенье — граничный день недели, выпадать из фильтра оно не должно."""
    await add_note(db, lesson_date=SUN_ODD, text="воскресный хвост")
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_WEEK, SUN_ODD)  # type: ignore[arg-type]

    assert "воскресный хвост" in message.all_text


async def test_notes_show_only_own_records(db: aiosqlite.Connection) -> None:
    await add_note(db, telegram_id=OTHER_USER_ID, text="чужая домашка")
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_ALL, WED_ODD)  # type: ignore[arg-type]

    assert "чужая домашка" not in message.all_text
    assert message.last_answer == texts.NOTES_EMPTY["all"]


async def test_open_note_in_the_list_has_a_done_button(
    db: aiosqlite.Connection,
) -> None:
    note = await add_note(db, text="дочитать главу 3")
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]

    item_index = next(
        index for index, answer in enumerate(message.answers) if "дочитать главу 3" in answer
    )
    markup = message.markups[item_index]
    assert button_texts(markup) == [texts.BTN_NOTE_DONE]
    assert markup.inline_keyboard[0][0].callback_data == (
        f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:{note.id}"
    )


async def test_closed_note_in_the_list_has_no_button(db: aiosqlite.Connection) -> None:
    await add_note(db, text="давно сделано", closed=True)
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_ALL, WED_ODD)  # type: ignore[arg-type]

    item_index = next(
        index for index, answer in enumerate(message.answers) if "давно сделано" in answer
    )
    assert message.markups[item_index] is None


async def test_list_offers_the_other_two_periods(db: aiosqlite.Connection) -> None:
    await add_note(db)
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]

    assert message.last_answer == texts.NOTES_PERIOD_HINT
    assert button_texts(message.last_markup) == [texts.BTN_NOTES_WEEK, texts.BTN_NOTES_ALL]


async def test_period_button_switches_the_list(
    db: aiosqlite.Connection, state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_note(db, text="давно сделано", closed=True)
    monkeypatch.setattr(clock, "today", lambda *args, **kwargs: WED_ODD)
    callback = FakeCallback(
        data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_PERIOD}:{keyboards.PERIOD_ALL}"
    )

    await handle_notes_period(callback, state, db)  # type: ignore[arg-type]

    assert callback.message is not None
    assert texts.NOTES_TITLES["all"] in callback.message.answers
    assert "давно сделано" in callback.message.all_text


async def test_period_button_clears_pending_note_entry(
    db: aiosqlite.Connection, state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переключение периода посреди ожидания текста заметки снимает это ожидание.

    Иначе следующее сообщение (уже не текст заметки) молча ушло бы её содержимым.
    """
    monkeypatch.setattr(clock, "today", lambda *args, **kwargs: WED_ODD)
    await state.set_state(NoteEntry.text)
    callback = FakeCallback(
        data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_PERIOD}:{keyboards.PERIOD_OPEN}"
    )

    await handle_notes_period(callback, state, db)  # type: ignore[arg-type]

    assert await state.get_state() is None


async def test_period_buttons_from_the_keyboard_all_work(
    db: aiosqlite.Connection, state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Кнопки под списком должны попадать ровно в те периоды, которые умеет хендлер."""
    monkeypatch.setattr(clock, "today", lambda *args, **kwargs: WED_ODD)

    for row in keyboards.notes_periods().inline_keyboard:
        for button in row:
            callback = FakeCallback(data=button.callback_data)

            await handle_notes_period(callback, state, db)  # type: ignore[arg-type]

            assert callback.message is not None
            assert callback.message.answers, button.callback_data
            assert callback.answered == [(None, False)], button.callback_data


async def test_unknown_period_button_is_handled_politely(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    callback = FakeCallback(data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_PERIOD}:вчера")

    await handle_notes_period(callback, state, db)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]
    assert callback.message is not None
    assert callback.message.answers == []


async def test_long_list_is_truncated_with_a_warning(db: aiosqlite.Connection) -> None:
    """Список длиннее MAX_NOTES_SHOWN обрезаем сверху, но говорим об этом вслух."""
    total = MAX_NOTES_SHOWN + 3
    for index in range(total):
        await add_note(db, text=f"заметка номер {index}")
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]

    assert texts.NOTES_TRUNCATED.format(shown=MAX_NOTES_SHOWN, total=total) in message.answers
    assert "заметка номер 0" not in message.all_text
    assert f"заметка номер {total - 1}" in message.all_text


async def test_notes_command_drops_a_stuck_entry_dialog(
    db: aiosqlite.Connection, state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clock, "today", lambda *args, **kwargs: WED_ODD)
    await state.set_state(NoteEntry.text)

    await handle_notes(FakeMessage(text="/notes"), state, db)  # type: ignore[arg-type]

    assert await state.get_state() is None


# ======================================================================================
# 5. «Сделал» и «Не сделал»
# ======================================================================================


async def test_done_button_closes_the_note(db: aiosqlite.Connection) -> None:
    note = await add_note(db, text="дочитать главу 3")
    callback = FakeCallback(data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:{note.id}")

    await handle_note_done(callback, db)  # type: ignore[arg-type]

    closed = await db_module.get_note(db, note.id)
    assert closed is not None
    assert closed.is_open is False
    assert closed.closed_at is not None
    assert callback.answered == [(texts.NOTE_DONE_TOAST, False)]
    assert callback.message is not None
    assert callback.message.last_answer == texts.NOTE_DONE_REPLY


async def test_closed_note_disappears_from_the_open_list(
    db: aiosqlite.Connection,
) -> None:
    """Полный цикл списка: было в «Незакрытых» → нажали «Сделал» → больше нет."""
    note = await add_note(db, text="дочитать главу 3")
    before = FakeMessage()
    await send_notes_list(before, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]
    assert "дочитать главу 3" in before.all_text

    await handle_note_done(  # type: ignore[arg-type]
        FakeCallback(data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:{note.id}"), db
    )

    after = FakeMessage()
    await send_notes_list(after, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]
    assert "дочитать главу 3" not in after.all_text
    assert after.last_answer == texts.NOTES_EMPTY["open"]


async def test_done_pressed_twice_does_not_break_anything(
    db: aiosqlite.Connection,
) -> None:
    """Кнопка есть и в списке, и в напоминании — второе нажатие не ошибка."""
    note = await add_note(db)
    data = f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:{note.id}"

    await handle_note_done(FakeCallback(data=data), db)  # type: ignore[arg-type]
    second = FakeCallback(data=data)
    await handle_note_done(second, db)  # type: ignore[arg-type]

    assert second.answered == [(texts.NOTE_ALREADY_DONE, False)]
    assert second.message is not None
    assert second.message.answers == [], "Второй раз поздравлять не с чем"


async def test_done_button_cannot_close_someone_elses_note(
    db: aiosqlite.Connection,
) -> None:
    """id заметки лежит прямо в кнопке — чужую закрыть ею быть не должно возможности."""
    note = await add_note(db, telegram_id=OTHER_USER_ID, text="чужая домашка")
    callback = FakeCallback(
        data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:{note.id}",
        from_user=FakeUser(id=USER_ID),
    )

    await handle_note_done(callback, db)  # type: ignore[arg-type]

    still_open = await db_module.get_note(db, note.id)
    assert still_open is not None and still_open.is_open is True
    assert callback.answered == [(texts.NOTE_NOT_FOUND, True)]


async def test_close_note_directly_checks_the_owner(db: aiosqlite.Connection) -> None:
    """Та же защита на уровне базы, без хендлера."""
    note = await add_note(db, telegram_id=OTHER_USER_ID)

    assert await db_module.close_note(db, note.id, USER_ID) is None

    still_open = await db_module.get_note(db, note.id)
    assert still_open is not None and still_open.is_open is True
    assert still_open.closed_at is None

    # Хозяину — можно.
    mine = await db_module.close_note(db, note.id, OTHER_USER_ID)
    assert mine is not None and mine.is_open is False


async def test_done_button_with_broken_data_is_handled_politely(
    db: aiosqlite.Connection,
) -> None:
    callback = FakeCallback(data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:не-число")

    await handle_note_done(callback, db)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]


async def test_done_button_for_a_missing_note(db: aiosqlite.Connection) -> None:
    callback = FakeCallback(data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_DONE}:999")

    await handle_note_done(callback, db)  # type: ignore[arg-type]

    assert callback.answered == [(texts.NOTE_NOT_FOUND, True)]


async def test_not_done_keeps_the_note_open(db: aiosqlite.Connection) -> None:
    note = await add_note(db)
    callback = FakeCallback(
        data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_NOT_DONE}:{note.id}"
    )

    await handle_note_not_done(callback)  # type: ignore[arg-type]

    still_open = await db_module.get_note(db, note.id)
    assert still_open is not None and still_open.is_open is True
    assert callback.message is not None
    assert callback.message.last_answer == texts.NOTE_NOT_DONE_REPLY


async def test_not_done_promises_the_morning_repeat(db: aiosqlite.Connection) -> None:
    """Правило этапа: «Не сделал» → мягкое подталкивание, напоминание повторится утром."""
    note = await add_note(db)
    callback = FakeCallback(
        data=f"{keyboards.CB_NOTES}:{keyboards.NOTE_NOT_DONE}:{note.id}"
    )

    await handle_note_not_done(callback)  # type: ignore[arg-type]

    assert callback.message is not None
    assert "утром" in callback.message.last_answer.lower()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(texts.NOTE_NOT_DONE_REPLY, id="ответ на «Не сделал»"),
        pytest.param(texts.NOTE_REMINDER_DAY_BEFORE, id="напоминание за сутки"),
        pytest.param(texts.NOTE_REMINDER_MORNING, id="утреннее напоминание"),
        pytest.param(texts.NOTES_PROMPT, id="вопрос после пары"),
    ],
)
def test_reminders_have_no_reproach(reply: str) -> None:
    """Тон без давления и морализаторства — это требование, а не пожелание.

    Восклицательный знак сам по себе не укоризна («Доброе утро!» — это приветствие),
    поэтому ищем именно слова, которыми принято подгонять и стыдить.
    """
    lowered = reply.lower()
    for word in (
        "опять",
        "снова",
        "уже давно",
        "надо было",
        "должен",
        "почему ты",
        "срочно",
        "не тяни",
        "успей",
        "последний раз",
    ):
        assert word not in lowered, f"В тексте есть укоризна: «{word}»"


# ======================================================================================
# 6. Пользовательский текст и HTML
# ======================================================================================


async def test_note_text_is_escaped_in_the_list(db: aiosqlite.Connection) -> None:
    """Сообщения уходят с parse_mode=HTML: «<3» в домашке уронило бы весь список."""
    await add_note(db, text="сверстать <div> & сравнить 3<5")
    message = FakeMessage()

    await send_notes_list(message, db, USER_ID, keyboards.PERIOD_OPEN, WED_ODD)  # type: ignore[arg-type]

    body = message.all_text
    assert "&lt;div&gt;" in body
    assert "&amp;" in body
    assert "3&lt;5" in body
    assert "<div>" not in body


async def test_note_text_is_escaped_in_the_reminder(db: aiosqlite.Connection) -> None:
    note = await add_note(db, text="сверстать <div> & сравнить 3<5")

    text = views.format_note_reminder(note, lesson_by_id(ODD_TUE), morning=False)

    assert "&lt;div&gt;" in text
    assert "&amp;" in text
    assert "<div>" not in text


async def test_saved_note_keeps_the_original_text_in_the_database(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Экранирование — только на выводе: в базе лежит ровно то, что написал человек."""
    await handle_note_has(FakeCallback(data=cb_data(keyboards.NOTE_HAS)), state)  # type: ignore[arg-type]

    await handle_note_text(FakeMessage(text="сравнить 3<5"), state, db)  # type: ignore[arg-type]

    note = (await db_module.list_notes(db))[0]
    assert note.text == "сравнить 3<5"


async def test_note_item_shows_lesson_time_and_due_date(
    db: aiosqlite.Connection,
) -> None:
    note = await add_note(db, text="дочитать главу 3")

    item = views.format_note_item(note, lesson_by_id(ODD_TUE))

    assert "1 сентября" in item
    assert "11:40–13:05" in item
    assert "Безопасность жизнедеятельности" in item
    assert "15 сентября" in item


async def test_note_item_survives_a_lesson_that_left_the_schedule(
    db: aiosqlite.Connection,
) -> None:
    note = await add_note(db, lesson_id="L9999", due_date=None, text="старая домашка")

    item = views.format_note_item(note, lesson_by_id("L9999"))

    assert texts.NOTE_UNKNOWN_LESSON in item
    assert texts.NOTE_ITEM_DUE_NONE in item
    assert "старая домашка" in item
