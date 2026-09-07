"""Тесты команды /teachernote: заметки про преподавателей.

Telegram не поднимается: сообщение и нажатие — заглушки из `tests/fakes.py`,
база временная. Список преподавателей берётся из настоящего
`data/schedule_4md4.json` — выдумывать фамилии нельзя.

Два места, где легче всего ошибиться и где проверка самая подробная:

* **выбор только кнопкой.** В callback_data уходит индекс в `schedule.teachers()`.
  Если индекс устарел или не тот, заметка не должна молча уехать к другому человеку;
* **свой /cancel.** У команды своё состояние, и отменять она должна именно его,
  не задевая ни онбординг, ни ввод заметки к паре.
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

from app import db as db_module, keyboards, texts, views
from app.handlers.notes import NoteEntry
from app.handlers.onboarding import Onboarding
from app.handlers.teacher_notes import (
    MAX_TEACHER_NOTES_SHOWN,
    TEACHER_KEY,
    TeacherNoteEntry,
    ask_teacher,
    handle_teacher_note_add,
    handle_teacher_note_cancel,
    handle_teacher_note_list,
    handle_teacher_note_text,
    handle_teacher_notes,
    handle_teacher_pick,
    send_teacher_notes,
)
from app.schedule import ScheduleError, clear_cache, teachers
from fakes import USER_ID, FakeCallback, FakeMessage, FakeUser

OTHER_USER_ID = 777

ZVEREV = "Зверев В.В."


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


def pick(index: int) -> str:
    return f"{keyboards.CB_TEACHER_NOTES}:{keyboards.TEACHER_PICK}:{index}"


ADD = f"{keyboards.CB_TEACHER_NOTES}:{keyboards.TEACHER_ADD}"
LIST = f"{keyboards.CB_TEACHER_NOTES}:{keyboards.TEACHER_LIST}"


def button_texts(markup: Any) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.text for row in markup.inline_keyboard for button in row]


def button_data(markup: Any) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.callback_data or "" for row in markup.inline_keyboard for button in row]


async def add_teacher_note(
    db: aiosqlite.Connection,
    text: str,
    *,
    teacher: str = ZVEREV,
    telegram_id: int = USER_ID,
) -> db_module.TeacherNote:
    return await db_module.create_teacher_note(db, telegram_id, teacher, text)


# ======================================================================================
# 1. Меню команды
# ======================================================================================


async def test_command_shows_a_menu_of_two_actions(state: FSMContext) -> None:
    message = FakeMessage(text="/teachernote")

    await handle_teacher_notes(message, state)  # type: ignore[arg-type]

    assert message.answers == [texts.TEACHER_NOTES_MENU]
    assert button_texts(message.last_markup) == [
        texts.BTN_TEACHER_NOTE_ADD,
        texts.BTN_TEACHER_NOTE_LIST,
    ]
    assert button_data(message.last_markup) == [ADD, LIST]


async def test_menu_explains_that_there_are_no_reminders(state: FSMContext) -> None:
    """Это справка, а не задача: человек должен понимать, что напоминаний не будет."""
    message = FakeMessage(text="/teachernote")

    await handle_teacher_notes(message, state)  # type: ignore[arg-type]

    assert "апоминани" in message.answers[0]


async def test_command_drops_a_stuck_dialog(state: FSMContext) -> None:
    """Команда посреди недописанной заметки прерывает диалог, а не продолжает его."""
    await state.set_state(TeacherNoteEntry.text)
    await state.update_data(**{TEACHER_KEY: ZVEREV})

    await handle_teacher_notes(FakeMessage(text="/teachernote"), state)  # type: ignore[arg-type]

    assert await state.get_state() is None


# ======================================================================================
# 2. «Добавить заметку» → список преподавателей
# ======================================================================================


async def test_add_button_offers_all_ten_teachers(state: FSMContext) -> None:
    callback = FakeCallback(data=ADD)

    await handle_teacher_note_add(callback, state)  # type: ignore[arg-type]

    assert callback.message is not None
    assert callback.message.last_answer == texts.TEACHER_NOTES_PICK
    names = button_texts(callback.message.last_markup)
    assert names == list(teachers())
    assert len(names) == 10


async def test_teacher_buttons_carry_their_own_index(state: FSMContext) -> None:
    """Подпись кнопки и индекс в callback_data должны указывать на одного человека."""
    callback = FakeCallback(data=ADD)

    await handle_teacher_note_add(callback, state)  # type: ignore[arg-type]

    assert callback.message is not None
    markup = callback.message.last_markup
    names = teachers()
    for text, data in zip(button_texts(markup), button_data(markup), strict=True):
        index = int(data.rsplit(":", 1)[1])
        assert names[index] == text


async def test_add_button_does_not_set_the_text_state_yet(state: FSMContext) -> None:
    """Ждать текст, пока не выбран человек, нельзя — его будет некуда привязать."""
    await handle_teacher_note_add(FakeCallback(data=ADD), state)  # type: ignore[arg-type]

    assert await state.get_state() is None


async def test_no_teachers_in_schedule_is_explained(
    state: FSMContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Расписание не читается — команда не должна падать и не должна молчать."""

    def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr("app.handlers.teacher_notes.teachers", boom)
    message = FakeMessage()

    await ask_teacher(message)  # type: ignore[arg-type]

    assert message.answers == [texts.TEACHER_NOTES_NO_TEACHERS]
    assert message.markups == [None]


