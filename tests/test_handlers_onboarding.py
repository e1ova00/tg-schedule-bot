"""Тесты диалога знакомства: геопозиция → транспорт → сборы → запас.

Telegram не поднимается: сообщения и нажатия кнопок — заглушки из `tests/fakes.py`,
база временная (фикстура `db`), состояние диалога — MemoryStorage (фикстура `state`).
Хендлеры вызываются напрямую, в том же порядке, в каком их вызвал бы диспетчер.

Критерий приёмки этапа: после /start данные лежат в базе.
"""

from __future__ import annotations

import aiosqlite
import pytest
from aiogram.fsm.context import FSMContext

from app import keyboards, texts, users
from app.handlers.onboarding import (
    Onboarding,
    ask_step,
    handle_buffer_choice,
    handle_buffer_text,
    handle_cancel,
    handle_location,
    handle_location_retry,
    handle_prep_choice,
    handle_prep_text,
    handle_transport_choice,
    handle_transport_retry,
    start_onboarding,
)
from app.handlers.start import handle_start
from fakes import USER_ID, FakeCallback, FakeLocation, FakeMessage

HOME = FakeLocation(latitude=59.8500, longitude=30.2500)


async def begin_dialog(state: FSMContext, db: aiosqlite.Connection) -> FakeMessage:
    """Проходит /start и оставляет диалог на первом вопросе."""
    message = FakeMessage(text="/start")
    await handle_start(message, state, db)  # type: ignore[arg-type]
    return message


# ======================================================================================
# 1. Диалог целиком
# ======================================================================================


