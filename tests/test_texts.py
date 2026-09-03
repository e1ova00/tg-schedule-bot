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


def test_greeting_points_to_help() -> None:
    """После /start человек должен понять, куда идти дальше."""
    assert "/help" in texts.START_GREETING


def test_unknown_command_points_to_help() -> None:
    assert "/help" in texts.UNKNOWN_COMMAND