# ======================================================================================
# 3. Выбор преподавателя кнопкой
# ======================================================================================


@pytest.mark.parametrize("index", range(10))
async def test_every_button_picks_its_own_teacher(
    state: FSMContext, index: int
) -> None:
    """Каждый из десяти индексов ведёт ровно к своему человеку, без смещений."""
    callback = FakeCallback(data=pick(index))

    await handle_teacher_pick(callback, state)  # type: ignore[arg-type]

    data = await state.get_data()
    assert data[TEACHER_KEY] == teachers()[index]
    assert await state.get_state() == TeacherNoteEntry.text.state


async def test_pick_asks_for_the_text_and_names_the_teacher(state: FSMContext) -> None:
    callback = FakeCallback(data=pick(3))

    await handle_teacher_pick(callback, state)  # type: ignore[arg-type]

    assert callback.message is not None
    assert teachers()[3] in callback.message.last_answer
    assert "/cancel" in callback.message.last_answer
    assert callback.message.edited_markups == 1, "кнопки выбора надо убрать"
    assert callback.answered == [(None, False)]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(pick(10), id="индекс за пределами списка"),
        pytest.param(pick(99), id="индекс из старого расписания"),
        pytest.param(pick(-1), id="отрицательный индекс"),
        pytest.param(pick("абв"), id="не число"),
        pytest.param(pick(""), id="пустое значение"),
    ],
)
async def test_stale_index_does_not_pick_anyone(
    db: aiosqlite.Connection, state: FSMContext, data: str
) -> None:
    """Список мог измениться после обновления расписания — молча писать не тому нельзя."""
    callback = FakeCallback(data=data)

    await handle_teacher_pick(callback, state)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]
    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert callback.message is not None
    assert callback.message.answers == []
    assert await db_module.list_teacher_notes(db) == []


