"""Диалог знакомства: геопозиция → транспорт → сборы → запас.

Тот же сценарий переиспользуется в /settings, только на один шаг: режим лежит в данных
FSM под ключом `mode`. После шага в режиме «settings» диалог заканчивается и снова
показывает карточку настроек, в режиме «onboarding» — идёт к следующему вопросу.

Сами хендлеры тонкие: разобрать ответ, отдать значение в `app.users`, отправить текст.
Вся проверка чисел — в `app.users.parse_minutes`, её можно тестировать без Telegram.
"""

from __future__ import annotations

import logging

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import keyboards, texts, users, views
from app.handlers import common

logger = logging.getLogger(__name__)

router = Router(name="onboarding")

MODE_KEY = "mode"
MODE_ONBOARDING = "onboarding"
MODE_SETTINGS = "settings"


class Onboarding(StatesGroup):
    """Шаги диалога. Одни и те же состояния используются и при правке настроек."""

    location = State()
    transport = State()
    prep = State()
    buffer = State()


_STATE_BY_STEP = {
    keyboards.STEP_LOCATION: Onboarding.location,
    keyboards.STEP_TRANSPORT: Onboarding.transport,
    keyboards.STEP_PREP: Onboarding.prep,
    keyboards.STEP_BUFFER: Onboarding.buffer,
}

# Порядок вопросов при первом знакомстве. None — значит дальше финал.
_NEXT_STEP: dict[str, str | None] = {
    keyboards.STEP_LOCATION: keyboards.STEP_TRANSPORT,
    keyboards.STEP_TRANSPORT: keyboards.STEP_PREP,
    keyboards.STEP_PREP: keyboards.STEP_BUFFER,
    keyboards.STEP_BUFFER: None,
}


# --- Вопросы ------------------------------------------------------------------------


# Формулировка зависит от режима: при знакомстве вопросы пронумерованы («первый», «второй»),
# при правке одной настройки нумерация была бы враньём.
_PROMPTS: dict[str, tuple[str, str]] = {
    keyboards.STEP_LOCATION: (texts.ONBOARDING_INTRO, texts.SETTINGS_LOCATION_ASK),
    keyboards.STEP_TRANSPORT: (
        texts.ONBOARDING_TRANSPORT_ASK,
        texts.SETTINGS_TRANSPORT_ASK,
    ),
    keyboards.STEP_PREP: (texts.ONBOARDING_PREP_ASK, texts.SETTINGS_PREP_ASK),
    keyboards.STEP_BUFFER: (texts.ONBOARDING_BUFFER_ASK, texts.SETTINGS_BUFFER_ASK),
}

_MARKUPS = {
    keyboards.STEP_LOCATION: keyboards.request_location,
    keyboards.STEP_TRANSPORT: keyboards.transport_choice,
    keyboards.STEP_PREP: keyboards.prep_choice,
    keyboards.STEP_BUFFER: keyboards.buffer_choice,
}


async def ask_step(message: Message, state: FSMContext, step: str) -> None:
    """Задаёт вопрос нужного шага и переводит FSM в соответствующее состояние."""
    if step not in _STATE_BY_STEP:
        raise ValueError(f"Неизвестный шаг диалога: {step!r}")

    data = await state.get_data()
    single = data.get(MODE_KEY) == MODE_SETTINGS
    await state.set_state(_STATE_BY_STEP[step])

    prompt = _PROMPTS[step][1 if single else 0]
    # Кнопка «Оставить как есть» нужна только при правке одной настройки: при первом
    # знакомстве отменить весь диалог можно командой /cancel, а обрывать один вопрос
    # кнопкой некуда — дальше всё равно идти некуда без ответа.
    await message.answer(prompt, reply_markup=_MARKUPS[step](with_cancel=single))


async def start_onboarding(message: Message, state: FSMContext) -> None:
    """Запускает знакомство с первого вопроса."""
    await state.clear()
    await state.update_data(**{MODE_KEY: MODE_ONBOARDING})
    await ask_step(message, state, keyboards.STEP_LOCATION)


async def start_single_step(message: Message, state: FSMContext, step: str) -> None:
    """Запускает тот же диалог ради одного шага — для /settings."""
    await state.clear()
    await state.update_data(**{MODE_KEY: MODE_SETTINGS})
    await ask_step(message, state, step)


# --- Переход к следующему шагу ------------------------------------------------------


