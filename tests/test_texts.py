"""Тексты для пользователя: app/texts.py.

Бот создаётся с parse_mode=HTML (app/bot.py), поэтому «сырые» символы < > &
в тексте Telegram воспримет как разметку и вернёт ошибку 400 вместо сообщения.
"""

from __future__ import annotations

import re

import pytest

from app import texts

ALL_TEXTS = {
    "START_GREETING": texts.START_GREETING,
    "START_RETURNING": texts.START_RETURNING,
    "HELP_TEXT": texts.HELP_TEXT,
    "UNKNOWN_COMMAND": texts.UNKNOWN_COMMAND,
    "SCHEDULE_ERROR": texts.SCHEDULE_ERROR,
    "DAY_HEADER": texts.DAY_HEADER,
    "LESSON_HEAD": texts.LESSON_HEAD,
    "LESSON_PLACE": texts.LESSON_PLACE,
    "LESSON_REMOTE": texts.LESSON_REMOTE,
    "NO_LESSONS": texts.NO_LESSONS,
    "NO_LESSONS_PREFIXED": texts.NO_LESSONS_PREFIXED,
    # Этап 3: онбординг, настройки, отказ в доступе.
    "ACCESS_DENIED": texts.ACCESS_DENIED,
    "ONBOARDING_INTRO": texts.ONBOARDING_INTRO,
    "ONBOARDING_LOCATION_RETRY": texts.ONBOARDING_LOCATION_RETRY,
    "ONBOARDING_LOCATION_SAVED": texts.ONBOARDING_LOCATION_SAVED,
    "ONBOARDING_TRANSPORT_ASK": texts.ONBOARDING_TRANSPORT_ASK,
    "ONBOARDING_TRANSPORT_RETRY": texts.ONBOARDING_TRANSPORT_RETRY,
    "ONBOARDING_TRANSPORT_SAVED": texts.ONBOARDING_TRANSPORT_SAVED,
    "ONBOARDING_PREP_ASK": texts.ONBOARDING_PREP_ASK,
    "ONBOARDING_PREP_RETRY": texts.ONBOARDING_PREP_RETRY,
    "ONBOARDING_PREP_SAVED": texts.ONBOARDING_PREP_SAVED,
    "ONBOARDING_BUFFER_ASK": texts.ONBOARDING_BUFFER_ASK,
    "ONBOARDING_BUFFER_RETRY": texts.ONBOARDING_BUFFER_RETRY,
    "ONBOARDING_BUFFER_SAVED": texts.ONBOARDING_BUFFER_SAVED,
    "ONBOARDING_DONE": texts.ONBOARDING_DONE,
    "ONBOARDING_CANCELLED": texts.ONBOARDING_CANCELLED,
    "NOTHING_TO_CANCEL": texts.NOTHING_TO_CANCEL,
    "SETTINGS_CARD": texts.SETTINGS_CARD,
    "SETTINGS_LOCATION_ASK": texts.SETTINGS_LOCATION_ASK,
    "SETTINGS_TRANSPORT_ASK": texts.SETTINGS_TRANSPORT_ASK,
    "SETTINGS_PREP_ASK": texts.SETTINGS_PREP_ASK,
    "SETTINGS_BUFFER_ASK": texts.SETTINGS_BUFFER_ASK,
    "SETTINGS_NOT_READY": texts.SETTINGS_NOT_READY,
}

# Разрешаем только теги, которые Telegram действительно понимает в режиме HTML.
ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"}


@pytest.mark.parametrize(("name", "value"), ALL_TEXTS.items())
def test_text_is_not_empty(name: str, value: str) -> None:
    assert value.strip(), f"{name} пустой — пользователю нечего показать"


@pytest.mark.parametrize(("name", "value"), ALL_TEXTS.items())
def test_text_is_safe_for_html_parse_mode(name: str, value: str) -> None:
    """Незакрытые или незнакомые теги — частая причина «бот молчит» при parse_mode=HTML."""
    tags = {tag.lower() for tag in re.findall(r"<\s*/?\s*([a-zA-Z-]+)", value)}
    assert tags <= ALLOWED_TAGS, f"{name}: подозрительные HTML-теги {tags - ALLOWED_TAGS}"
    assert "&" not in value.replace("&amp;", ""), f"{name}: символ & нужно экранировать как &amp;"