async def test_stale_index_after_a_valid_one_does_not_overwrite_the_choice(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Нажали правильную кнопку, потом старую — заметка должна уйти первому человеку."""
    await handle_teacher_pick(FakeCallback(data=pick(3)), state)  # type: ignore[arg-type]
    await handle_teacher_pick(FakeCallback(data=pick(42)), state)  # type: ignore[arg-type]

    await handle_teacher_note_text(FakeMessage(text="сдавать в pdf"), state, db)  # type: ignore[arg-type]

    saved = await db_module.list_teacher_notes(db, USER_ID)
    assert [note.teacher for note in saved] == [teachers()[3]]


async def test_pick_under_an_inaccessible_message_is_polite(state: FSMContext) -> None:
    callback = FakeCallback(data=pick(0), message=None)

    await handle_teacher_pick(callback, state)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]
    assert await state.get_state() is None


# ======================================================================================
# 4. Текст заметки
# ======================================================================================


async def test_full_cycle_saves_the_note_with_the_right_teacher(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Полный сценарий: меню → «Добавить» → кнопка → текст → запись в базе."""
    menu = FakeMessage(text="/teachernote")
    await handle_teacher_notes(menu, state)  # type: ignore[arg-type]

    add = FakeCallback(data=ADD)
    await handle_teacher_note_add(add, state)  # type: ignore[arg-type]
    assert add.message is not None
    index = button_texts(add.message.last_markup).index(ZVEREV)

    await handle_teacher_pick(FakeCallback(data=pick(index)), state)  # type: ignore[arg-type]

    answer = FakeMessage(text="принимает работы только на почту")
    await handle_teacher_note_text(answer, state, db)  # type: ignore[arg-type]

    saved = await db_module.list_teacher_notes(db, USER_ID)
    assert len(saved) == 1
    assert saved[0].teacher == ZVEREV
    assert saved[0].text == "принимает работы только на почту"
    assert saved[0].telegram_id == USER_ID
    assert ZVEREV in answer.last_answer
    assert await state.get_state() is None, "диалог закончен"


async def test_saved_text_keeps_the_original_characters(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await handle_teacher_pick(FakeCallback(data=pick(0)), state)  # type: ignore[arg-type]

    await handle_teacher_note_text(FakeMessage(text="сдавать <pdf> & docx"), state, db)  # type: ignore[arg-type]

    note = (await db_module.list_teacher_notes(db, USER_ID))[0]
    assert note.text == "сдавать <pdf> & docx"


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
    await handle_teacher_pick(FakeCallback(data=pick(0)), state)  # type: ignore[arg-type]

    answer = FakeMessage(text=text)
    await handle_teacher_note_text(answer, state, db)  # type: ignore[arg-type]

    assert await db_module.list_teacher_notes(db) == []
    assert answer.last_answer == texts.TEACHER_NOTES_TEXT_RETRY
    assert await state.get_state() == TeacherNoteEntry.text.state, "ждём текст дальше"


async def test_broken_state_does_not_create_a_note(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Бот обновился посреди диалога — не молчим и мусор не сохраняем."""
    await state.set_state(TeacherNoteEntry.text)

    answer = FakeMessage(text="что-то важное")
    await handle_teacher_note_text(answer, state, db)  # type: ignore[arg-type]

    assert await db_module.list_teacher_notes(db) == []
    assert answer.last_answer == texts.STALE_BUTTON
    assert await state.get_state() is None


async def test_notes_of_two_users_do_not_mix(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await add_teacher_note(db, "чужая заметка", telegram_id=OTHER_USER_ID)
    await handle_teacher_pick(FakeCallback(data=pick(0)), state)  # type: ignore[arg-type]

    await handle_teacher_note_text(FakeMessage(text="моя заметка"), state, db)  # type: ignore[arg-type]

    mine = await db_module.list_teacher_notes(db, USER_ID)
    assert [note.text for note in mine] == ["моя заметка"]


# ======================================================================================
# 5. «Посмотреть все»
# ======================================================================================


async def test_empty_list_is_explained_politely(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    callback = FakeCallback(data=LIST)

    await handle_teacher_note_list(callback, state, db)  # type: ignore[arg-type]

    assert callback.message is not None
    assert callback.message.answers == [texts.TEACHER_NOTES_EMPTY]
    assert "/teachernote" in callback.message.answers[0]


async def test_list_shows_all_notes_grouped_by_teacher(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await add_teacher_note(db, "принимает только на почту", teacher=ZVEREV)
    await add_teacher_note(db, "любит вопросы по лекциям", teacher=ZVEREV)
    await add_teacher_note(db, "просит отчёт в pdf", teacher="Агеева Е.А.")
    callback = FakeCallback(data=LIST)

    await handle_teacher_note_list(callback, state, db)  # type: ignore[arg-type]

    assert callback.message is not None
    body = callback.message.all_text
    assert texts.TEACHER_NOTES_TITLE in callback.message.answers
    for fragment in (
        "принимает только на почту",
        "любит вопросы по лекциям",
        "просит отчёт в pdf",
        ZVEREV,
        "Агеева Е.А.",
    ):
        assert fragment in body


async def test_list_escapes_html_in_the_note_text(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Сообщения уходят с parse_mode=HTML: «<b>» в заметке уронило бы весь список."""
    await add_teacher_note(db, "требует <b>жирный</b> заголовок & подпись")
    callback = FakeCallback(data=LIST)

    await handle_teacher_note_list(callback, state, db)  # type: ignore[arg-type]

    assert callback.message is not None
    body = callback.message.all_text
    assert "&lt;b&gt;жирный&lt;/b&gt;" in body
    assert "&amp;" in body
    assert "<b>жирный" not in body


async def test_list_shows_only_my_notes(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await add_teacher_note(db, "чужая заметка", telegram_id=OTHER_USER_ID)
    callback = FakeCallback(data=LIST, from_user=FakeUser(id=USER_ID))

    await handle_teacher_note_list(callback, state, db)  # type: ignore[arg-type]

    assert callback.message is not None
    assert callback.message.answers == [texts.TEACHER_NOTES_EMPTY]


async def test_long_list_is_truncated_with_a_warning(
    db: aiosqlite.Connection,
) -> None:
    """Сообщение в Telegram не бесконечное: показываем свежие и говорим об этом вслух."""
    total = MAX_TEACHER_NOTES_SHOWN + 3
    for index in range(total):
        await add_teacher_note(db, f"заметка номер {index}")
    message = FakeMessage()

    await send_teacher_notes(message, db, USER_ID)  # type: ignore[arg-type]

    assert (
        texts.TEACHER_NOTES_TRUNCATED.format(shown=MAX_TEACHER_NOTES_SHOWN, total=total)
        in message.answers
    )
    assert "заметка номер 0" not in message.all_text
    assert f"заметка номер {total - 1}" in message.all_text


async def test_list_matches_the_view_function(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Хендлер тонкий: текст списка собирает `views.format_teacher_notes`."""
    await add_teacher_note(db, "принимает только на почту")
    callback = FakeCallback(data=LIST)

    await handle_teacher_note_list(callback, state, db)  # type: ignore[arg-type]

    saved = await db_module.list_teacher_notes(db, USER_ID)
    assert callback.message is not None
    assert callback.message.last_answer == views.format_teacher_notes(saved)


async def test_list_button_clears_a_pending_entry(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Иначе следующее сообщение молча ушло бы текстом недописанной заметки."""
    await handle_teacher_pick(FakeCallback(data=pick(0)), state)  # type: ignore[arg-type]

    await handle_teacher_note_list(FakeCallback(data=LIST), state, db)  # type: ignore[arg-type]

    assert await state.get_state() is None


# ======================================================================================
# 6. /cancel — свой у каждой команды
# ======================================================================================


async def test_cancel_during_entry_saves_nothing(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await handle_teacher_pick(FakeCallback(data=pick(0)), state)  # type: ignore[arg-type]

    answer = FakeMessage(text="/cancel")
    await handle_teacher_note_cancel(answer, state)  # type: ignore[arg-type]

    assert await db_module.list_teacher_notes(db) == []
    assert answer.last_answer == texts.TEACHER_NOTES_CANCELLED
    assert await state.get_state() is None


def test_teacher_note_state_does_not_overlap_with_others() -> None:
    """Состояния разных диалогов не должны совпадать: иначе /cancel отменит чужое."""
    assert TeacherNoteEntry.text.state not in (
        NoteEntry.text.state,
        Onboarding.prep.state,
    )


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
    """Отдаёт сообщение настоящему корневому роутеру — как это делает диспетчер в бою."""
    message = Message(
        message_id=1,
        date=datetime(2026, 9, 1, 13, 20),
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


async def test_cancel_in_teacher_entry_belongs_to_teacher_notes(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Главная причина, по которой роутер /teachernote подключён раньше онбординга."""
    await routed_state.set_state(TeacherNoteEntry.text)
    await routed_state.update_data(**{TEACHER_KEY: ZVEREV})

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.TEACHER_NOTES_CANCELLED]
    assert texts.ONBOARDING_CANCELLED not in stub_bot.texts
    assert texts.NOTES_ENTRY_CANCELLED not in stub_bot.texts
    assert await routed_state.get_state() is None
    assert await db_module.list_teacher_notes(db) == []


async def test_cancel_during_onboarding_is_not_hijacked_by_teacher_notes(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Обратная сторона: вне своего состояния /teachernote чужую отмену не трогает."""
    await routed_state.set_state(Onboarding.prep)

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.ONBOARDING_CANCELLED]


async def test_cancel_in_lesson_note_entry_is_not_hijacked_either(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """И третья сторона: заметка к паре отменяется своим текстом."""
    await routed_state.set_state(NoteEntry.text)
    await routed_state.update_data(
        note_lesson_id="L1216", note_lesson_date=date(2026, 9, 1).isoformat()
    )

    await route_message(root_router, stub_bot, db, routed_state, "/cancel")

    assert stub_bot.texts == [texts.NOTES_ENTRY_CANCELLED]


async def test_plain_text_in_teacher_entry_becomes_a_teacher_note(
    root_router: Router,
    stub_bot: StubBot,
    db: aiosqlite.Connection,
    routed_state: FSMContext,
) -> None:
    """Обычный текст не должен утечь ни в онбординг, ни в fallback, ни в заметку к паре."""
    await routed_state.set_state(TeacherNoteEntry.text)
    await routed_state.update_data(**{TEACHER_KEY: ZVEREV})

    await route_message(root_router, stub_bot, db, routed_state, "принимает на почту")

    saved = await db_module.list_teacher_notes(db)
    assert len(saved) == 1
    assert saved[0].teacher == ZVEREV
    assert saved[0].text == "принимает на почту"
    assert await db_module.list_notes(db) == [], "в заметки к парам это попасть не должно"
    assert texts.UNKNOWN_COMMAND not in stub_bot.texts


def test_router_order_puts_teacher_notes_before_onboarding(root_router: Router) -> None:
    """Порядок роутеров — это и есть механизм, который проверяют тесты выше."""
    names = [child.name for child in root_router.sub_routers]

    assert (
        names.index("notes")
        < names.index("teacher-notes")
        < names.index("add-note")
        < names.index("onboarding")
        < names.index("fallback")
    )
