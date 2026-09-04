"""Тесты команды /settings: показать сохранённые ответы и поменять любой из них.

Вторая половина критерия приёмки этапа 3: «/settings их показывает и меняет».
Telegram и сеть не нужны — заглушки из `tests/fakes.py` и временная база.
"""

from __future__ import annotations

import aiosqlite
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from app import keyboards, texts, users
from app.handlers.onboarding import (
    Onboarding,
    handle_buffer_text,
    handle_location,
    handle_prep_text,
    handle_transport_choice,
)
from app.handlers.settings import handle_edit_request, handle_settings
from fakes import USER_ID, FakeCallback, FakeLocation, FakeMessage, make_onboarded_user


def edit_data(step: str) -> str:
    return f"{keyboards.CB_SETTINGS}:edit:{step}"


# ======================================================================================
# 1. Показ настроек
# ======================================================================================


async def test_settings_shows_saved_values(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await make_onboarded_user(
        db, transport_mode=users.TRANSPORT_CAR, prep_minutes=30, buffer_minutes=10
    )
    message = FakeMessage(text="/settings")

    await handle_settings(message, state, db)  # type: ignore[arg-type]

    card = message.last_answer
    assert texts.LOCATION_SAVED_SHORT in card
    assert "На машине" in card
    assert "30 минут" in card
    assert "10 минут" in card


async def test_settings_does_not_leak_raw_coordinates(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Про точку говорим «сохранена»: сырые координаты в чате никому не нужны."""
    await make_onboarded_user(db, latitude=59.8500, longitude=30.2500)
    message = FakeMessage(text="/settings")

    await handle_settings(message, state, db)  # type: ignore[arg-type]

    assert "59.85" not in message.last_answer
    assert "30.25" not in message.last_answer


async def test_settings_offers_button_for_every_answer(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await make_onboarded_user(db)
    message = FakeMessage(text="/settings")

    await handle_settings(message, state, db)  # type: ignore[arg-type]

    markup = message.last_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    steps = [row[0].callback_data for row in markup.inline_keyboard]
    assert steps == [edit_data(step) for step in keyboards.EDITABLE_STEPS]


async def test_settings_before_onboarding_sends_people_to_start(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Пока половина ответов пустая, менять нечего — зовём закончить знакомство."""
    await users.ensure_user(db, USER_ID, username="tester")
    message = FakeMessage(text="/settings")

    await handle_settings(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.SETTINGS_NOT_READY]
    assert "/start" in message.last_answer


async def test_settings_for_unknown_user_is_not_ready(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    message = FakeMessage(text="/settings")

    await handle_settings(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.SETTINGS_NOT_READY]


async def test_settings_drops_stuck_dialog(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """/settings посреди зависшего диалога начинает с чистого листа."""
    await make_onboarded_user(db)
    await state.set_state(Onboarding.prep)

    await handle_settings(FakeMessage(text="/settings"), state, db)  # type: ignore[arg-type]

    assert await state.get_state() is None


async def test_settings_ignores_message_without_author(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    message = FakeMessage(text="/settings", from_user=None)

    await handle_settings(message, state, db)  # type: ignore[arg-type]

    assert message.answers == []


# ======================================================================================
# 2. Кнопки «поменять …»
# ======================================================================================


@pytest.mark.parametrize(
    ("step", "expected_state"),
    [
        pytest.param(keyboards.STEP_LOCATION, Onboarding.location, id="точка"),
        pytest.param(keyboards.STEP_TRANSPORT, Onboarding.transport, id="транспорт"),
        pytest.param(keyboards.STEP_PREP, Onboarding.prep, id="сборы"),
        pytest.param(keyboards.STEP_BUFFER, Onboarding.buffer, id="запас"),
    ],
)
async def test_edit_button_asks_single_question(
    state: FSMContext, step: str, expected_state: object
) -> None:
    callback = FakeCallback(data=edit_data(step))

    await handle_edit_request(callback, state)  # type: ignore[arg-type]

    assert await state.get_state() == expected_state.state  # type: ignore[attr-defined]
    assert (await state.get_data()).get("mode") == "settings"
    assert callback.message is not None
    assert callback.message.answers  # вопрос задан
    assert callback.message.edited_markups == 1  # старые кнопки убраны


async def test_edit_question_is_not_numbered(state: FSMContext) -> None:
    """При правке одной настройки нумерация «третий вопрос» была бы враньём."""
    callback = FakeCallback(data=edit_data(keyboards.STEP_PREP))

    await handle_edit_request(callback, state)  # type: ignore[arg-type]

    assert callback.message is not None
    assert callback.message.last_answer == texts.SETTINGS_PREP_ASK
    assert "Третий вопрос" not in callback.message.last_answer


@pytest.mark.parametrize(
    "data",
    [
        pytest.param("set:edit:погода", id="шага не существует"),
        pytest.param("set:edit:", id="шаг не указан"),
        pytest.param("мусор", id="чужой формат"),
        pytest.param(None, id="кнопка без данных"),
    ],
)
async def test_edit_button_ignores_unknown_step(
    state: FSMContext, data: str | None
) -> None:
    callback = FakeCallback(data=data)

    await handle_edit_request(callback, state)  # type: ignore[arg-type]

    assert await state.get_state() is None
    assert callback.message is not None
    assert callback.message.answers == []
    assert callback.answered  # нажатие подтверждено, кнопка не «висит»


async def test_settings_menu_buttons_match_handler(state: FSMContext) -> None:
    """Кнопки из карточки должны попадать ровно в те шаги, которые умеет хендлер."""
    for row in keyboards.settings_menu().inline_keyboard:
        callback = FakeCallback(data=row[0].callback_data)

        await handle_edit_request(callback, state)  # type: ignore[arg-type]

        assert await state.get_state() is not None, row[0].callback_data


# ======================================================================================
# 3. Правка доходит до базы и до карточки
# ======================================================================================


async def test_changing_prep_updates_database_and_card(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Полный сценарий: /settings → «поменять сборы» → новое число → обновлённая карточка."""
    await make_onboarded_user(db, prep_minutes=30)
    await handle_settings(FakeMessage(text="/settings"), state, db)  # type: ignore[arg-type]
    callback = FakeCallback(data=edit_data(keyboards.STEP_PREP))
    await handle_edit_request(callback, state)  # type: ignore[arg-type]

    answer = FakeMessage(text="45")
    await handle_prep_text(answer, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.prep_minutes == 45
    assert "45 минут" in answer.all_text
    # Диалог закончился на одном вопросе: про запас снова не спрашиваем.
    assert texts.ONBOARDING_BUFFER_ASK not in answer.all_text
    assert await state.get_state() is None


async def test_changing_transport_updates_card(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await make_onboarded_user(db, transport_mode=users.TRANSPORT_CAR)
    edit = FakeCallback(data=edit_data(keyboards.STEP_TRANSPORT))
    await handle_edit_request(edit, state)  # type: ignore[arg-type]

    choice = FakeCallback(
        data="onb:transport:public_transport", message=edit.message
    )
    await handle_transport_choice(choice, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.transport_mode == users.TRANSPORT_PUBLIC
    assert choice.message is not None
    assert "На общественном транспорте" in choice.message.last_answer
    assert await state.get_state() is None


async def test_changing_location_updates_point(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await make_onboarded_user(db, latitude=59.85, longitude=30.25)
    edit = FakeCallback(data=edit_data(keyboards.STEP_LOCATION))
    await handle_edit_request(edit, state)  # type: ignore[arg-type]

    answer = FakeMessage(location=FakeLocation(60.0, 30.4))
    await handle_location(answer, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.coordinates == (60.0, 30.4)
    assert texts.LOCATION_SAVED_SHORT in answer.last_answer  # снова показана карточка
    assert await state.get_state() is None


async def test_changing_buffer_keeps_other_answers(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Правка одного поля не должна сбрасывать остальные и отметку о завершении."""
    await make_onboarded_user(db, prep_minutes=30, buffer_minutes=10)
    edit = FakeCallback(data=edit_data(keyboards.STEP_BUFFER))
    await handle_edit_request(edit, state)  # type: ignore[arg-type]

    await handle_buffer_text(FakeMessage(text="20"), state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None
    assert saved.buffer_minutes == 20
    assert saved.prep_minutes == 30
    assert saved.transport_mode == users.TRANSPORT_CAR
    assert users.is_onboarded(saved) is True


async def test_broken_answer_during_edit_keeps_old_value(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Ответили ерундой при правке — старое значение остаётся, бот переспрашивает."""
    await make_onboarded_user(db, prep_minutes=30)
    edit = FakeCallback(data=edit_data(keyboards.STEP_PREP))
    await handle_edit_request(edit, state)  # type: ignore[arg-type]

    answer = FakeMessage(text="как получится")
    await handle_prep_text(answer, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.prep_minutes == 30
    assert await state.get_state() == Onboarding.prep.state
