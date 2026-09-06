#!/usr/bin/env bash
#
# Обновление бота на сервере в одну команду.
#
#   bash /root/tg-schedule-bot/scripts/deploy.sh
#
# Что делает по шагам:
#   1. Забирает новую версию кода с GitHub (git pull).
#   2. Пересобирает образ и перезапускает контейнер (docker compose up -d --build).
#   3. Убирает мусор, оставшийся от старых образов, чтобы не забивать диск.
#   4. Показывает состояние и последние строки лога.
#
# База данных и логи при этом не трогаются: они лежат на сервере в папках
# data/ и logs/ и живут отдельно от контейнера.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

if [ ! -f .env ]; then
    echo "Ошибка: рядом с docker-compose.yml нет файла .env." >&2
    echo "Создайте его: cp .env.example .env — и заполните (см. docs/DEPLOY.md)." >&2
    exit 1
fi

echo "==> 1/4 Забираем свежий код с GitHub"
# --ff-only: обновиться можно только «в перемотку». Если на сервере кто-то правил
# файлы руками, git честно остановится вместо того, чтобы устроить конфликт слияния.
git pull --ff-only

echo "==> 2/4 Пересобираем образ и перезапускаем бота"
docker compose up -d --build

echo "==> 3/4 Убираем старые неиспользуемые образы"
# Только «висячие» слои от предыдущих сборок. Рабочий образ не трогается.
docker image prune -f >/dev/null

echo "==> 4/4 Проверяем, что бот поднялся"
docker compose ps
echo
echo "Последние 20 строк лога:"
docker compose logs --tail 20

echo
echo "Готово. Полный лог в реальном времени: docker compose logs -f (выход — Ctrl+C)."
