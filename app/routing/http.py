"""Общая обвязка для маршрутизаторов, которые ходят в HTTP.

Здесь живёт всё скучное: одна сессия aiohttp на процесс, таймаут, превращение любой
сетевой беды в `RouterError`. Сами реализации (2ГИС, ORS) остаются короткими.

Тестопригодность: сессия создаётся не внутри, а через `session_factory`. В тестах туда
передаётся фальшивая сессия — объект с методом `post(url, json=..., headers=...)`,
который возвращает асинхронный контекстный менеджер с полями `status` и `text()`.
Никакого настоящего сокета для проверки разбора ответов не нужно.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, ClassVar

import aiohttp

from app.routing.base import Router, RouterError

logger = logging.getLogger(__name__)

# Дольше ждать нет смысла: /route должен ответить, пока человек смотрит в экран,
# а будильнику лучше сработать по последнему известному времени, чем опоздать.
DEFAULT_TIMEOUT_SECONDS = 10.0

# Тела ответа в лог кладём обрезанным: сервисы иногда отдают HTML-страницу целиком.
_MAX_LOGGED_BODY = 300

SessionFactory = Callable[[], Any]


def _shorten(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_LOGGED_BODY else text[:_MAX_LOGGED_BODY] + "…"


class HttpRouter(Router):
    """База для маршрутизаторов поверх HTTP. Сама по себе маршрутов не считает."""

    #: Как называть сервис в тексте ошибки, попадающем в лог.
    service_title: ClassVar[str] = "Сервис маршрутов"

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._session_factory = session_factory or self._default_session_factory
        self._session: Any | None = None

    def _default_session_factory(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_seconds)
        )

    def _get_session(self) -> Any:
        """Ленивое создание сессии: без обращений к сети её не будет вовсе.

        Важно для тестов и для старта бота без ключей — `aiohttp.ClientSession`
        требует работающего event loop и создавать её «на всякий случай» незачем.
        """
        if self._session is None or getattr(self._session, "closed", False):
            self._session = self._session_factory()
        return self._session

    async def close(self) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            await session.close()
        except Exception:  # noqa: BLE001 — закрытие сессии не повод ронять бота
            logger.warning("Не удалось закрыть сессию %s", self.service_title, exc_info=True)

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST с JSON-телом. Любая беда наружу выходит как `RouterError`."""
        session = self._get_session()
        try:
            async with session.post(url, json=payload, headers=headers) as response:
                status = int(response.status)
                body = await response.text()
        except TimeoutError as exc:
            raise RouterError(
                f"{self.service_title}: ответ не пришёл за {self._timeout_seconds:.0f} с"
            ) from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise RouterError(f"{self.service_title}: сеть недоступна ({exc})") from exc

        if status == 401 or status == 403:
            raise RouterError(
                f"{self.service_title}: ключ доступа не принят (HTTP {status}). "
                "Проверьте ключ в .env."
            )
        if status != 200:
            raise RouterError(
                f"{self.service_title}: HTTP {status}, тело ответа: {_shorten(body)}"
            )

        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RouterError(
                f"{self.service_title}: в ответе не JSON, а {_shorten(body)}"
            ) from exc


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "HttpRouter", "SessionFactory"]
