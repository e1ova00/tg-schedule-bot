"""Тесты настроек: app/config.py.

Ни сети, ни Telegram: на вход — словарь переменных окружения, на выход — Config.
Проверяем в первую очередь граничные случаи, на которых пользователь спотыкается:
пустой токен, мусор в списке ID, незнакомый уровень логов, несуществующий часовой пояс.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from app.config import (
    DEFAULT_DB_PATH,
    DEFAULT_LOG_LEVEL,
    DEFAULT_TIMEZONE,
    PROJECT_ROOT,
    Config,
    ConfigError,
    build_config,
    load_config,
    parse_db_path,
    parse_log_level,
    parse_proxy_url,
    parse_timezone,
    parse_user_ids,
)

# --------------------------------------------------------------------------------------
# build_config
# --------------------------------------------------------------------------------------


def test_build_config_full_env(tmp_path: Path) -> None:
    """Все переменные заданы корректно — получаем заполненный Config."""
    # Абсолютный путь берём через tmp_path, а не пишем строкой в стиле Unix:
    # на Windows "/var/lib/bot/bot.db" без буквы диска абсолютным не считается.
    absolute_db_path = tmp_path / "bot.db"
    config = build_config(
        {
            "BOT_TOKEN": "123456:AAHfake-token",
            "LOG_LEVEL": "DEBUG",
            "DB_PATH": str(absolute_db_path),
            "TZ": "Europe/Berlin",
            "ALLOWED_USER_IDS": "111, 222",
            "TELEGRAM_PROXY_URL": "socks5://127.0.0.1:10808",
        }
    )

    assert isinstance(config, Config)
    assert config.bot_token == "123456:AAHfake-token"
    assert config.log_level == "DEBUG"
    assert config.db_path == absolute_db_path
    assert config.timezone.key == "Europe/Berlin"
    assert config.allowed_user_ids == (111, 222)
    assert config.proxy_url == "socks5://127.0.0.1:10808"


def test_build_config_defaults_when_only_token_given() -> None:
    """Кроме токена всё необязательно: остальное берётся из значений по умолчанию."""
    config = build_config({"BOT_TOKEN": "123456:AAHfake-token"})

    assert config.log_level == DEFAULT_LOG_LEVEL == "INFO"
    assert config.timezone.key == DEFAULT_TIMEZONE == "Europe/Moscow"
    assert config.db_path == PROJECT_ROOT / DEFAULT_DB_PATH
    assert config.proxy_url is None
    assert config.allowed_user_ids == ()


def test_build_config_strips_spaces_around_token() -> None:
    """Токен, скопированный из чата с пробелом или переводом строки, всё равно должен читаться."""
    config = build_config({"BOT_TOKEN": "  123456:AAHfake-token \n"})
    assert config.bot_token == "123456:AAHfake-token"


@pytest.mark.parametrize(
    "env",
    [
        pytest.param({}, id="переменной нет вообще"),
        pytest.param({"BOT_TOKEN": ""}, id="пустая строка"),
        pytest.param({"BOT_TOKEN": "   "}, id="одни пробелы"),
    ],
)
def test_build_config_without_token_raises_config_error(env: dict[str, str]) -> None:
    """Без токена бот бесполезен — падаем понятной для человека ошибкой, а не KeyError."""
    with pytest.raises(ConfigError) as exc_info:
        build_config(env)

    message = str(exc_info.value)
    assert "BOT_TOKEN" in message
    assert ".env" in message
    assert "BotFather" in message


def test_build_config_result_is_immutable() -> None:
    """Config заморожен: случайно поменять настройки в рантайме нельзя."""
    config = build_config({"BOT_TOKEN": "123456:AAHfake-token"})
    with pytest.raises(Exception):
        config.bot_token = "другой"  # type: ignore[misc]


# --------------------------------------------------------------------------------------
# parse_user_ids
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("123456789", (123456789,), id="один id"),
        pytest.param("111,222,333", (111, 222, 333), id="несколько без пробелов"),
        pytest.param(" 111 , 222 ", (111, 222), id="с пробелами вокруг"),
        pytest.param("111,,222", (111, 222), id="лишняя запятая посередине"),
        pytest.param("111,", (111,), id="запятая в конце"),
    ],
)
def test_parse_user_ids_valid(raw: str, expected: tuple[int, ...]) -> None:
    assert parse_user_ids(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="переменной нет"),
        pytest.param("", id="пустая строка"),
        pytest.param("   ", id="одни пробелы"),
    ],
)
def test_parse_user_ids_empty_is_not_an_error(raw: str | None) -> None:
    """Пустой список разрешён: ограничение доступа появится только на этапе 3."""
    assert parse_user_ids(raw) == ()


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("111,abc", id="буквы"),
        pytest.param("@username", id="юзернейм вместо id"),
        pytest.param("111 222", id="пробел вместо запятой"),
        pytest.param("111.5", id="дробное число"),
    ],
)
def test_parse_user_ids_rejects_garbage(raw: str) -> None:
    """Мусор в ALLOWED_USER_IDS — ошибка с подсказкой, где взять правильный ID."""
    with pytest.raises(ConfigError) as exc_info:
        parse_user_ids(raw)

    message = str(exc_info.value)
    assert "ALLOWED_USER_IDS" in message
    assert "userinfobot" in message


def test_parse_user_ids_error_names_the_bad_value() -> None:
    """В тексте ошибки видно, какой именно кусок строки не разобрался."""
    with pytest.raises(ConfigError) as exc_info:
        parse_user_ids("111, ой")
    assert "ой" in str(exc_info.value)


# --------------------------------------------------------------------------------------
# parse_log_level
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("DEBUG", "DEBUG", id="как есть"),
        pytest.param("debug", "DEBUG", id="строчными буквами"),
        pytest.param(" warning ", "WARNING", id="с пробелами"),
        pytest.param("CRITICAL", "CRITICAL", id="самый строгий уровень"),
        pytest.param("ERROR", "ERROR", id="ошибки"),
        pytest.param("INFO", "INFO", id="обычный"),
    ],
)
def test_parse_log_level_valid(raw: str, expected: str) -> None:
    assert parse_log_level(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="переменной нет"),
        pytest.param("", id="пустая строка"),
        pytest.param("  ", id="одни пробелы"),
    ],
)
def test_parse_log_level_default_is_info(raw: str | None) -> None:
    assert parse_log_level(raw) == "INFO"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("TRACE", id="уровень из другой библиотеки"),
        pytest.param("ИНФО", id="по-русски"),
        pytest.param("10", id="число вместо имени"),
    ],
)
def test_parse_log_level_rejects_unknown(raw: str) -> None:
    """Опечатка в LOG_LEVEL не должна молча включать другой уровень логов."""
    with pytest.raises(ConfigError) as exc_info:
        parse_log_level(raw)

    message = str(exc_info.value)
    assert raw in message
    # В ошибке перечислены допустимые значения — человеку не надо лезть в код.
    assert "DEBUG" in message and "INFO" in message


def test_parse_log_level_result_is_understood_by_logging() -> None:
    """Возвращённое имя обязано подходить logging.setLevel — иначе упадёт setup_logging."""
    for raw in ("debug", "info", "warning", "error", "critical"):
        assert isinstance(logging.getLevelName(parse_log_level(raw)), int)


# --------------------------------------------------------------------------------------
# parse_timezone
# --------------------------------------------------------------------------------------


def test_parse_timezone_valid() -> None:
    assert parse_timezone("Europe/Berlin").key == "Europe/Berlin"


def test_parse_timezone_trims_spaces() -> None:
    assert parse_timezone("  Asia/Novosibirsk  ").key == "Asia/Novosibirsk"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="переменной нет"),
        pytest.param("", id="пустая строка"),
        pytest.param("   ", id="одни пробелы"),
    ],
)
def test_parse_timezone_default_is_moscow(raw: str | None) -> None:
    assert parse_timezone(raw).key == "Europe/Moscow"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("Mars/Olympus", id="несуществующая зона"),
        pytest.param("Москва", id="по-русски"),
        pytest.param("/Europe/Moscow", id="абсолютный путь"),
        pytest.param("../etc/passwd", id="путь наружу"),
    ],
)
def test_parse_timezone_falls_back_to_moscow(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    """Кривой TZ не должен ронять бота: откатываемся на Москву и предупреждаем в логе."""
    with caplog.at_level(logging.WARNING, logger="app.config"):
        tz = parse_timezone(raw)

    assert tz.key == "Europe/Moscow"
    assert caplog.records, "Ожидали предупреждение в логах про подмену часового пояса"
    assert raw in caplog.text


# --------------------------------------------------------------------------------------
# parse_db_path
# --------------------------------------------------------------------------------------


def test_parse_db_path_relative_is_resolved_from_project_root(tmp_path: Path) -> None:
    """Относительный путь из .env считается от корня проекта, а не от папки запуска."""
    assert parse_db_path("data/bot.db", project_root=tmp_path) == tmp_path / "data" / "bot.db"


def test_parse_db_path_absolute_is_kept_as_is(tmp_path: Path) -> None:
    absolute = tmp_path / "somewhere" / "bot.db"
    assert parse_db_path(str(absolute), project_root=Path("/другой/корень")) == absolute


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="переменной нет"),
        pytest.param("", id="пустая строка"),
        pytest.param("   ", id="одни пробелы"),
    ],
)
def test_parse_db_path_default(raw: str | None, tmp_path: Path) -> None:
    assert parse_db_path(raw, project_root=tmp_path) == tmp_path / DEFAULT_DB_PATH


def test_parse_db_path_does_not_create_anything(tmp_path: Path) -> None:
    """Разбор пути — чистая функция: файлов и папок она создавать не должна."""
    parse_db_path("data/bot.db", project_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------------------
# parse_proxy_url
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="переменной нет"),
        pytest.param("", id="пустая строка"),
        pytest.param("   ", id="одни пробелы"),
    ],
)
def test_parse_proxy_url_empty_means_no_proxy(raw: str | None) -> None:
    assert parse_proxy_url(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "socks5://127.0.0.1:10808",
        "socks4://127.0.0.1:1080",
        "http://127.0.0.1:8080",
        "https://proxy.example.com:443",
    ],
)
def test_parse_proxy_url_accepts_known_schemes(raw: str) -> None:
    assert parse_proxy_url(raw) == raw


def test_parse_proxy_url_trims_spaces() -> None:
    assert parse_proxy_url("  socks5://127.0.0.1:10808  ") == "socks5://127.0.0.1:10808"


def test_parse_proxy_url_rejects_unknown_scheme() -> None:
    """Опечатка в адресе прокси не должна приводить к непонятной сетевой ошибке позже."""
    with pytest.raises(ConfigError, match="ftp://nope"):
        parse_proxy_url("ftp://nope")


# --------------------------------------------------------------------------------------
# load_config: чтение .env
# --------------------------------------------------------------------------------------


@pytest.fixture()
def isolated_environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Подменяет os.environ копией, чтобы load_dotenv не портил окружение других тестов."""
    fake_env = dict(os.environ)
    for key in ("BOT_TOKEN", "LOG_LEVEL", "DB_PATH", "TZ", "ALLOWED_USER_IDS"):
        fake_env.pop(key, None)
    monkeypatch.setattr(os, "environ", fake_env)
    return fake_env


def test_load_config_reads_env_file(tmp_path: Path, isolated_environ: dict[str, str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_TOKEN=777:from-file\nLOG_LEVEL=debug\nTZ=Europe/Moscow\nALLOWED_USER_IDS=42\n",
        encoding="utf-8",
    )

    config = load_config(env_file=env_file)

    assert config.bot_token == "777:from-file"
    assert config.log_level == "DEBUG"
    assert config.allowed_user_ids == (42,)


def test_load_config_prefers_real_environment_over_file(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    """Переменная, уже заданная в окружении (Docker, systemd), главнее файла .env."""
    env_file = tmp_path / ".env"
    env_file.write_text("BOT_TOKEN=777:from-file\n", encoding="utf-8")
    isolated_environ["BOT_TOKEN"] = "888:from-environment"

    assert load_config(env_file=env_file).bot_token == "888:from-environment"


def test_load_config_without_env_file_raises_config_error(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    """Файла .env нет и токена в окружении нет — понятная ошибка вместо трейсбека."""
    with pytest.raises(ConfigError):
        load_config(env_file=tmp_path / "нет-такого.env")
