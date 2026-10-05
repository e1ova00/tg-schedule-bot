/**
 * Генератор демо-данных: «Анна», 24 года, ~7 недель истории.
 * Генерация детерминированная (seeded PRNG), даты — относительно сегодняшнего дня,
 * поэтому графики и Daily Insight всегда выглядят свежо.
 *
 * В данные заложены закономерности, которые находит InsightEngine:
 *  - вечерние тренировки и сон 7+ часов → результаты чуть выше;
 *  - первые дни цикла → энергия немного ниже;
 *  - в дни тренировок самочувствие чуть выше.
 */
import { addDays, isoWeekday, minutesToTime, today } from './date.js';
import { detectPRs, recoveryScore, sleepHoursBetween, workoutVolume, cycleInfo } from './calculations.js';
import { BUILTIN_EXERCISES, BUILTIN_TEMPLATES } from '../data/catalog.js';

function mulberry32(seed) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const DAYS = 49;

export function createDemoData(now = new Date()) {
  const rand = mulberry32(20261005);
  const r = (min, max) => min + rand() * (max - min);
  const pick = (list) => list[Math.floor(rand() * list.length)];
  const end = today();
  const start = addDays(end, -(DAYS - 1));
  let seq = 0;
  const id = (p) => `demo_${p}_${(seq += 1).toString(36)}`;
  const at = (date, time) => {
    const [y, m, d] = date.split('-').map(Number);
    const [hh, mm] = time.split(':').map(Number);
    return new Date(y, m - 1, d, hh, mm).toISOString();
  };

  const profile = {
    name: 'Анна',
    sex: 'female',
    age: 24,
    height: 168,
    weight: 61,
    goal: 'muscle',
    occupation: 'Дизайнер интерфейсов',
    activity: 'moderate',
    workoutsPerWeek: 3,
    experience: 'intermediate',
    sleepTime: '23:30',
    wakeTime: '07:30',
    preferredWorkoutTime: 'evening',
    mealsPerDay: 4,
    createdAt: at(start, '09:00'),
    demo: true,
  };

  const cycle = { starts: [addDays(end, -65), addDays(end, -37), addDays(end, -9)], cycleLength: 28, periodLength: 5 };
  const phaseOn = (date) => cycleInfo(cycle, date)?.phase;

  /* ---------------- Сон и восстановление ---------------- */
  const recovery = [];
  const recoveryByDate = new Map();
  for (let i = 0; i < DAYS; i += 1) {
    const date = addDays(start, i);
    const isToday = date === end;
    const weekend = isoWeekday(date) >= 6;
    const wakeMin = (weekend ? 8 * 60 + 30 : 7 * 60 + 20) + Math.round(r(-20, 20));
    const sleep = isToday ? 7.8 : Math.round(Math.min(9, Math.max(5.6, 7.2 + r(-1.3, 1.1) + (weekend ? 0.4 : 0))) * 12) / 12;
    const bedMin = wakeMin - Math.round(sleep * 60);
    const bedTime = minutesToTime(bedMin);
    const wakeTime = minutesToTime(wakeMin);
    const menstrual = phaseOn(date) === 'menstrual';
    const quality = isToday ? 4 : Math.round(Math.min(5, Math.max(1, 2.2 + (sleep - 6) * 0.9 + r(-0.6, 0.6))));
    const energy = isToday ? 5 : Math.round(Math.min(5, Math.max(1, 2.4 + (sleep - 6.5) * 0.8 + r(-0.6, 0.8) - (menstrual ? 1 : 0))));
    const stress = isToday ? 2 : Math.round(Math.min(5, Math.max(1, (weekend ? 1.6 : 2.6) + r(-0.8, 1.2))));
    const record = {
      id: id('rec'),
      date,
      bedTime,
      wakeTime,
      sleepHours: sleepHoursBetween(bedTime, wakeTime),
      sleepQuality: quality,
      energy,
      soreness: 2,
      stress,
    };
    recovery.push(record);
    recoveryByDate.set(date, record);
  }

  /* ---------------- Тренировки ---------------- */
  const ex = Object.fromEntries(BUILTIN_EXERCISES.map((e) => [e.id, e]));
  const base = {
    'b-squat': [45, 0.035, 8], 'b-rdl': [40, 0.04, 10], 'b-hip-thrust': [70, 0.06, 10], 'b-bulgarian': [12, 0.04, 10],
    'b-leg-press': [90, 0.04, 12], 'b-bench': [30, 0.025, 8], 'b-lat-pulldown': [35, 0.025, 10], 'b-seated-row': [32, 0.025, 10],
    'b-ohp': [20, 0.02, 8], 'b-curl': [8, 0.02, 12], 'b-triceps-ext': [10, 0.02, 12], 'ex_demo_abduction': [35, 0.03, 15],
  };
  const customExercise = { id: 'ex_demo_abduction', name: 'Отведение бедра в тренажёре', muscle: 'glutes', source: 'custom', createdAt: at(start, '10:00') };
  const plan = { 1: 't-lower', 3: 't-upper', 5: 't-full', 6: 't-lower' };
  const workouts = [];

  for (let i = 0; i < DAYS - 1; i += 1) {
    const date = addDays(start, i);
    const wd = isoWeekday(date);
    const templateId = plan[wd];
    if (!templateId || (wd === 6 && rand() < 0.55)) continue;
    const template = BUILTIN_TEMPLATES.find((t) => t.id === templateId);
    const evening = rand() < 0.68;
    const startTime = evening ? pick(['17:30', '17:45', '18:00', '18:15', '18:30', '18:45']) : pick(['07:45', '08:00', '08:15', '12:30']);
    const sleep = recoveryByDate.get(date).sleepHours;
    const weeks = i / 7;
    const factor = 1 + (sleep >= 7 ? 0.035 : -0.03) + (evening ? 0.025 : -0.02) + (phaseOn(date) === 'menstrual' ? -0.02 : 0) + r(-0.012, 0.012);
    const exerciseIds = templateId === 't-lower' ? [...template.exerciseIds.slice(0, 4), 'ex_demo_abduction'] : template.exerciseIds;

    const exercises = exerciseIds.map((eid) => {
      const [w0, rate, reps] = base[eid];
      const target = w0 * (1 + rate * weeks) * factor;
      const weight = Math.max(2, Math.round(target / 2.5) * 2.5);
      const sets = Array.from({ length: eid === 'b-hip-thrust' || eid === 'b-squat' ? 4 : 3 }, (_, s) => ({
        weight,
        reps: Math.max(5, reps - (s === 2 ? 1 : 0) - (s === 3 ? 2 : 0)),
        rpe: Math.min(10, Math.round((7 + s * 0.5 + r(-0.3, 0.4)) * 2) / 2),
      }));
      const meta = eid === customExercise.id ? customExercise : ex[eid];
      return { exerciseId: eid, name: meta.name, muscle: meta.muscle, sets };
    });

    const startedAt = at(date, startTime);
    const workout = {
      id: id('wo'),
      name: template.name,
      date,
      startedAt,
      endedAt: new Date(new Date(startedAt).getTime() + r(55, 80) * 60000).toISOString(),
      exercises,
      note: rand() < 0.2 ? pick(['Отличная техника в приседе', 'Немного устала, но всё по плану', 'Добавить разминку для плеч']) : '',
      createdAt: startedAt,
    };
    workout.prs = detectPRs(workout, workouts);
    workout.volume = workoutVolume(workout);
    workouts.push(workout);

    // Мышечная усталость на следующий день после тренировки ног выше.
    const next = recoveryByDate.get(addDays(date, 1));
    if (next && next.date !== end) next.soreness = templateId === 't-lower' ? Math.round(r(3, 4.4)) : Math.round(r(2, 3.4));
  }

  // Красивый финальный PR в ягодичном мосте: 100 кг на последней тренировке ног.
  const lastLower = [...workouts].reverse().find((w) => w.exercises.some((e) => e.exerciseId === 'b-hip-thrust'));
  if (lastLower) {
    const ht = lastLower.exercises.find((e) => e.exerciseId === 'b-hip-thrust');
    ht.sets[0].weight = 100;
    ht.sets[0].reps = 8;
    const prior = workouts.filter((w) => w.startedAt < lastLower.startedAt);
    lastLower.prs = detectPRs(lastLower, prior);
    lastLower.volume = workoutVolume(lastLower);
  }

  for (const rec of recovery) rec.score = recoveryScore(rec).score;

  /* ---------------- Кардио ---------------- */
  const cardio = [];
  for (let i = 0; i < DAYS - 1; i += 1) {
    const date = addDays(start, i);
    const wd = isoWeekday(date);
    if (wd !== 2 && wd !== 7) continue;
    if (rand() < 0.2) continue;
    const type = wd === 7 ? pick(['Ходьба', 'Велосипед']) : pick(['Бег', 'Эллипс']);
    const duration = Math.round(type === 'Ходьба' ? r(40, 60) : r(25, 40));
    const speed = { Бег: 9.5, Эллипс: 0, Ходьба: 5.3, Велосипед: 17 }[type];
    cardio.push({
      id: id('car'),
      date,
      type,
      duration,
      distance: speed ? Math.round(((duration / 60) * speed * (1 + i / 400)) * 10) / 10 : null,
      avgHr: Math.round(type === 'Ходьба' ? r(105, 118) : r(138, 152) - i * 0.12),
      intensity: Math.round(type === 'Ходьба' ? r(3, 4.5) : r(5, 7.5)),
      createdAt: at(date, '09:00'),
    });
  }

  /* ---------------- Самочувствие ---------------- */
  const workoutDates = new Set(workouts.map((w) => w.date));
  const factorsPool = ['Тренировки', 'Сон', 'Работа', 'Учёба', 'Семья', 'Друзья', 'Питание', 'Здоровье', 'Отношения', 'Финансы'];
  const mood = [];
  for (let i = 0; i < DAYS; i += 1) {
    const date = addDays(start, i);
    const rec = recoveryByDate.get(date);
    const trained = workoutDates.has(date);
    const baseMood = 3.6 + (rec.energy - 3) * 0.55 - (rec.stress - 2.5) * 0.35 + (trained ? 0.55 : 0) + i * 0.012;
    const clampMood = (v) => Math.max(1, Math.min(7, Math.round(v)));
    if (rand() < 0.6 || date === end) {
      const factors = [...new Set([trained ? 'Тренировки' : pick(factorsPool), pick(factorsPool)])].slice(0, rand() < 0.5 ? 1 : 2);
      mood.push({ id: id('mood'), at: at(date, pick(['09:10', '12:40', '15:20'])), date, kind: 'now', value: clampMood(baseMood + r(-0.8, 0.8)), factors, note: '' });
    }
    if (date !== end && rand() < 0.85) {
      const factors = [...new Set([trained ? 'Тренировки' : 'Работа', pick(factorsPool)])];
      mood.push({ id: id('mood'), at: at(date, '22:00'), date, kind: 'day', value: clampMood(baseMood + 0.3 + r(-0.7, 0.7)), factors, note: rand() < 0.1 ? 'Хороший продуктивный день' : '' });
    }
  }

  /* ---------------- Питание и вода ---------------- */
  const foods = {
    Завтрак: [['Овсянка с ягодами', 380, 13, 9, 62, 300], ['Омлет из 3 яиц с хлебом', 420, 26, 24, 22, 250], ['Греческий йогурт с гранолой', 350, 22, 10, 42, 250]],
    Обед: [['Курица, рис и овощи', 610, 45, 14, 72, 400], ['Боул с лососем', 640, 38, 24, 64, 380], ['Паста с индейкой', 590, 40, 16, 68, 380]],
    Ужин: [['Творог с фруктами', 330, 30, 8, 30, 300], ['Говядина с гречкой', 560, 42, 18, 52, 350], ['Тофу с овощами и булгуром', 470, 26, 15, 55, 350]],
    Перекус: [['Протеиновый батончик', 210, 20, 7, 18, 60], ['Банан и орехи', 260, 6, 14, 30, 120], ['Кефир 1%', 120, 9, 3, 12, 300]],
  };
  const nutritionLog = [];
  const water = {};
  for (let i = 0; i < DAYS; i += 1) {
    const date = addDays(start, i);
    const isToday = date === end;
    const meals = isToday ? ['Завтрак', 'Обед', 'Перекус'] : ['Завтрак', 'Обед', 'Перекус', 'Ужин'];
    for (const meal of meals) {
      if (!isToday && meal === 'Перекус' && rand() < 0.3) continue;
      const [name, kcal, protein, fat, carbs, grams] = pick(foods[meal]);
      const k = isToday ? 1 : r(0.9, 1.12);
      nutritionLog.push({
        id: id('food'),
        date,
        meal,
        name,
        grams: Math.round(grams * k),
        kcal: Math.round(kcal * k),
        protein: Math.round(protein * k * 10) / 10,
        fat: Math.round(fat * k * 10) / 10,
        carbs: Math.round(carbs * k * 10) / 10,
        source: 'manual',
        createdAt: at(date, '12:00'),
      });
    }
    water[date] = isToday ? 1250 : Math.round(r(1500, 2500) / 250) * 250;
  }

  /* ---------------- Замеры ---------------- */
  const measurements = [];
  for (let w = 0; w < 7; w += 1) {
    const date = addDays(start, w * 7 + 1);
    const add = (metric, value) => measurements.push({ id: id('m'), date, metric, value: Math.round(value * 10) / 10, createdAt: at(date, '08:00') });
    add('bodyWeight', 61.8 - w * 0.13 + r(-0.2, 0.2));
    if (w % 2 === 0) {
      add('waist', 69 - w * 0.25 + r(-0.2, 0.2));
      add('hips', 96 + w * 0.22 + r(-0.2, 0.2));
      add('thigh', 56 + w * 0.15);
      add('arm', 27 + w * 0.06);
      add('chest', 88 + r(-0.3, 0.3));
    }
  }

  /* ---------------- Добавки ---------------- */
  const supStart = start;
  const supplements = [
    { id: 'sup_demo_d3', name: 'Витамин D3', dose: '2000 МЕ', schedule: 'morning', time: '08:00', frequency: 'daily', startDate: supStart },
    { id: 'sup_demo_omega', name: 'Омега-3', dose: '1 капсула', schedule: 'day', time: '13:00', frequency: 'daily', startDate: supStart },
    { id: 'sup_demo_creatine', name: 'Креатин', dose: '5 г', schedule: 'custom', time: '18:30', frequency: 'daily', startDate: supStart },
    { id: 'sup_demo_mg', name: 'Магний', dose: '200 мг', schedule: 'evening', time: '21:30', frequency: 'everyOtherDay', startDate: supStart },
  ];
  const supplementLog = {};
  for (let i = 0; i < DAYS; i += 1) {
    const date = addDays(start, i);
    const isToday = date === end;
    const taken = [];
    for (const s of supplements) {
      if (s.frequency === 'everyOtherDay' && i % 2 === 1) continue;
      if (isToday && s.schedule !== 'morning') continue;
      if (rand() < 0.87) taken.push(s.id);
    }
    supplementLog[date] = taken;
  }

  return {
    profile,
    workouts,
    exercises: [customExercise],
    templates: [{ id: 'tpl_demo_glutes', name: 'Ягодицы + спина', exerciseIds: ['b-hip-thrust', 'b-rdl', 'b-bulgarian', 'b-lat-pulldown', 'b-seated-row'], createdAt: at(start, '10:00') }],
    activeWorkout: null,
    cardio,
    measurements,
    mood,
    recovery,
    nutritionGoals: { kcal: 2100, protein: 110, fat: 70, carbs: 250, water: 2000 },
    nutritionLog,
    water,
    supplements,
    supplementLog,
    cycle,
    labs: [],
    generatedAt: now.toISOString(),
  };
}
