# Образ бота для сервера. Один этап сборки, без multi-stage: проект маленький,
# экономить на размере образа тут нечего, а читать простой файл проще.
FROM python:3.12-slim

# tzdata — системная база часовых поясов. Без неё zoneinfo.ZoneInfo("Europe/Moscow")
# внутри контейнера может не найти зону, а на ней держится всё: будильники,
# APScheduler и время в логах. ca-certificates — корневые сертификаты для HTTPS
# к Telegram и 2ГИС.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PYTHONUNBUFFERED — чтобы строки лога появлялись в `docker compose logs` сразу,
# а не пачками по мере заполнения буфера.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Дублирует TZ из docker-compose.yml: если образ когда-нибудь запустят без compose,
# часовой пояс всё равно останется московским.
ENV TZ=Europe/Moscow

WORKDIR /app

# Зависимости — отдельным слоем: пока requirements.txt не менялся, пересборка
# после правки кода не выкачивает библиотеки заново.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Непривилегированный пользователь: бот не должен работать от root.
# UID ровно 1000 выбран не случайно — под этим же номером должны быть папки
# data/ и logs/ на сервере, которые проброшены внутрь контейнера (см. docs/DEPLOY.md).
RUN useradd --create-home --uid 1000 bot

COPY app ./app
COPY data ./data

# Папки под базу и логи создаём заранее и отдаём боту, иначе первый запуск
# упрётся в «нет прав на запись».
RUN mkdir -p /app/data /app/logs && chown -R bot:bot /app/data /app/logs

USER bot

CMD ["python", "-m", "app"]
