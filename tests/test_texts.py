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
    "HELP_TEXT": texts.HELP_TEXT,
    "UNKNOWN_COMMAND": texts.UNKNOWN_COMMAND,
    "SCHEDULE_ERROR": texts.SCHEDULE_ERROR,
    "DAY_HEADER": texts.DAY_HEADER,
    "LESSON_HEAD": texts.LESSON_HEAD,
    "LESSON_PLACE": texts.LESSON_PLACE,
    "LESSON_REMOTE": texts.LESSON_REMOTE,
    "NO_LESSONS": texts.NO_LESSONS,
    "NO_LESSONS_PREFIXED": texts.NO_LESSONS_PREFIXED,
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
