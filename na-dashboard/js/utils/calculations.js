/**
 * Чистые функции расчётов. Не трогают DOM и хранилище — легко проверить отдельно.
 */
import { addDays, daysBetween, isoWeekday, timeToMinutes, today } from './date.js';

export const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

export const mean = (values) => {
  const list = values.filter(Number.isFinite);
  return list.length ? list.reduce((s, v) => s + v, 0) / list.length : null;
};

/** Коэффициент корреляции Пирсона. null, если данных мало или нет разброса. */
export function pearson(xs, ys) {
  const n = Math.min(xs.length, ys.length);
  if (n < 3) return null;
  const mx = mean(xs.slice(0, n));
  const my = mean(ys.slice(0, n));
  let num = 0;
  let dx = 0;
  let dy = 0;
  for (let i = 0; i < n; i += 1) {
    const a = xs[i] - mx;
    const b = ys[i] - my;
    num += a * b;
    dx += a * a;
    dy += b * b;
  }
  if (dx === 0 || dy === 0) return null;
  return num / Math.sqrt(dx * dy);
}

export const percentChange = (from, to) => (from > 0 ? ((to - from) / from) * 100 : null);

/* ------------------------------------------------------------------ */
/* Силовые                                                             */
/* ------------------------------------------------------------------ */

/**
 * Расчётный одноповторный максимум по формуле Эпли:
 *   1RM = вес × (1 + повторы / 30)
 * Для одного повтора возвращает сам вес. Формула ориентировочная и
 * точнее всего работает в диапазоне 1–10 повторов.
 */
export function estimate1RM(weight, reps) {
  if (!(weight > 0) || !(reps > 0)) return 0;
  if (reps === 1) return weight;
  return weight * (1 + reps / 30);
}

/** Тренировочный объём подхода = вес × повторы. */
export const setVolume = ({ weight = 0, reps = 0 }) => (weight > 0 && reps > 0 ? weight * reps : 0);

export const workoutVolume = (workout) =>
  (workout?.exercises ?? []).reduce(
    (sum, ex) => sum + ex.sets.reduce((s, set) => s + setVolume(set), 0),
    0,
  );

/** Показатели одного упражнения внутри тренировки. */
export function exerciseStats(exercise) {
  const sets = (exercise?.sets ?? []).filter((s) => s.reps > 0);
  if (!sets.length) return null;
  return {
    maxWeight: Math.max(...sets.map((s) => s.weight || 0)),
    volume: sets.reduce((sum, s) => sum + setVolume(s), 0),
    e1rm: Math.max(...sets.map((s) => estimate1RM(s.weight, s.reps))),
  };
}

/**
 * Определить личные рекорды (PR): максимальный рабочий вес в упражнении
 * больше, чем во всех предыдущих тренировках. Первая запись упражнения PR не считается.
 */
export function detectPRs(workout, previousWorkouts) {
  const prs = [];
  for (const ex of workout.exercises ?? []) {
    const stats = exerciseStats(ex);
    if (!stats || stats.maxWeight <= 0) continue;
    let previousMax = null;
    for (const w of previousWorkouts) {
      for (const pe of w.exercises ?? []) {
        if (pe.exerciseId !== ex.exerciseId) continue;
        const ps = exerciseStats(pe);
        if (ps && (previousMax === null || ps.maxWeight > previousMax)) previousMax = ps.maxWeight;
      }
    }
    if (previousMax !== null && stats.maxWeight > previousMax) {
      prs.push({ exerciseId: ex.exerciseId, name: ex.name, weight: stats.maxWeight, previous: previousMax });
    }
  }
  return prs;
}

/**
 * Относительная «эффективность» каждой тренировки.
 * Для каждого упражнения: лучший расчётный 1RM / среднее из трёх предыдущих лучших.
 * 1.00 — как обычно, 1.05 — на 5% сильнее своей недавней нормы.
 * Так прогрессия со временем не искажает сравнение утренних и вечерних тренировок.
 */
