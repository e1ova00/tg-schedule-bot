"""Точка входа `python -m app`: понятные ошибки вместо трейсбеков.

Пользователь — не программист. Если токена нет или он неверный, в терминале должна быть
одна человеческая строка с подсказкой, а не 30 строк Traceback.
"""

from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetMe

from app import __main__ as entrypoint
from app.config import Config, ConfigError


@pytest.fixture(autouse=True)
def no_real_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    """Чтобы тесты не переписывали настоящий logs/bot.log и корневой логгер."""
    monkeypatch.setattr(entrypoint, "setup_logging", lambda **kwargs: None)


def test_main_without_token_returns_error_code_and_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken_load_config() -> Config:
        raise ConfigError("Не найден BOT_TOKEN. Скопируйте .env.example в .env.")

    monkeypatch.setattr(entrypoint, "load_config", broken_load_config)

    exit_code = entrypoint.main()

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "BOT_TOKEN" in stderr
    assert "Traceback" not in stderr


def test_explain_translates_bad_token() -> None:
    error = TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")
    explanation = entrypoint._explain(error)

    assert "BOT_TOKEN" in explanation
    assert "BotFather" in explanation
    assert "Unauthorized" not in explanation


def test_explain_translates_network_problem() -> None:
    error = TelegramNetworkError(method=GetMe(), message="Cannot connect")
    explanation = entrypoint._explain(error)

    assert "интернет" in explanation.lower()


def test_explain_falls_back_to_log_file_hint() -> None:
    explanation = entrypoint._explain(RuntimeError("что-то странное"))

    assert "logs/bot.log" in explanation
    assert "RuntimeError" in explanation


def test_main_survives_bad_token_from_telegram(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Токен есть, но Telegram его не принял: код выхода 1 и подсказка, без трейсбека."""
    from app.config import build_config

    monkeypatch.setattr(
        entrypoint, "load_config", lambda: build_config({"BOT_TOKEN": "123:неверный"})
    )

    async def failing_run_bot(config: Config) -> None:
        raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")

    monkeypatch.setattr(entrypoint, "run_bot", failing_run_bot)

    exit_code = entrypoint.main()

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "BotFather" in stderr
    assert "Traceback" not in stderr


def test_main_returns_zero_on_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl+C — штатное завершение, а не авария."""
    from app.config import build_config

    monkeypatch.setattr(
        entrypoint, "load_config", lambda: build_config({"BOT_TOKEN": "123:токен"})
    )

    async def interrupted_run_bot(config: Config) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(entrypoint, "run_bot", interrupted_run_bot)

    assert entrypoint.main() == 0