async def _finish_step(
    message: Message,
    state: FSMContext,
    db: aiosqlite.Connection,
    telegram_id: int,
    step: str,
) -> None:
    """Решает, что делать после ответа: следующий вопрос, финал или карточка настроек."""
    data = await state.get_data()
    if data.get(MODE_KEY) == MODE_SETTINGS:
        await state.clear()
        await common.send_settings(message, db, telegram_id)
        return

    next_step = _NEXT_STEP[step]
    if next_step is None:
        await _complete_onboarding(message, state, db, telegram_id)
        return
    await ask_step(message, state, next_step)


async def _complete_onboarding(
    message: Message,
    state: FSMContext,
    db: aiosqlite.Connection,
    telegram_id: int,
) -> None:
    await state.clear()
    user = await users.mark_onboarded(db, telegram_id)
    logger.info("Онбординг завершён: telegram_id=%s", telegram_id)
    await message.answer(
        texts.ONBOARDING_DONE.format(settings=views.format_settings(user))
    )


# --- /cancel и «Оставить как есть» ---------------------------------------------------


async def _abort_step(
    message: Message, state: FSMContext, db: aiosqlite.Connection, telegram_id: int
) -> None:
    """Прерывает текущий шаг.

    При правке одной настройки (/settings) это не «отмена» в духе всего диалога, а просто
    «передумал» — возвращаемся к карточке настроек без изменений. При первом знакомстве
    ведём себя как раньше: сохранённые ответы остаются, продолжить можно через /start.
    """
    data = await state.get_data()
    mode = data.get(MODE_KEY)
    await state.clear()
    if mode == MODE_SETTINGS:
        await message.answer(texts.SETTINGS_EDIT_CANCELLED, reply_markup=keyboards.remove_keyboard())
        await common.send_settings(message, db, telegram_id)
    else:
        await message.answer(
            texts.ONBOARDING_CANCELLED, reply_markup=keyboards.remove_keyboard()
        )


@router.message(Command("cancel"))
async def handle_cancel(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """Прерывает диалог на любом шаге. Уже сохранённые ответы остаются в базе."""
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL)
        return
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await state.clear()
        await message.answer(
            texts.ONBOARDING_CANCELLED, reply_markup=keyboards.remove_keyboard()
        )
        return
    await _abort_step(message, state, db, user_id)