export function scoreWorkouts(workouts) {
  const sorted = [...workouts].sort((a, b) => String(a.startedAt).localeCompare(String(b.startedAt)));
  const history = new Map();
  const result = [];
  for (const w of sorted) {
    const ratios = [];
    for (const ex of w.exercises ?? []) {
      const stats = exerciseStats(ex);
      if (!stats || stats.e1rm <= 0) continue;
      const past = history.get(ex.exerciseId) ?? [];
      if (past.length >= 2) {
        const baseline = mean(past.slice(-3));
        if (baseline > 0) ratios.push(stats.e1rm / baseline);
      }
      past.push(stats.e1rm);
      history.set(ex.exerciseId, past);
    }
    if (ratios.length) {
      const d = new Date(w.startedAt);
      result.push({ workout: w, date: w.date, performance: mean(ratios), minutes: d.getHours() * 60 + d.getMinutes() });
    }
  }
  return result;
}

/* ------------------------------------------------------------------ */
/* Восстановление                                                      */
/* ------------------------------------------------------------------ */

/**
 * Индекс восстановления 0–100 — прозрачная взвешенная сумма.
 * Каждая часть нормируется в диапазон 0…1 и умножается на свой вес:
 *
 *   Сон (длительность)  30 баллов: min(часы / 8, 1)
 *   Качество сна        20 баллов: (оценка − 1) / 4      — оценка 1…5
 *   Энергия             20 баллов: (оценка − 1) / 4      — 1…5
 *   Мышечная усталость  15 баллов: (5 − оценка) / 4      — больше усталость → меньше баллов
 *   Стресс              15 баллов: (5 − оценка) / 4      — больше стресс → меньше баллов
 *
 * Это не медицинский показатель, а сводка ваших собственных ответов.
 */
export const RECOVERY_WEIGHTS = Object.freeze({
  sleep: { label: 'Сон', max: 30 },
  sleepQuality: { label: 'Качество сна', max: 20 },
  energy: { label: 'Энергия', max: 20 },
  soreness: { label: 'Мышечная усталость', max: 15 },
  stress: { label: 'Стресс', max: 15 },
});

export function recoveryScore({ sleepHours, sleepQuality, energy, soreness, stress }) {
  const scale = (v) => clamp((Number(v) - 1) / 4, 0, 1);
  const inverse = (v) => clamp((5 - Number(v)) / 4, 0, 1);
  const normalized = {
    sleep: clamp((Number(sleepHours) || 0) / 8, 0, 1),
    sleepQuality: scale(sleepQuality),
    energy: scale(energy),
    soreness: inverse(soreness),
    stress: inverse(stress),
  };
  const parts = Object.entries(RECOVERY_WEIGHTS).map(([key, { label, max }]) => ({
    key,
    label,
    max,
    value: Math.round(normalized[key] * max * 10) / 10,
  }));
  const score = Math.round(parts.reduce((sum, p) => sum + p.value, 0));
  return { score: clamp(score, 0, 100), parts };
}

/** Длительность сна в часах по времени отхода ко сну и пробуждения ('23:30' → '07:15'). */
export function sleepHoursBetween(bedTime, wakeTime) {
  const bed = timeToMinutes(bedTime);
  const wake = timeToMinutes(wakeTime);
  if (bed === null || wake === null) return null;
  let diff = wake - bed;
  if (diff <= 0) diff += 24 * 60;
  return Math.round((diff / 60) * 100) / 100;
}

/* ------------------------------------------------------------------ */
/* Цикл (календарный, ориентировочный расчёт)                          */
/* ------------------------------------------------------------------ */

export const CYCLE_PHASES = Object.freeze({
  menstrual: 'Менструальная',
  follicular: 'Фолликулярная',
  ovulatory: 'Овуляторная',
  luteal: 'Лютеиновая',
});