def test_help_lists_available_commands() -> None:
    assert "/start" in texts.HELP_TEXT
    assert "/help" in texts.HELP_TEXT


@pytest.mark.parametrize("command", ["/today", "/tomorrow"])
def test_help_lists_schedule_commands(command: str) -> None:
    """Команды этапа 2 должны быть в /help, иначе человек про них не узнает."""
    assert command in texts.HELP_TEXT


def test_weekdays_and_months_are_full_and_russian() -> None:
    """Заголовок дня собирается из этих списков — пропуск сдвинет все названия."""
    assert len(texts.WEEKDAYS_RU) == 7
    assert texts.WEEKDAYS_RU[0] == "понедельник"
    assert texts.WEEKDAYS_RU[6] == "воскресенье"
    assert len(texts.MONTHS_RU) == 12
    assert texts.MONTHS_RU[0] == "января"
    assert texts.MONTHS_RU[11] == "декабря"


def test_parity_words_cover_both_weeks() -> None:
    assert texts.PARITY_RU == {"odd": "числитель", "even": "знаменатель"}


def test_greeting_points_to_help() -> None:
    """После /start человек должен понять, куда идти дальше."""
    assert "/help" in texts.START_GREETING


def test_unknown_command_points_to_help() -> None:
    assert "/help" in texts.UNKNOWN_COMMAND


# --------------------------------------------------------------------------------------
# Этап 3: онбординг и настройки
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["/settings", "/cancel"])
def test_help_lists_stage_three_commands(command: str) -> None:
    assert command in texts.HELP_TEXT


def test_returning_greeting_does_not_start_the_dialog_again() -> None:
    """Тому, кто уже настроен, про геопозицию писать не надо — он решит, что всё сбросилось."""
    assert "геопозиц" not in texts.START_RETURNING.lower()
    assert "/settings" in texts.START_RETURNING


def test_location_question_explains_itself() -> None:
    """Просьба прислать координаты обязана объяснять зачем и куда они денутся."""
    text = texts.ONBOARDING_INTRO

    assert texts.BTN_SEND_LOCATION in text
    assert "скрепк" in text  # подсказка для Telegram на компьютере
    assert "/cancel" in text


def test_retry_texts_offer_a_way_out() -> None:
    """Если не получается ответить — человек не должен чувствовать себя в ловушке."""
    assert "/cancel" in texts.ONBOARDING_LOCATION_RETRY
    assert "/start" in texts.SETTINGS_NOT_READY
    assert "/settings" in texts.ONBOARDING_CANCELLED


@pytest.mark.parametrize(
    ("template", "fields"),
    [
        (texts.ONBOARDING_TRANSPORT_SAVED, {"transport": "едешь на машине"}),
        (texts.ONBOARDING_PREP_SAVED, {"value": "30 минут"}),
        (texts.ONBOARDING_BUFFER_SAVED, {"value": "10 минут"}),
        (texts.ONBOARDING_PREP_RETRY, {"minimum": 0, "maximum": 180}),
        (texts.ONBOARDING_BUFFER_RETRY, {"minimum": 0, "maximum": 60}),
        (texts.ONBOARDING_DONE, {"settings": "карточка"}),
        (
            texts.SETTINGS_CARD,
            {
                "location": "сохранена",
                "transport": "На машине",
                "prep": "30 минут",
                "buffer": "10 минут",
            },
        ),
    ],
)
def test_templates_format_with_expected_fields(
    template: str, fields: dict[str, object]
) -> None:
    """Лишняя или забытая подстановка — это KeyError в момент отправки сообщения."""
    result = template.format(**fields)

    assert "{" not in result and "}" not in result
    for value in fields.values():
        assert str(value) in result


def test_transport_titles_cover_both_modes() -> None:
    assert set(texts.TRANSPORT_TITLES) == {"car", "public_transport"}
    assert set(texts.TRANSPORT_PHRASES) == {"car", "public_transport"}


def test_settings_card_has_all_four_lines() -> None:
    for placeholder in ("{location}", "{transport}", "{prep}", "{buffer}"):
        assert placeholder in texts.SETTINGS_CARD


def test_finish_text_warns_about_silent_mondays() -> None:
    """Правило 2 из CLAUDE.md: по понедельникам будильника не будет — предупреждаем сразу."""
    assert "онедельник" in texts.ONBOARDING_DONE
