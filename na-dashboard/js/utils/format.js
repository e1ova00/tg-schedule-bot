/** Форматирование чисел для русскоязычного интерфейса. */

const nf0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

export const fmt = (value, digits = 0) => {
  if (!Number.isFinite(value)) return '—';
  return (digits === 0 ? nf0 : nf1).format(value);
};

export const fmtKg = (value) => `${fmt(value, 1)} кг`;

export const fmtPercent = (value) => `${fmt(value)}%`;

/** Знак для изменения: +12% / −3% */
export const fmtSigned = (value, digits = 0, suffix = '%') => {
  if (!Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${fmt(Math.abs(value), digits)}${suffix}`;
};

/** Склонение: plural(5, ['тренировка', 'тренировки', 'тренировок']) */
export function plural(n, forms) {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return forms[2];
  if (last > 1 && last < 5) return forms[1];
  if (last === 1) return forms[0];
  return forms[2];
}

/** Число из строки формы: '72,5' → 72.5; пусто → null */
export function toNumber(value) {
  if (value === null || value === undefined) return null;
  const s = String(value).trim().replace(',', '.');
  if (s === '') return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}
