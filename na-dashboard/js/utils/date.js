/**
 * Утилиты для работы с датами.
 * Все даты дневников хранятся как локальные строки 'YYYY-MM-DD',
 * чтобы не зависеть от часового пояса при сравнении «тот же день».
 */

export const DAY_MS = 24 * 60 * 60 * 1000;
const LOCALE = 'ru-RU';

const pad = (n) => String(n).padStart(2, '0');

/** Date → 'YYYY-MM-DD' (локальное время). */
export function toISODate(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** 'YYYY-MM-DD' → Date (локальная полночь). */
export function parseISODate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

export function today() {
  return toISODate(new Date());
}

export function addDays(iso, n) {
  const d = parseISODate(iso);
  d.setDate(d.getDate() + n);
  return toISODate(d);
}

/** Количество дней от a до b (b − a). */
export function daysBetween(a, b) {
  return Math.round((parseISODate(b) - parseISODate(a)) / DAY_MS);
}

/** Понедельник недели, в которую попадает дата. */
export function startOfWeek(iso) {
  const d = parseISODate(iso);
  const shift = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - shift);
  return toISODate(d);
}

/** День недели 1 (пн) … 7 (вс). */
export function isoWeekday(iso) {
  const day = parseISODate(iso).getDay();
  return day === 0 ? 7 : day;
}

/** Массив из n дат, заканчивающийся end (включительно). */
export function lastNDates(n, end = today()) {
  return Array.from({ length: n }, (_, i) => addDays(end, i - n + 1));
}

export function isWithinLastDays(iso, days, end = today()) {
  const diff = daysBetween(iso, end);
  return diff >= 0 && diff < days;
}

/** '07:30' → 450 */
export function timeToMinutes(time) {
  if (typeof time !== 'string' || !/^\d{1,2}:\d{2}$/.test(time)) return null;
  const [h, m] = time.split(':').map(Number);
  return h * 60 + m;
}

/** 450 → '07:30' */
export function minutesToTime(minutes) {
  const total = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
}

/** Минуты от полуночи для ISO-времени (локально). */
export function minutesOfDay(isoDateTime) {
  const d = new Date(isoDateTime);
  return d.getHours() * 60 + d.getMinutes();
}

/** 7.8 → '7 ч 48 мин' */
export function formatHours(hours) {
  if (!Number.isFinite(hours)) return '—';
  const total = Math.round(hours * 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m} мин`;
  return m === 0 ? `${h} ч` : `${h} ч ${m} мин`;
}

/** Секунды → '01:02:03' */
export function formatClock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${pad(h)}:${pad(m)}:${pad(s % 60)}`;
}

/** '2026-10-03' → '3 окт.' */
export function formatShortDate(iso) {
  return parseISODate(iso).toLocaleDateString(LOCALE, { day: 'numeric', month: 'short' });
}

/** '2026-10-03' → 'суббота, 3 октября' */
export function formatLongDate(iso) {
  return parseISODate(iso).toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'long' });
}

export function formatTime(isoDateTime) {
  return new Date(isoDateTime).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
}

/** Человеческое название дня относительно сегодня. */
export function relativeDayLabel(iso) {
  const diff = daysBetween(iso, today());
  if (diff === 0) return 'Сегодня';
  if (diff === 1) return 'Вчера';
  if (diff === -1) return 'Завтра';
  return formatShortDate(iso);
}

export function greetingFor(date = new Date()) {
  const h = date.getHours();
  if (h >= 5 && h < 12) return 'Доброе утро';
  if (h >= 12 && h < 17) return 'Добрый день';
  if (h >= 17 && h < 23) return 'Добрый вечер';
  return 'Доброй ночи';
}
