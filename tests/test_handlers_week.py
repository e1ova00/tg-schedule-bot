"""Тесты команды /week: неделя целиком и листание кнопками.

Telegram не поднимается: сообщение и нажатие — заглушки из `tests/fakes.py`,
«сегодня» подменяется через `app.clock.today`, поэтому результат не зависит от
реального дня.

Главное, что здесь проверяется и что легко сломать незаметно:

* листание **редактирует то же сообщение** (`edits`), а не шлёт новое (`answers`) —
  иначе чат зарастает одинаковыми простынями;
* за краем диапазона (±52 недели) кнопки нет, а старая кнопка отвечает алертом и
  ничего не меняет.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardMarkup

from app import clock, keyboards, texts
from app.handlers.week import handle_week, handle_week_nav, render_week
from app.schedule import WEEK_NAV_LIMIT, ScheduleError, clear_cache, monday_of
from fakes import FakeCallback, FakeMessage

MON_ODD = date(2026, 8, 31)
WED_ODD = date(2026, 9, 2)
SUN_ODD = date(2026, 9, 6)
MON_EVEN = date(2026, 9, 7)
TUE_EVEN = date(2026, 9, 8)


@pytest.fixture(autouse=True)
def _clean_schedule_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> Callable[[date], None]:
    """Подменяет «сегодня» по Москве, не трогая системные часы."""

    def _freeze(day: date) -> None:
        monkeypatch.setattr(clock, "today", lambda *args, **kwargs: day)

    return _freeze


def command(args: str | None = None) -> CommandObject:
    return CommandObject(prefix="/", command="week", args=args)


def go(monday: date) -> str:
    return f"{keyboards.CB_WEEK}:{keyboards.WEEK_GO}:{monday.isoformat()}"


def buttons(markup: Any) -> dict[str, str]:
    """Подписи кнопок и их callback_data одним словарём."""
    assert isinstance(markup, InlineKeyboardMarkup)
    return {
        button.text: button.callback_data or ""
        for row in markup.inline_keyboard
        for button in row
    }


# ======================================================================================
# 1. /week без аргумента и с датой
# ======================================================================================


async def test_week_without_date_shows_current_week(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(WED_ODD)
    message = FakeMessage(text="/week")

    await handle_week(message, command())  # type: ignore[arg-type]

    assert len(message.answers) == 1
    answer = message.answers[0]
    assert "Неделя 31 августа — 5 сентября" in answer
    assert "числитель" in answer
    assert "<b>Среда</b>, 2 сентября — сегодня" in answer


async def test_week_without_date_on_sunday_still_shows_that_week(
    freeze_today: Callable[[date], None],
) -> None:
    """Воскресенье — граничный день: оно относится к уходящей неделе, а не к следующей."""
    freeze_today(SUN_ODD)
    message = FakeMessage(text="/week")

    await handle_week(message, command())  # type: ignore[arg-type]

    assert "Неделя 31 августа — 5 сентября" in message.answers[0]
    assert "числитель" in message.answers[0]
    # Само воскресенье в обзоре не показываем — пар в этот день не бывает.
    assert "сегодня" not in message.answers[0]


async def test_week_with_date_shows_the_week_of_that_date(
    freeze_today: Callable[[date], None],
) -> None:
    """/week 2026-09-08 — вторник знаменателя, значит неделя 7–12 сентября."""
    freeze_today(WED_ODD)
    message = FakeMessage(text="/week 2026-09-08")

    await handle_week(message, command("2026-09-08"))  # type: ignore[arg-type]

    answer = message.answers[0]
    assert "Неделя 7 сентября — 12 сентября" in answer
    assert "знаменатель" in answer
    # Сегодняшний день в другой неделе — пометки быть не должно.
    assert "сегодня" not in answer


async def test_week_with_a_monday_date_shows_the_same_week(
    freeze_today: Callable[[date], None],
) -> None:
    """Любой день недели даёт один и тот же ответ — неделя не «съезжает»."""
    freeze_today(WED_ODD)
    answers = []
    for shift in range(7):
        message = FakeMessage()
        day = MON_EVEN + timedelta(days=shift)
        await handle_week(message, command(day.isoformat()))  # type: ignore[arg-type]
        answers.append(message.answers[0])

    assert len(set(answers)) == 1


async def test_even_week_shows_empty_friday_and_saturday(
    freeze_today: Callable[[date], None],
) -> None:
    """CLAUDE.md, правило 2: по знаменателю пятница и суббота пустые — и это норма."""
    freeze_today(MON_EVEN)
    message = FakeMessage(text="/week")

    await handle_week(message, command())  # type: ignore[arg-type]

    answer = message.answers[0]
    assert f"<b>Пятница</b>, 11 сентября\n{texts.WEEK_DAY_EMPTY}" in answer
    assert f"<b>Суббота</b>, 12 сентября\n{texts.WEEK_DAY_EMPTY}" in answer


async def test_odd_week_shows_empty_thursday(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(MON_ODD)
    message = FakeMessage(text="/week")

    await handle_week(message, command())  # type: ignore[arg-type]

    assert f"<b>Четверг</b>, 3 сентября\n{texts.WEEK_DAY_EMPTY}" in message.answers[0]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("завтра", id="слово вместо даты"),
        pytest.param("08.09.2026", id="точки вместо дефисов"),
        pytest.param("2026-02-29", id="29 февраля в невисокосном году"),
        pytest.param("2026-13-01", id="тринадцатого месяца не бывает"),
    ],
)
async def test_week_with_bad_date_explains_the_format(
    freeze_today: Callable[[date], None], raw: str
) -> None:
    freeze_today(WED_ODD)
    message = FakeMessage(text=f"/week {raw}")

    await handle_week(message, command(raw))  # type: ignore[arg-type]

    assert message.answers == [texts.WEEK_BAD_DATE.format(value=raw)]
    assert message.markups == [None], "кнопок листания под ошибкой быть не должно"


async def test_week_bad_date_is_escaped(freeze_today: Callable[[date], None]) -> None:
    """Ответы уходят с parse_mode=HTML — «<b>» в аргументе не должно ломать сообщение."""
    freeze_today(WED_ODD)
    message = FakeMessage(text="/week <b>")

    await handle_week(message, command("<b>"))  # type: ignore[arg-type]

    assert "&lt;b&gt;" in message.answers[0]
    assert "<b>" not in message.answers[0]


async def test_broken_schedule_answers_politely(
    freeze_today: Callable[[date], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_today(WED_ODD)

    def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr("app.handlers.week.format_week", boom)
    message = FakeMessage(text="/week")

    await handle_week(message, command())  # type: ignore[arg-type]

    assert message.answers == [texts.SCHEDULE_ERROR]


# ======================================================================================
# 2. Кнопки листания под сообщением
# ======================================================================================


async def test_week_message_has_both_nav_buttons(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(WED_ODD)
    message = FakeMessage(text="/week")

    await handle_week(message, command())  # type: ignore[arg-type]

    found = buttons(message.last_markup)
    assert found[texts.BTN_WEEK_PREV] == go(MON_ODD - timedelta(days=7))
    assert found[texts.BTN_WEEK_NEXT] == go(MON_EVEN)


def test_render_week_hides_the_button_at_the_edge() -> None:
    """У края диапазона кнопка «дальше» просто не рисуется — нажимать не на что."""
    last = monday_of(MON_ODD) + timedelta(weeks=WEEK_NAV_LIMIT)

    _, markup = render_week(last, MON_ODD)

    found = buttons(markup)
    assert texts.BTN_WEEK_PREV in found
    assert texts.BTN_WEEK_NEXT not in found

    first = monday_of(MON_ODD) - timedelta(weeks=WEEK_NAV_LIMIT)
    _, backward = render_week(first, MON_ODD)
    assert texts.BTN_WEEK_NEXT in buttons(backward)
    assert texts.BTN_WEEK_PREV not in buttons(backward)


# ======================================================================================
# 3. Листание: редактируем то же сообщение
# ======================================================================================


async def test_next_week_edits_the_same_message(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(WED_ODD)
    callback = FakeCallback(data=go(MON_EVEN))

    await handle_week_nav(callback)  # type: ignore[arg-type]

    message = callback.message
    assert message is not None
    assert message.answers == [], "новое сообщение слать не надо — правим старое"
    assert len(message.edits) == 1
    text, markup = message.edits[0]
    assert "Неделя 7 сентября — 12 сентября" in text
    assert "знаменатель" in text
    assert callback.answered == [(None, False)], "всплывашка без текста — просто «принял»"
    assert buttons(markup)[texts.BTN_WEEK_NEXT] == go(MON_EVEN + timedelta(days=7))


async def test_previous_week_edits_the_same_message(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(MON_EVEN)
    callback = FakeCallback(data=go(MON_ODD))

    await handle_week_nav(callback)  # type: ignore[arg-type]

    message = callback.message
    assert message is not None
    assert message.answers == []
    assert "Неделя 31 августа — 5 сентября" in message.edits[0][0]


async def test_paging_forward_and_back_returns_to_the_same_week(
    freeze_today: Callable[[date], None],
) -> None:
    """Туда-обратно: даты сдвигаются ровно на 7 дней и возвращаются на место."""
    freeze_today(WED_ODD)
    message = FakeMessage(text="/week")
    await handle_week(message, command())  # type: ignore[arg-type]
    start_text = message.answers[0]

    forward = FakeCallback(data=buttons(message.last_markup)[texts.BTN_WEEK_NEXT])
    await handle_week_nav(forward)  # type: ignore[arg-type]
    assert forward.message is not None
    next_text, next_markup = forward.message.edits[0]
    assert "7 сентября — 12 сентября" in next_text

    back = FakeCallback(data=buttons(next_markup)[texts.BTN_WEEK_PREV])
    await handle_week_nav(back)  # type: ignore[arg-type]
    assert back.message is not None
    back_text, _ = back.message.edits[0]

    assert back_text == start_text


async def test_paging_ten_weeks_forward_keeps_step_of_seven_days(
    freeze_today: Callable[[date], None],
) -> None:
    """Каждое нажатие — ровно одна неделя, чётность при этом чередуется."""
    freeze_today(WED_ODD)
    monday = MON_ODD
    parities = []
    for _ in range(10):
        monday += timedelta(days=7)
        callback = FakeCallback(data=go(monday))

        await handle_week_nav(callback)  # type: ignore[arg-type]

        assert callback.message is not None
        text = callback.message.edits[0][0]
        parities.append("числитель" if "Это числитель." in text else "знаменатель")

    assert parities == ["знаменатель", "числитель"] * 5


async def test_nav_falls_back_to_a_new_message_if_editing_fails(
    freeze_today: Callable[[date], None],
) -> None:
    """Сообщение слишком старое, чтобы его править, — ответить всё равно надо."""
    freeze_today(WED_ODD)
    message = FakeMessage()

    async def broken_edit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("message is too old")

    message.edit_text = broken_edit  # type: ignore[method-assign]
    callback = FakeCallback(data=go(MON_EVEN), message=message)

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert len(message.answers) == 1
    assert "Неделя 7 сентября — 12 сентября" in message.answers[0]


# ======================================================================================
# 4. Края диапазона и сломанные кнопки
# ======================================================================================


async def test_button_beyond_the_limit_shows_an_alert(
    freeze_today: Callable[[date], None],
) -> None:
    """Кнопку в такую неделю мы не рисуем, но старое сообщение может пережить год."""
    freeze_today(WED_ODD)
    far = monday_of(WED_ODD) + timedelta(weeks=WEEK_NAV_LIMIT + 1)
    callback = FakeCallback(data=go(far))

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert callback.answered == [(texts.WEEK_EDGE, True)]
    assert callback.message is not None
    assert callback.message.edits == [], "сообщение меняться не должно"
    assert callback.message.answers == []


async def test_button_beyond_the_limit_backwards_shows_an_alert(
    freeze_today: Callable[[date], None],
) -> None:
    freeze_today(WED_ODD)
    far = monday_of(WED_ODD) - timedelta(weeks=WEEK_NAV_LIMIT + 1)
    callback = FakeCallback(data=go(far))

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert callback.answered == [(texts.WEEK_EDGE, True)]
    assert callback.message is not None
    assert callback.message.edits == []


async def test_last_allowed_week_still_opens(
    freeze_today: Callable[[date], None],
) -> None:
    """Ровно 52 недели — ещё внутри диапазона, алерта быть не должно."""
    freeze_today(WED_ODD)
    edge = monday_of(WED_ODD) + timedelta(weeks=WEEK_NAV_LIMIT)
    callback = FakeCallback(data=go(edge))

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert callback.answered == [(None, False)]
    assert callback.message is not None
    assert len(callback.message.edits) == 1
    # А кнопки «дальше» на этом краю уже нет.
    assert texts.BTN_WEEK_NEXT not in buttons(callback.message.edits[0][1])


async def test_command_itself_is_limited_by_the_range(
    freeze_today: Callable[[date], None],
) -> None:
    """`/week` на дату далеко за пределами ±52 недель отказывает так же, как кнопка.

    Раньше команда неделю всё-таки показывала — и оставляла без кнопок вообще,
    выйти можно было только новой командой. Теперь она проверяет диапазон сама,
    как это уже делали кнопки, и отвечает тем же текстом `WEEK_EDGE`.
    """
    freeze_today(WED_ODD)
    message = FakeMessage(text="/week 2050-01-03")

    await handle_week(message, command("2050-01-03"))  # type: ignore[arg-type]

    assert message.answers == [texts.WEEK_EDGE]


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(f"{keyboards.CB_WEEK}:{keyboards.WEEK_GO}:завтра", id="не дата"),
        pytest.param(f"{keyboards.CB_WEEK}:{keyboards.WEEK_GO}:2026-02-29", id="кривая дата"),
        pytest.param(f"{keyboards.CB_WEEK}:{keyboards.WEEK_GO}:", id="пустое значение"),
    ],
)
async def test_broken_button_is_handled_politely(
    freeze_today: Callable[[date], None], data: str
) -> None:
    freeze_today(WED_ODD)
    callback = FakeCallback(data=data)

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]
    assert callback.message is not None
    assert callback.message.edits == []


async def test_button_under_an_inaccessible_message_does_not_crash(
    freeze_today: Callable[[date], None],
) -> None:
    """Кнопку нажали под очень старым сообщением — отвечать в него нельзя."""
    freeze_today(WED_ODD)
    callback = FakeCallback(data=go(MON_EVEN), message=None)

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert callback.answered == [(texts.STALE_BUTTON, True)]


async def test_nav_survives_broken_schedule(
    freeze_today: Callable[[date], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_today(WED_ODD)

    def boom(*args: object, **kwargs: object) -> None:
        raise ScheduleError("файл расписания повреждён")

    monkeypatch.setattr("app.handlers.week.format_week", boom)
    callback = FakeCallback(data=go(MON_EVEN))

    await handle_week_nav(callback)  # type: ignore[arg-type]

    assert callback.answered == [(texts.SCHEDULE_ERROR, True)]
    assert callback.message is not None
    assert callback.message.edits == []


# ======================================================================================
# 5. Место роутера в общей цепочке
# ======================================================================================


def test_week_router_is_registered_before_fallback(root_router: Router) -> None:
    """Иначе /week поймает fallback и ответит «не знаю такой команды»."""
    names = [child.name for child in root_router.sub_routers]

    assert "week" in names
    assert names.index("week") < names.index("fallback")


def test_week_callbacks_do_not_collide_with_notes() -> None:
    """Префиксы callback_data разные — кнопки недели и заметок не перепутаются."""
    assert keyboards.CB_WEEK != keyboards.CB_NOTES
    assert keyboards.parse_week_monday(go(TUE_EVEN)) == TUE_EVEN
    assert keyboards.parse_week_monday(f"{keyboards.CB_NOTES}:has:L1216:2026-09-01") is None
