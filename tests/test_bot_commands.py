"""Список команд для меню Telegram (кнопка ☰) не должен расходиться с /help."""

from __future__ import annotations

import re

from app import texts
from app.bot import BOT_COMMANDS


def test_bot_commands_match_help_text() -> None:
    menu_commands = {f"/{c.command}" for c in BOT_COMMANDS}
    help_commands = set(re.findall(r"/\w+", texts.HELP_TEXT))
    assert menu_commands == help_commands


def test_bot_commands_have_descriptions() -> None:
    assert all(command.description.strip() for command in BOT_COMMANDS)
