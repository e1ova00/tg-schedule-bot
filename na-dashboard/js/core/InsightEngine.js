/**
 * InsightEngine — rule-based анализ локальных записей пользователя.
 *
 * Это НЕ искусственный интеллект и не медицинский сервис: набор понятных правил
 * (средние значения, сравнение групп, коэффициент корреляции) с порогами
 * минимального объёма данных. Если данных мало — движок честно говорит об этом.
 * Формулировки описывают связь («в вашей истории», «в среднем»), а не причину.
 */
import { addDays, formatHours, minutesToTime, today, greetingFor, isWithinLastDays } from '../utils/date.js';
import { cycleInfo, mean, pearson, percentChange, scoreWorkouts, exerciseStats } from '../utils/calculations.js';
import { fmt } from '../utils/format.js';

export const NOT_ENOUGH_DATA =
  'Пока недостаточно данных. Продолжайте записывать тренировки и самочувствие — после накопления истории здесь появятся персональные закономерности.';

export const WINDOW_NOT_ENOUGH =
  'Продолжайте записывать тренировки, чтобы определить ваше оптимальное тренировочное окно.';

export const DISCLAIMER = 'Наблюдения основаны только на ваших записях и не являются медицинскими рекомендациями.';

const MIN = Object.freeze({
  workoutsForWindow: 8,
  perWindow: 3,
  groupSize: 3,
  moodGroup: 4,
  correlationPairs: 6,
});

export class InsightEngine {
  #storage;

  constructor(storage) {
    this.#storage = storage;
  }

