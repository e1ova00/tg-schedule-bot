"""Сборка текстов из данных: без Telegram, без сети, без базы.

Сюда попадает всё, что превращает `User` в человеческие строки. Отдельный модуль нужен,
чтобы и онбординг, и /settings показывали настройки одинаково, а тесты могли проверить
формулировки, не поднимая бота.
"""

from __future__ import annotations

from app import texts, users


def minutes_word(count: int) -> str:
    """«1 минута», «3 минуты», «15 минут» — правильное окончание для числа."""
    tail_two = count % 100
    tail_one = count % 10
    if 11 <= tail_two <= 14:
        return "минут"
    if tail_one == 1:
        return "минута"
    if 2 <= tail_one <= 4:
        return "минуты"
    return "минут"


def format_minutes(count: int | None) -> str:
    """«30 минут» или «пока не задано», если значения ещё нет."""
    if count is None:
        return texts.VALUE_MISSING
    if count == 0:
        return "0 минут (совсем без запаса)"
    return f"{count} {minutes_word(count)}"


def transport_title(mode: str | None) -> str:
    """Способ передвижения по-русски — для карточки настроек и кнопок."""
    return texts.TRANSPORT_TITLES.get(mode or "", texts.VALUE_MISSING)


def transport_phrase(mode: str | None) -> str:
    """То же самое, но так, чтобы вставлялось в середину фразы."""
    return texts.TRANSPORT_PHRASES.get(mode or "", texts.VALUE_MISSING)


def location_title(user: users.User | None) -> str:
    """Про геопозицию говорим «сохранена», без сырых координат — так спокойнее."""
    if user is not None and user.has_location:
        return texts.LOCATION_SAVED_SHORT
    return texts.VALUE_MISSING


def format_settings(user: users.User | None) -> str:
    """Карточка настроек для /settings и для финала онбординга."""
    return texts.SETTINGS_CARD.format(
        location=location_title(user),
        transport=transport_title(user.transport_mode if user else None),
        prep=format_minutes(user.prep_minutes if user else None),
        buffer=format_minutes(user.buffer_minutes if user else None),
    )


__all__ = [
    "format_minutes",
    "format_settings",
    "location_title",
    "minutes_word",
    "transport_phrase",
    "transport_title",
]