/**
 * Ориентировочная фаза по календарю:
 * день 1…длительность менструации — менструальная;
 * «овуляторное окно» — около (длина цикла − 14) ± 1 день;
 * между ними — фолликулярная, после — лютеиновая.
 * Это усреднённая календарная модель, а не определение овуляции.
 */
export function phaseForDay(day, cycleLength = 28, periodLength = 5) {
  const ovulationDay = Math.max(periodLength + 2, cycleLength - 14);
  if (day <= periodLength) return 'menstrual';
  if (day >= ovulationDay - 1 && day <= ovulationDay + 1) return 'ovulatory';
  if (day < ovulationDay - 1) return 'follicular';
  return 'luteal';
}

/**
 * День цикла и фаза для даты.
 * starts — известные даты начала циклов (история), используется последняя ≤ date.
 */
export function cycleInfo({ starts = [], cycleLength = 28, periodLength = 5 }, date = today()) {
  const past = starts.filter((s) => s <= date).sort();
  const last = past.at(-1);
  if (!last) return null;
  const day = daysBetween(last, date) + 1;
  const nextStart = addDays(last, cycleLength);
  return {
    day,
    cycleLength,
    periodLength,
    lastStart: last,
    nextStart,
    isLate: day > cycleLength,
    phase: day > cycleLength ? 'luteal' : phaseForDay(day, cycleLength, periodLength),
    ovulationDay: Math.max(periodLength + 2, cycleLength - 14),
  };
}

/* ------------------------------------------------------------------ */
/* Добавки                                                             */
/* ------------------------------------------------------------------ */

export const SUPPLEMENT_FREQUENCIES = Object.freeze({
  daily: 'Каждый день',
  everyOtherDay: 'Через день',
  weekdays: 'По будням',
  weekly: 'Раз в неделю',
});

/** Запланирован ли приём добавки на дату. */
export function isSupplementScheduled(supplement, iso) {
  const start = supplement.startDate ?? iso;
  if (iso < start) return false;
  switch (supplement.frequency) {
    case 'everyOtherDay':
      return daysBetween(start, iso) % 2 === 0;
    case 'weekdays':
      return isoWeekday(iso) <= 5;
    case 'weekly':
      return isoWeekday(iso) === isoWeekday(start);
    default:
      return true;
  }
}

/**
 * Регулярность приёма (adherence) за последние `days` дней:
 * принято / запланировано × 100.
 */
export function supplementAdherence(supplements, log, days, end = today()) {
  let scheduled = 0;
  let taken = 0;
  for (let i = 0; i < days; i += 1) {
    const iso = addDays(end, -i);
    const takenIds = new Set(log[iso] ?? []);
    for (const s of supplements) {
      if (!isSupplementScheduled(s, iso)) continue;
      scheduled += 1;
      if (takenIds.has(s.id)) taken += 1;
    }
  }
  return { scheduled, taken, percent: scheduled ? Math.round((taken / scheduled) * 100) : null };
}

/* ------------------------------------------------------------------ */
/* Питание                                                             */
/* ------------------------------------------------------------------ */

export function nutritionTotals(entries) {
  return entries.reduce(
    (acc, e) => ({
      kcal: acc.kcal + (Number(e.kcal) || 0),
      protein: acc.protein + (Number(e.protein) || 0),
      fat: acc.fat + (Number(e.fat) || 0),
      carbs: acc.carbs + (Number(e.carbs) || 0),
    }),
    { kcal: 0, protein: 0, fat: 0, carbs: 0 },
  );
}

/** Пересчёт значений «на 100 г» на порцию. */
export function scalePer100(per100, grams) {
  const k = (Number(grams) || 0) / 100;
  const r = (v) => (Number.isFinite(v) ? Math.round(v * k * 10) / 10 : null);
  return { kcal: r(per100.kcal), protein: r(per100.protein), fat: r(per100.fat), carbs: r(per100.carbs) };
}