  #data() {
    const s = this.#storage;
    return {
      profile: s.get('profile') ?? {},
      workouts: s.list('workouts'),
      recovery: s.list('recovery'),
      mood: s.list('mood'),
      cycle: s.get('cycle'),
    };
  }

  /* ------------------------------------------------------------ */
  /* Daily Insight                                                 */
  /* ------------------------------------------------------------ */

  dailyInsight(now = new Date()) {
    const data = this.#data();
    const name = data.profile.name?.trim();
    const greeting = name ? `${greetingFor(now)}, ${name}` : greetingFor(now);
    const lines = [];

    const todayRecovery = data.recovery.find((r) => r.date === today());
    if (todayRecovery) {
      const energy = todayRecovery.energy >= 4 ? 'высокий' : todayRecovery.energy === 3 ? 'средний' : 'невысокий';
      lines.push({
        kind: 'recovery',
        text: `Индекс восстановления сегодня — ${todayRecovery.score}. Вы спали ${formatHours(todayRecovery.sleepHours)} и отметили ${energy} уровень энергии.`,
      });
    }

    const enough =
      data.workouts.length >= 3 || data.recovery.length >= 3 || data.mood.length >= 3;

    const scored = scoreWorkouts(data.workouts);
    const window = this.optimalWindow(scored);
    if (window.status === 'ok') {
      lines.push({ kind: 'window', text: `Оптимальное окно тренировки: ${window.label}.` });
    }

    const top = this.observations(data, scored)[0];
    if (top) lines.push({ kind: top.category, text: top.text });

    if (!todayRecovery && enough) {
      lines.push({ kind: 'hint', text: 'Отметьте утренний check-in в разделе «Самочувствие», чтобы увидеть индекс восстановления за сегодня.' });
    }

    return {
      greeting,
      enough,
      lines,
      window,
      emptyText: NOT_ENOUGH_DATA,
      disclaimer: DISCLAIMER,
    };
  }

  /* ------------------------------------------------------------ */
  /* Оптимальное время тренировки                                  */
  /* ------------------------------------------------------------ */

  /**
   * Перебираем двухчасовые окна с шагом 30 минут и сравниваем среднюю
   * относительную эффективность тренировок внутри окна и вне его.
   * Окно должно содержать не меньше трёх тренировок и не меньше половины
   * тренировок самого «populated» окна — чтобы случайный выброс не победил.
   */
  optimalWindow(scored = scoreWorkouts(this.#storage.list('workouts'))) {
    if (scored.length < MIN.workoutsForWindow) {
      return { status: 'insufficient', message: WINDOW_NOT_ENOUGH, sample: scored.length };
    }
    const candidates = [];
    for (let start = 5 * 60; start <= 21 * 60; start += 30) {
      const inside = scored.filter((s) => s.minutes >= start && s.minutes < start + 120);
      const outside = scored.filter((s) => !(s.minutes >= start && s.minutes < start + 120));
      if (inside.length < MIN.perWindow || outside.length < 2) continue;
      candidates.push({
        start,
        n: inside.length,
        mean: mean(inside.map((s) => s.performance)),
        outside: mean(outside.map((s) => s.performance)),
      });
    }
    if (!candidates.length) {
      return { status: 'insufficient', message: WINDOW_NOT_ENOUGH, sample: scored.length };
    }
    const maxN = Math.max(...candidates.map((c) => c.n));
    const eligible = candidates.filter((c) => c.n >= Math.max(MIN.perWindow, Math.ceil(maxN / 2)));
    let best = eligible.reduce((a, b) => (b.mean > a.mean ? b : a));
    // Выравниваем начало окна по самой ранней тренировке внутри него (кратно 30 мин),
    // если от этого средняя эффективность не падает.
    const inBest = scored.filter((s) => s.minutes >= best.start && s.minutes < best.start + 120);
    const aligned = Math.floor(Math.min(...inBest.map((s) => s.minutes)) / 30) * 30;
    const shifted = candidates.find((c) => c.start === aligned);
    if (shifted && shifted.mean >= best.mean - 1e-9) best = shifted;
    const diffPercent = percentChange(best.outside, best.mean);
    if (diffPercent < 1.5) {
      return {
        status: 'flat',
        message: 'В вашей истории эффективность тренировок пока примерно одинакова в разное время суток.',
        sample: scored.length,
      };
    }
    const label = `${minutesToTime(best.start)}–${minutesToTime(best.start + 120)}`;
    return {
      status: 'ok',
      label,
      start: minutesToTime(best.start),
      end: minutesToTime(best.start + 120),
      diffPercent,
      sample: best.n,
      message: `В вашей истории средняя эффективность тренировок в это время выше примерно на ${fmt(diffPercent, 1)}% (по ${best.n} тренировкам).`,
    };
  }

  /* ------------------------------------------------------------ */
  /* Наблюдения и корреляции                                       */
  /* ------------------------------------------------------------ */

  /** Все наблюдения, отсортированные по «силе» (для показа лучших первыми). */
  observations(data = this.#data(), scored = scoreWorkouts(data.workouts)) {
    const recoveryByDate = new Map(data.recovery.map((r) => [r.date, r]));
    const list = [
      this.#strengthTrend(data.workouts),
      this.#sleepVsPerformance(scored, recoveryByDate),
      this.#recoveryVsPerformance(scored, recoveryByDate),
      this.#timeOfDay(scored),
      this.#moodVsTraining(data.mood, data.workouts),
      this.#moodWeekTrend(data.mood),
      this.#sleepAverage(data.recovery),
      ...this.cycleObservations(data, scored),
    ].filter(Boolean);
    return list.sort((a, b) => b.weight - a.weight);
  }

  #strengthTrend(workouts) {
    const recent = workouts.filter((w) => isWithinLastDays(w.date, 28));
    const byExercise = new Map();
    for (const w of [...recent].sort((a, b) => a.date.localeCompare(b.date))) {
      for (const ex of w.exercises ?? []) {
        const stats = exerciseStats(ex);
        if (!stats || stats.maxWeight <= 0) continue;
        const entry = byExercise.get(ex.exerciseId) ?? { name: ex.name, points: [] };
        entry.points.push(stats.maxWeight);
        byExercise.set(ex.exerciseId, entry);
      }
    }
    // Предпочитаем базовые упражнения с заметным рабочим весом: у лёгких весов
    // шаг блина 2,5 кг даёт непропорционально большие проценты.
    let best = null;
    for (const { name, points } of byExercise.values()) {
      if (points.length < 3) continue;
      const change = percentChange(points[0], points.at(-1));
      if (change === null || change < 3) continue;
      const score = change * (points[0] >= 20 ? 1 : 0.3);
      if (!best || score > best.score) best = { name, change, score };
    }
    if (!best) return null;
    return {
      id: 'strength-trend',
      category: 'training',
      weight: 60 + Math.min(best.change, 30),
      text: `За последние 4 недели рабочий вес в упражнении «${best.name}» вырос на ${fmt(best.change)}%.`,
    };
  }

  #sleepVsPerformance(scored, recoveryByDate) {
    const pairs = scored
      .map((s) => ({ perf: s.performance, sleep: recoveryByDate.get(s.date)?.sleepHours }))
      .filter((p) => Number.isFinite(p.sleep));
    const long = pairs.filter((p) => p.sleep >= 7).map((p) => p.perf);
    const short = pairs.filter((p) => p.sleep < 7).map((p) => p.perf);
    if (long.length < MIN.groupSize || short.length < MIN.groupSize) return null;
    const diff = percentChange(mean(short), mean(long));
    if (diff >= 1.5) {
      return {
        id: 'sleep-performance',
        category: 'recovery',
        weight: 70 + diff,
        text: `Ваши лучшие силовые результаты чаще наблюдаются после сна более 7 часов: в среднем на ${fmt(diff, 1)}% выше, чем после короткого сна.`,
      };
    }
    if (diff <= -1.5) {
      return {
        id: 'sleep-performance',
        category: 'recovery',
        weight: 30,
        text: 'В вашей истории продолжительность сна пока не связана с более высокими результатами тренировок.',
      };
    }
    return null;
  }

  #recoveryVsPerformance(scored, recoveryByDate) {
    const pairs = scored
      .map((s) => [recoveryByDate.get(s.date)?.score, s.performance])
      .filter(([r]) => Number.isFinite(r));
    if (pairs.length < MIN.correlationPairs) return null;
    const r = pearson(pairs.map((p) => p[0]), pairs.map((p) => p[1]));
    if (r === null || Math.abs(r) < 0.3) return null;
    const strength = Math.abs(r) >= 0.5 ? 'заметно' : 'умеренно';
    const direction = r > 0 ? 'с более сильными' : 'с менее сильными';
    return {
      id: 'recovery-performance',
      category: 'recovery',
      weight: 50 + Math.abs(r) * 20,
      text: `В вашей истории более высокий индекс восстановления ${strength} связан ${direction} тренировками (r = ${fmt(r, 1)}).`,
    };
  }

  #timeOfDay(scored) {
    const morning = scored.filter((s) => s.minutes < 12 * 60).map((s) => s.performance);
    const evening = scored.filter((s) => s.minutes >= 16 * 60).map((s) => s.performance);
    if (morning.length < MIN.groupSize || evening.length < MIN.groupSize) return null;
    const diff = percentChange(mean(morning), mean(evening));
    if (Math.abs(diff) < 1.5) return null;
    const better = diff > 0 ? 'вечерние' : 'утренние';
    return {
      id: 'time-of-day',
      category: 'progress',
      weight: 45 + Math.abs(diff),
      text: `В среднем ${better} тренировки в вашей истории получаются эффективнее примерно на ${fmt(Math.abs(diff), 1)}%.`,
    };
  }

  #moodVsTraining(mood, workouts) {
    const workoutDates = new Set(workouts.map((w) => w.date));
    const byDate = new Map();
    for (const m of mood) {
      const list = byDate.get(m.date) ?? [];
      list.push(m.value);
      byDate.set(m.date, list);
    }
    const on = [];
    const off = [];
    for (const [date, values] of byDate) (workoutDates.has(date) ? on : off).push(mean(values));
    if (on.length < MIN.moodGroup || off.length < MIN.moodGroup) return null;
    const a = mean(on);
    const b = mean(off);
    if (Math.abs(a - b) < 0.3) return null;
    const word = a > b ? 'выше' : 'ниже';
    return {
      id: 'mood-training',
      category: 'mood',
      weight: 55 + Math.abs(a - b) * 10,
      text: `В дни тренировок средняя оценка самочувствия в вашей истории ${word}: ${fmt(a, 1)} против ${fmt(b, 1)} из 7.`,
    };
  }

  #moodWeekTrend(mood) {
    const end = today();
    const thisWeek = mood.filter((m) => isWithinLastDays(m.date, 7, end)).map((m) => m.value);
    const prevWeek = mood.filter((m) => isWithinLastDays(m.date, 7, addDays(end, -7))).map((m) => m.value);
    if (thisWeek.length < 3 || prevWeek.length < 3) return null;
    const a = mean(thisWeek);
    const b = mean(prevWeek);
    const text =
      Math.abs(a - b) < 0.2
        ? `Средняя оценка самочувствия за эту неделю примерно такая же, как на предыдущей (${fmt(a, 1)} из 7).`
        : `Средняя оценка самочувствия за эту неделю ${a > b ? 'выше' : 'ниже'} предыдущей: ${fmt(a, 1)} против ${fmt(b, 1)} из 7.`;
    return { id: 'mood-week', category: 'mood', weight: 40 + Math.abs(a - b) * 10, text };
  }

  #sleepAverage(recovery) {
    const week = recovery.filter((r) => isWithinLastDays(r.date, 7)).map((r) => r.sleepHours);
    if (week.length < 3) return null;
    return {
      id: 'sleep-average',
      category: 'recovery',
      weight: 20,
      text: `За последние 7 дней вы спали в среднем ${formatHours(mean(week))}.`,
    };
  }

  /** Наблюдения по циклу — только сопоставление с собственной историей. */
  cycleObservations(data = this.#data(), scored = scoreWorkouts(data.workouts)) {
    const { profile, cycle, recovery } = data;
    if (profile.sex !== 'female' || !cycle?.starts?.length) return [];
    const phaseOf = (date) => cycleInfo(cycle, date)?.phase ?? null;
    const result = [];

    const energyAll = recovery.map((r) => r.energy).filter(Number.isFinite);
    const energyMenstrual = recovery.filter((r) => phaseOf(r.date) === 'menstrual').map((r) => r.energy);
    if (energyAll.length >= 10 && energyMenstrual.length >= MIN.groupSize) {
      const diff = mean(energyMenstrual) - mean(energyAll);
      if (Math.abs(diff) >= 0.3) {
        result.push({
          id: 'cycle-energy',
          category: 'cycle',
          weight: 50 + Math.abs(diff) * 10,
          text: `В вашей истории уровень энергии в первые дни цикла обычно немного ${diff < 0 ? 'ниже' : 'выше'} среднего.`,
        });
      }
    }

    const follicular = scored.filter((s) => phaseOf(s.date) === 'follicular').map((s) => s.performance);
    const luteal = scored.filter((s) => phaseOf(s.date) === 'luteal').map((s) => s.performance);
    if (follicular.length >= MIN.groupSize && luteal.length >= MIN.groupSize) {
      const diff = percentChange(mean(luteal), mean(follicular));
      if (Math.abs(diff) >= 1.5) {
        result.push({
          id: 'cycle-performance',
          category: 'cycle',
          weight: 45 + Math.abs(diff),
          text: `В вашей истории силовые результаты в фолликулярной фазе в среднем ${diff > 0 ? 'выше' : 'ниже'}, чем в лютеиновой, примерно на ${fmt(Math.abs(diff), 1)}%.`,
        });
      }
    }
    return result;
  }
}
