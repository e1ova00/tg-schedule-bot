#!/usr/bin/env bash
#
# Резервная копия базы бота. Запускается на сервере, обычно раз в сутки по cron.
#
# Почему не обычный «cp»: бот работает круглосуточно и может писать в базу ровно
# в тот момент, когда мы её копируем — тогда копия получится битой и бесполезной.
# Команда sqlite3 ".backup" делает копию согласованно, не мешая боту работать.
#
# Как запустить руками:
#   bash /root/tg-schedule-bot/scripts/backup_db.sh
#
# Копии складываются в backups/ рядом с проектом, хранятся последние 14 штук.

set -euo pipefail

# Время в именах файлов — московское, независимо от часового пояса сервера.
export TZ="Europe/Moscow"

# Сколько копий держим. Более старые удаляются автоматически.
KEEP=14

# Корень проекта считаем от расположения самого скрипта: тогда неважно,
# из какой папки его запустили и что написано в cron.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

DB_FILE="$PROJECT_DIR/data/bot.db"
BACKUP_DIR="$PROJECT_DIR/backups"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "Ошибка: не найдена программа sqlite3." >&2
    echo "Установите её командой: sudo apt install -y sqlite3" >&2
    exit 1
fi

if [ ! -f "$DB_FILE" ]; then
    echo "Ошибка: не найден файл базы $DB_FILE." >&2
    echo "Похоже, бот ещё ни разу не запускался на этом сервере." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y-%m-%d-%H%M)"
TARGET="$BACKUP_DIR/bot-$STAMP.db"

# Кавычки внутри команды обязательны: путь может содержать пробелы.
sqlite3 "$DB_FILE" ".backup '$TARGET'"

echo "$(date '+%Y-%m-%d %H:%M:%S') копия готова: $TARGET"

# Удаляем всё, кроме последних $KEEP копий. Имена файлов начинаются с даты,
# поэтому обычная сортировка по алфавиту = сортировка по времени.
# shellcheck disable=SC2012 — имена файлов задаём мы сами, спецсимволов в них нет.
ls -1 "$BACKUP_DIR"/bot-*.db 2>/dev/null \
    | sort \
    | head -n -"$KEEP" \
    | while IFS= read -r old; do
        rm -f -- "$old"
        echo "$(date '+%Y-%m-%d %H:%M:%S') удалена старая копия: $old"
    done

exit 0