@router.callback_query(F.data == keyboards.CB_SETTINGS_CANCEL)
async def handle_settings_edit_cancel(
    callback: CallbackQuery, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """Кнопка «Оставить как есть» под инлайн-клавиатурой правки настройки."""
    message = common.callback_message(callback)
    if message is None or await state.get_state() is None:
        await callback.answer()
        return
    await callback.answer()
    await common.hide_inline_keyboard(callback)
    await _abort_step(message, state, db, callback.from_user.id)


# --- Шаг 1: геопозиция --------------------------------------------------------------


@router.message(Onboarding.location, F.location)
async def handle_location(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    location = message.location
    user_id = message.from_user.id if message.from_user else None
    if location is None or user_id is None:
        return

    await users.save_location(db, user_id, location.latitude, location.longitude)
    await message.answer(
        texts.ONBOARDING_LOCATION_SAVED, reply_markup=keyboards.remove_keyboard()
    )
    await _finish_step(message, state, db, user_id, keyboards.STEP_LOCATION)


@router.message(Onboarding.location, F.text == texts.BTN_CANCEL_EDIT)
async def handle_location_cancel(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    """Кнопка «Оставить как есть» — есть только при правке через /settings."""
    data = await state.get_data()
    if data.get(MODE_KEY) != MODE_SETTINGS:
        # На первом знакомстве без геопозиции считать дорогу не от чего, кнопки тут нет —
        # если текст всё же пришёл (например, старая клавиатура), это просто «не то».
        await handle_location_retry(message)
        return
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await state.clear()
        return
    await _abort_step(message, state, db, user_id)


@router.message(Onboarding.location)
async def handle_location_retry(message: Message) -> None:
    """Пришло что угодно вместо точки — спокойно объясняем ещё раз."""
    await message.answer(
        texts.ONBOARDING_LOCATION_RETRY, reply_markup=keyboards.request_location()
    )


# --- Шаг 2: транспорт ---------------------------------------------------------------


@router.callback_query(Onboarding.transport, F.data)
async def handle_transport_choice(
    callback: CallbackQuery, state: FSMContext, db: aiosqlite.Connection
) -> None:
    value = keyboards.parse_callback_value(
        callback.data, keyboards.CB_ONBOARDING, keyboards.STEP_TRANSPORT
    )
    mode = users.normalize_transport(value)
    message = common.callback_message(callback)
    if mode is None or message is None:
        await callback.answer()
        return

    await users.save_transport_mode(db, callback.from_user.id, mode)
    await callback.answer()
    await common.hide_inline_keyboard(callback)
    await message.answer(
        texts.ONBOARDING_TRANSPORT_SAVED.format(transport=views.transport_phrase(mode))
    )
    await _finish_step(
        message, state, db, callback.from_user.id, keyboards.STEP_TRANSPORT
    )


@router.message(Onboarding.transport)
async def handle_transport_retry(message: Message) -> None:
    await message.answer(
        texts.ONBOARDING_TRANSPORT_RETRY, reply_markup=keyboards.transport_choice()
    )


# --- Шаги 3 и 4: минуты -------------------------------------------------------------


async def _save_minutes(
    message: Message,
    state: FSMContext,
    db: aiosqlite.Connection,
    telegram_id: int,
    step: str,
    minutes: int,
) -> None:
    """Общая часть для «сборов» и «запаса»: сохранить, подтвердить, шагнуть дальше."""
    if step == keyboards.STEP_PREP:
        await users.save_prep_minutes(db, telegram_id, minutes)
        template = texts.ONBOARDING_PREP_SAVED
    else:
        await users.save_buffer_minutes(db, telegram_id, minutes)
        template = texts.ONBOARDING_BUFFER_SAVED

    await message.answer(template.format(value=views.format_minutes(minutes)))
    await _finish_step(message, state, db, telegram_id, step)


@router.callback_query(Onboarding.prep, F.data)
async def handle_prep_choice(
    callback: CallbackQuery, state: FSMContext, db: aiosqlite.Connection
) -> None:
    raw = keyboards.parse_callback_value(
        callback.data, keyboards.CB_ONBOARDING, keyboards.STEP_PREP
    )
    minutes = users.parse_prep_minutes(raw)
    message = common.callback_message(callback)
    if minutes is None or message is None:
        await callback.answer()
        return

    await callback.answer()
    await common.hide_inline_keyboard(callback)
    await _save_minutes(
        message, state, db, callback.from_user.id, keyboards.STEP_PREP, minutes
    )


@router.message(Onboarding.prep)
async def handle_prep_text(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    user_id = message.from_user.id if message.from_user else None
    minutes = users.parse_prep_minutes(message.text)
    if minutes is None or user_id is None:
        await message.answer(
            texts.ONBOARDING_PREP_RETRY.format(
                minimum=users.PREP_MIN_MINUTES, maximum=users.PREP_MAX_MINUTES
            ),
            reply_markup=keyboards.prep_choice(),
        )
        return
    await _save_minutes(message, state, db, user_id, keyboards.STEP_PREP, minutes)


@router.callback_query(Onboarding.buffer, F.data)
async def handle_buffer_choice(
    callback: CallbackQuery, state: FSMContext, db: aiosqlite.Connection
) -> None:
    raw = keyboards.parse_callback_value(
        callback.data, keyboards.CB_ONBOARDING, keyboards.STEP_BUFFER
    )
    minutes = users.parse_buffer_minutes(raw)
    message = common.callback_message(callback)
    if minutes is None or message is None:
        await callback.answer()
        return

    await callback.answer()
    await common.hide_inline_keyboard(callback)
    await _save_minutes(
        message, state, db, callback.from_user.id, keyboards.STEP_BUFFER, minutes
    )


@router.message(Onboarding.buffer)
async def handle_buffer_text(
    message: Message, state: FSMContext, db: aiosqlite.Connection
) -> None:
    user_id = message.from_user.id if message.from_user else None
    minutes = users.parse_buffer_minutes(message.text)
    if minutes is None or user_id is None:
        await message.answer(
            texts.ONBOARDING_BUFFER_RETRY.format(
                minimum=users.BUFFER_MIN_MINUTES, maximum=users.BUFFER_MAX_MINUTES
            ),
            reply_markup=keyboards.buffer_choice(),
        )
        return
    await _save_minutes(message, state, db, user_id, keyboards.STEP_BUFFER, minutes)


__all__ = ["Onboarding", "ask_step", "router", "start_onboarding", "start_single_step"]