async def test_full_dialog_saves_everything_to_database(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Главный тест этапа: прошли четыре шага — все ответы лежат в базе."""
    await begin_dialog(state, db)

    await handle_location(FakeMessage(location=HOME), state, db)  # type: ignore[arg-type]
    await handle_transport_choice(  # type: ignore[arg-type]
        FakeCallback(data="onb:transport:public_transport"), state, db
    )
    await handle_prep_text(FakeMessage(text="35"), state, db)  # type: ignore[arg-type]
    final = FakeMessage(text="15")
    await handle_buffer_text(final, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None
    assert saved.coordinates == (HOME.latitude, HOME.longitude)
    assert saved.transport_mode == users.TRANSPORT_PUBLIC
    assert (saved.prep_minutes, saved.buffer_minutes) == (35, 15)
    assert users.is_onboarded(saved) is True
    # Диалог закончился: бот больше ничего не ждёт.
    assert await state.get_state() is None
    assert "Готово" in final.last_answer


async def test_final_message_repeats_all_answers(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """В итоговом сообщении человек должен увидеть, что именно бот запомнил."""
    await begin_dialog(state, db)
    await handle_location(FakeMessage(location=HOME), state, db)  # type: ignore[arg-type]
    await handle_transport_choice(FakeCallback(data="onb:transport:car"), state, db)  # type: ignore[arg-type]
    await handle_prep_text(FakeMessage(text="35"), state, db)  # type: ignore[arg-type]
    final = FakeMessage(text="15")

    await handle_buffer_text(final, state, db)  # type: ignore[arg-type]

    text = final.last_answer
    assert texts.LOCATION_SAVED_SHORT in text
    assert "На машине" in text
    assert "35 минут" in text
    assert "15 минут" in text
    # Про молчание по понедельникам обещано в CLAUDE.md — человека предупреждаем сразу.
    assert "онедельник" in text


async def test_dialog_asks_four_questions_in_order(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Порядок вопросов из PLAN.md: точка → транспорт → сборы → запас."""
    start = await begin_dialog(state, db)
    assert start.last_answer == texts.ONBOARDING_INTRO
    assert await state.get_state() == Onboarding.location.state

    after_location = FakeMessage(location=HOME)
    await handle_location(after_location, state, db)  # type: ignore[arg-type]
    assert after_location.last_answer == texts.ONBOARDING_TRANSPORT_ASK
    assert await state.get_state() == Onboarding.transport.state

    callback = FakeCallback(data="onb:transport:car")
    await handle_transport_choice(callback, state, db)  # type: ignore[arg-type]
    assert callback.message is not None
    assert callback.message.last_answer == texts.ONBOARDING_PREP_ASK
    assert await state.get_state() == Onboarding.prep.state

    after_prep = FakeMessage(text="30")
    await handle_prep_text(after_prep, state, db)  # type: ignore[arg-type]
    assert after_prep.last_answer == texts.ONBOARDING_BUFFER_ASK
    assert await state.get_state() == Onboarding.buffer.state


# ======================================================================================
# 2. Шаг 1: геопозиция
# ======================================================================================


async def test_location_step_saves_point(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await begin_dialog(state, db)
    message = FakeMessage(location=HOME)

    await handle_location(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None
    assert saved.coordinates == (HOME.latitude, HOME.longitude)
    assert texts.ONBOARDING_LOCATION_SAVED in message.all_text


async def test_location_question_offers_button_and_explains_why(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Просьба прислать координаты должна быть объяснена и не выглядеть требованием."""
    message = await begin_dialog(state, db)

    text = message.last_answer
    assert texts.BTN_SEND_LOCATION in text
    assert "скрепк" in text  # подсказка для Telegram на компьютере
    assert "/cancel" in text
    assert message.last_markup is not None


async def test_onboarding_keyboards_have_no_cancel_button(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Кнопка «Оставить как есть» — только для правки через /settings.

    При первом знакомстве обрывать один вопрос кнопкой некуда: дальше всё равно
    идти некуда без ответа, а весь диалог целиком можно бросить командой /cancel.
    """
    message = await begin_dialog(state, db)
    assert texts.BTN_CANCEL_EDIT not in str(message.last_markup)

    location_message = FakeMessage(location=HOME)
    await handle_location(location_message, state, db)  # type: ignore[arg-type]
    # Тот же объект: сначала «точку запомнил», следом — вопрос про транспорт.
    assert location_message.last_markup is not None
    assert texts.BTN_CANCEL_EDIT not in str(location_message.last_markup)


async def test_location_step_asks_again_for_text(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Прислали текст вместо точки — спокойно объясняем и остаёмся на том же шаге."""
    await begin_dialog(state, db)
    message = FakeMessage(text="я живу на Невском")

    await handle_location_retry(message)  # type: ignore[arg-type]

    assert message.answers == [texts.ONBOARDING_LOCATION_RETRY]
    assert message.last_markup is not None
    assert await state.get_state() == Onboarding.location.state
    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.has_location is False


async def test_location_step_ignores_message_without_author(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await begin_dialog(state, db)
    message = FakeMessage(location=HOME, from_user=None)

    await handle_location(message, state, db)  # type: ignore[arg-type]

    assert message.answers == []


# ======================================================================================
# 3. Шаг 2: транспорт
# ======================================================================================


@pytest.mark.parametrize(
    ("value", "expected", "phrase"),
    [
        pytest.param("car", users.TRANSPORT_CAR, "на машине", id="машина"),
        pytest.param(
            "public_transport",
            users.TRANSPORT_PUBLIC,
            "на общественном транспорте",
            id="общественный транспорт",
        ),
    ],
)
async def test_transport_choice_saves_mode(
    db: aiosqlite.Connection,
    state: FSMContext,
    value: str,
    expected: str,
    phrase: str,
) -> None:
    await begin_dialog(state, db)
    callback = FakeCallback(data=f"onb:transport:{value}")

    await handle_transport_choice(callback, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.transport_mode == expected
    assert callback.message is not None
    assert phrase in callback.message.all_text
    # Нажатие подтверждено и кнопки убраны, чтобы на них не нажали второй раз.
    assert callback.answered
    assert callback.message.edited_markups == 1


async def test_transport_buttons_match_handler(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Кнопки из keyboards и разбор в хендлере должны говорить на одном языке."""
    await begin_dialog(state, db)
    buttons = [row[0] for row in keyboards.transport_choice().inline_keyboard]

    for button in buttons:
        value = keyboards.parse_callback_value(
            button.callback_data, keyboards.CB_ONBOARDING, keyboards.STEP_TRANSPORT
        )
        assert users.normalize_transport(value) is not None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param("onb:transport:самокат", id="неизвестный транспорт"),
        pytest.param("onb:prep:20", id="кнопка от другого шага"),
        pytest.param("мусор", id="совсем не наш формат"),
        pytest.param(None, id="кнопка без данных"),
    ],
)
async def test_transport_ignores_foreign_callback(
    db: aiosqlite.Connection, state: FSMContext, data: str | None
) -> None:
    await begin_dialog(state, db)
    await state.set_state(Onboarding.transport)
    callback = FakeCallback(data=data)

    await handle_transport_choice(callback, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.transport_mode is None
    assert await state.get_state() == Onboarding.transport.state
    assert callback.answered  # нажатие всё равно подтверждаем, иначе кнопка «зависнет»


async def test_transport_retry_on_text(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await begin_dialog(state, db)
    message = FakeMessage(text="на метро")

    await handle_transport_retry(message)  # type: ignore[arg-type]

    assert message.answers == [texts.ONBOARDING_TRANSPORT_RETRY]
    assert message.last_markup is not None


# ======================================================================================
# 4. Шаги 3 и 4: минуты
# ======================================================================================


async def test_prep_accepts_custom_number(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Своё число текстом — основной сценарий: кнопок всего четыре."""
    await begin_dialog(state, db)
    await state.set_state(Onboarding.prep)
    message = FakeMessage(text="37 минут")

    await handle_prep_text(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.prep_minutes == 37
    assert "37 минут" in message.all_text


async def test_prep_accepts_button_value(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await begin_dialog(state, db)
    await state.set_state(Onboarding.prep)
    callback = FakeCallback(data="onb:prep:20")

    await handle_prep_choice(callback, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.prep_minutes == 20
    assert await state.get_state() == Onboarding.buffer.state


@pytest.mark.parametrize("raw", ["полчаса", "-5", "300", "", "🙂"])
async def test_prep_asks_again_for_broken_input(
    db: aiosqlite.Connection, state: FSMContext, raw: str
) -> None:
    """Непонятный ответ — переспрашиваем с диапазоном, шаг не меняется, в базу не пишем."""
    await begin_dialog(state, db)
    await state.set_state(Onboarding.prep)
    message = FakeMessage(text=raw)

    await handle_prep_text(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.prep_minutes is None
    assert await state.get_state() == Onboarding.prep.state
    assert str(users.PREP_MAX_MINUTES) in message.last_answer
    assert message.last_markup is not None


@pytest.mark.parametrize("raw", ["чуть-чуть", "90", "-1"])
async def test_buffer_asks_again_for_broken_input(
    db: aiosqlite.Connection, state: FSMContext, raw: str
) -> None:
    """У запаса диапазон уже: 90 минут — это ошибка, а для сборов было бы нормой."""
    await begin_dialog(state, db)
    await state.set_state(Onboarding.buffer)
    message = FakeMessage(text=raw)

    await handle_buffer_text(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.buffer_minutes is None
    assert await state.get_state() == Onboarding.buffer.state
    assert str(users.BUFFER_MAX_MINUTES) in message.last_answer


async def test_buffer_button_finishes_onboarding(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await begin_dialog(state, db)
    await handle_location(FakeMessage(location=HOME), state, db)  # type: ignore[arg-type]
    await handle_transport_choice(FakeCallback(data="onb:transport:car"), state, db)  # type: ignore[arg-type]
    await handle_prep_text(FakeMessage(text="30"), state, db)  # type: ignore[arg-type]
    callback = FakeCallback(data="onb:buffer:10")

    await handle_buffer_choice(callback, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.buffer_minutes == 10
    assert users.is_onboarded(saved) is True
    assert await state.get_state() is None


async def test_zero_minutes_is_a_valid_answer(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """«Собираюсь мгновенно» — законный ответ, а не ошибка ввода."""
    await begin_dialog(state, db)
    await state.set_state(Onboarding.prep)
    message = FakeMessage(text="0")

    await handle_prep_text(message, state, db)  # type: ignore[arg-type]

    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.prep_minutes == 0
    assert await state.get_state() == Onboarding.buffer.state


# ======================================================================================
# 5. /cancel и служебное
# ======================================================================================


async def test_cancel_stops_dialog_but_keeps_answers(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    await begin_dialog(state, db)
    await handle_location(FakeMessage(location=HOME), state, db)  # type: ignore[arg-type]
    message = FakeMessage(text="/cancel")

    await handle_cancel(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.ONBOARDING_CANCELLED]
    assert await state.get_state() is None
    saved = await users.get_user(db, USER_ID)
    assert saved is not None and saved.has_location is True
    assert users.is_onboarded(saved) is False


async def test_cancel_outside_dialog_is_gentle(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    message = FakeMessage(text="/cancel")

    await handle_cancel(message, state, db)  # type: ignore[arg-type]

    assert message.answers == [texts.NOTHING_TO_CANCEL]


async def test_cancelled_dialog_can_be_continued(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """После /cancel человек пишет /start и продолжает с первого вопроса."""
    await begin_dialog(state, db)
    await handle_cancel(FakeMessage(text="/cancel"), state, db)  # type: ignore[arg-type]

    message = await begin_dialog(state, db)

    assert message.answers[-1] == texts.ONBOARDING_INTRO
    assert await state.get_state() == Onboarding.location.state


async def test_ask_step_rejects_unknown_step(state: FSMContext) -> None:
    """Опечатка в имени шага должна ломаться громко и сразу, а не тихо в проде."""
    with pytest.raises(ValueError):
        await ask_step(FakeMessage(), state, "погода")  # type: ignore[arg-type]


async def test_start_onboarding_can_be_called_twice(
    db: aiosqlite.Connection, state: FSMContext
) -> None:
    """Повторный запуск диалога начинает с чистого листа, а не путается в старых данных."""
    message = FakeMessage()
    await start_onboarding(message, state)
    await state.set_state(Onboarding.buffer)

    await start_onboarding(message, state)

    assert await state.get_state() == Onboarding.location.state
    assert (await state.get_data()).get("mode") == "onboarding"
