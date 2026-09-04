"""Тексты настроек и клавиатуры: `app/views.py` и `app/keyboards.py`.

Ни базы, ни Telegram: на вход — обычные значения, на выход — строки и разметка кнопок.
Проверяем то, что человек увидит в чате: «30 минут», а не «30 минута».
"""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app import keyboards, texts, users, views
from app.users import User

# ======================================================================================
# 1. Склонение минут
# ======================================================================================


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "минута"),
        (2, "минуты"),
        (3, "минуты"),
        (4, "минуты"),
        (5, "минут"),
        (10, "минут"),
        (11, "минут"),
        (12, "минут"),
        (13, "минут"),
        (14, "минут"),
        (15, "минут"),
        (20, "минут"),
        (21, "минута"),
        (22, "минуты"),
        (25, "минут"),
        (31, "минута"),
        (44, "минуты"),
        (100, "минут"),
        (101, "минута"),
        (111, "минут"),
        (0, "минут"),
    ],
)
def test_minutes_word(count: int, expected: str) -> None:
    assert views.minutes_word(count) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "1 минута"),
        (5, "5 минут"),
        (22, "22 минуты"),
        (45, "45 минут"),
    ],
)
def test_format_minutes(count: int, expected: str) -> None:
    assert views.format_minutes(count) == expected


def test_format_minutes_without_value() -> None:
    assert views.format_minutes(None) == texts.VALUE_MISSING


def test_format_minutes_explains_zero() -> None:
    """Ноль — валидный ответ, но стоит проговорить, что запаса не будет совсем."""
    assert views.format_minutes(0).startswith("0 минут")
    assert "запас" in views.format_minutes(0)


@pytest.mark.parametrize("value", list(users.PREP_OPTIONS) + list(users.BUFFER_OPTIONS))
def test_button_values_are_formatted_without_errors(value: int) -> None:
    text = views.format_minutes(value)

    assert str(value) in text
    assert "минут" in text


# ======================================================================================
# 2. Карточка настроек
# ======================================================================================


def full_user(**overrides: object) -> User:
    defaults: dict[str, object] = {
        "telegram_id": 42,
        "latitude": 59.9386,
        "longitude": 30.3141,
        "transport_mode": users.TRANSPORT_CAR,
        "prep_minutes": 30,
        "buffer_minutes": 10,
        "onboarded_at": "2026-09-04T10:15:00+03:00",
    }
    defaults.update(overrides)
    return User(**defaults)  # type: ignore[arg-type]


def test_settings_card_shows_all_four_answers() -> None:
    card = views.format_settings(full_user())

    assert texts.LOCATION_SAVED_SHORT in card
    assert "На машине" in card
    assert "30 минут" in card
    assert "10 минут" in card


def test_settings_card_marks_missing_answers() -> None:
    """Незаполненные поля видно сразу — человек понимает, что ещё спросят."""
    card = views.format_settings(User(telegram_id=42))

    assert card.count(texts.VALUE_MISSING) == 4


def test_settings_card_survives_missing_user() -> None:
    """Карточку могут запросить для того, кого ещё нет в базе, — падать нельзя."""
    card = views.format_settings(None)

    assert texts.VALUE_MISSING in card


def test_location_title_hides_coordinates() -> None:
    assert views.location_title(full_user()) == texts.LOCATION_SAVED_SHORT
    assert views.location_title(User(telegram_id=42)) == texts.VALUE_MISSING
    assert views.location_title(full_user(latitude=None)) == texts.VALUE_MISSING


@pytest.mark.parametrize(
    ("mode", "title", "phrase"),
    [
        (users.TRANSPORT_CAR, "На машине", "едешь на машине"),
        (
            users.TRANSPORT_PUBLIC,
            "На общественном транспорте",
            "едешь на общественном транспорте",
        ),
    ],
)
def test_transport_titles(mode: str, title: str, phrase: str) -> None:
    assert views.transport_title(mode) == title
    assert views.transport_phrase(mode) == phrase


@pytest.mark.parametrize("mode", [None, "", "самокат"])
def test_transport_title_for_unknown_mode(mode: str | None) -> None:
    assert views.transport_title(mode) == texts.VALUE_MISSING
    assert views.transport_phrase(mode) == texts.VALUE_MISSING


# ======================================================================================
# 3. Клавиатуры
# ======================================================================================


def test_location_keyboard_requests_geolocation() -> None:
    markup = keyboards.request_location()

    assert isinstance(markup, ReplyKeyboardMarkup)
    button = markup.keyboard[0][0]
    assert button.text == texts.BTN_SEND_LOCATION
    assert button.request_location is True


def test_remove_keyboard_is_removal() -> None:
    assert isinstance(keyboards.remove_keyboard(), ReplyKeyboardRemove)


def test_transport_keyboard_has_two_options() -> None:
    markup = keyboards.transport_choice()

    assert isinstance(markup, InlineKeyboardMarkup)
    values = [row[0].callback_data for row in markup.inline_keyboard]
    assert values == ["onb:transport:car", "onb:transport:public_transport"]


@pytest.mark.parametrize(
    ("build", "step", "options"),
    [
        (keyboards.prep_choice, keyboards.STEP_PREP, users.PREP_OPTIONS),
        (keyboards.buffer_choice, keyboards.STEP_BUFFER, users.BUFFER_OPTIONS),
    ],
)
def test_minutes_keyboards_match_options(build, step: str, options: tuple[int, ...]) -> None:
    row = build().inline_keyboard[0]

    assert [button.text for button in row] == [str(value) for value in options]
    assert [button.callback_data for button in row] == [
        f"onb:{step}:{value}" for value in options
    ]


def test_settings_menu_covers_every_editable_step() -> None:
    markup = keyboards.settings_menu()

    assert len(markup.inline_keyboard) == len(keyboards.EDITABLE_STEPS)
    for row, step in zip(markup.inline_keyboard, keyboards.EDITABLE_STEPS, strict=True):
        assert row[0].callback_data == f"set:edit:{step}"
        assert row[0].text.strip()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("onb:prep:20", "20"),
        ("onb:prep:0", "0"),
        ("onb:transport:car", None),  # шаг не тот
        ("set:prep:20", None),  # префикс не тот
        ("onb:prep", None),  # частей меньше трёх
        ("onb:prep:20:30", None),  # частей больше трёх
        ("", None),
        (None, None),
    ],
)
def test_parse_callback_value(data: str | None, expected: str | None) -> None:
    assert (
        keyboards.parse_callback_value(data, keyboards.CB_ONBOARDING, keyboards.STEP_PREP)
        == expected
    )
