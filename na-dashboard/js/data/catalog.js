/**
 * Статические справочники приложения: мышечные группы, базовые упражнения,
 * встроенные шаблоны тренировок, справочники для форм.
 */

export const MUSCLE_GROUPS = Object.freeze([
  { id: 'quads', label: 'Квадрицепсы' },
  { id: 'glutes', label: 'Ягодицы' },
  { id: 'hamstrings', label: 'Задняя поверхность бедра' },
  { id: 'back', label: 'Спина' },
  { id: 'chest', label: 'Грудь' },
  { id: 'shoulders', label: 'Плечи' },
  { id: 'biceps', label: 'Бицепс' },
  { id: 'triceps', label: 'Трицепс' },
  { id: 'core', label: 'Кор / пресс' },
  { id: 'calves', label: 'Икры' },
  { id: 'fullbody', label: 'Всё тело' },
]);

export const muscleLabel = (id) => MUSCLE_GROUPS.find((m) => m.id === id)?.label ?? 'Другое';

export const BUILTIN_EXERCISES = Object.freeze([
  { id: 'b-squat', name: 'Присед', muscle: 'quads' },
  { id: 'b-bench', name: 'Жим лёжа', muscle: 'chest' },
  { id: 'b-deadlift', name: 'Становая тяга', muscle: 'back' },
  { id: 'b-rdl', name: 'Румынская тяга', muscle: 'hamstrings' },
  { id: 'b-hip-thrust', name: 'Ягодичный мост', muscle: 'glutes' },
  { id: 'b-lunge', name: 'Выпады', muscle: 'quads' },
  { id: 'b-bulgarian', name: 'Болгарские выпады', muscle: 'glutes' },
  { id: 'b-leg-press', name: 'Жим ногами', muscle: 'quads' },
  { id: 'b-lat-pulldown', name: 'Тяга верхнего блока', muscle: 'back' },
  { id: 'b-seated-row', name: 'Тяга горизонтального блока', muscle: 'back' },
  { id: 'b-pull-up', name: 'Подтягивания', muscle: 'back' },
  { id: 'b-ohp', name: 'Жим над головой', muscle: 'shoulders' },
  { id: 'b-curl', name: 'Сгибание рук', muscle: 'biceps' },
  { id: 'b-triceps-ext', name: 'Разгибание рук', muscle: 'triceps' },
]);

export const BUILTIN_TEMPLATES = Object.freeze([
  {
    id: 't-lower',
    name: 'Lower Body',
    exerciseIds: ['b-squat', 'b-rdl', 'b-hip-thrust', 'b-bulgarian', 'b-leg-press'],
  },
  {
    id: 't-upper',
    name: 'Upper Body',
    exerciseIds: ['b-bench', 'b-lat-pulldown', 'b-seated-row', 'b-ohp', 'b-curl', 'b-triceps-ext'],
  },
  {
    id: 't-full',
    name: 'Full Body',
    exerciseIds: ['b-squat', 'b-bench', 'b-seated-row', 'b-hip-thrust', 'b-ohp'],
  },
]);

/** Категории wger → русские названия и ближайшая мышечная группа. */
export const WGER_CATEGORIES = Object.freeze({
  8: { label: 'Руки', muscle: 'biceps' },
  9: { label: 'Ноги', muscle: 'quads' },
  10: { label: 'Пресс', muscle: 'core' },
  11: { label: 'Грудь', muscle: 'chest' },
  12: { label: 'Спина', muscle: 'back' },
  13: { label: 'Плечи', muscle: 'shoulders' },
  14: { label: 'Икры', muscle: 'calves' },
  15: { label: 'Кардио', muscle: 'fullbody' },
});

export const BODY_METRICS = Object.freeze([
  { id: 'bodyWeight', label: 'Вес тела', unit: 'кг' },
  { id: 'waist', label: 'Талия', unit: 'см' },
  { id: 'hips', label: 'Бёдра (обхват)', unit: 'см' },
  { id: 'chest', label: 'Грудь', unit: 'см' },
  { id: 'thigh', label: 'Бедро', unit: 'см' },
  { id: 'arm', label: 'Рука', unit: 'см' },
]);

export const CARDIO_TYPES = Object.freeze(['Бег', 'Ходьба', 'Велосипед', 'Эллипс', 'Плавание', 'Гребля', 'Другое']);

export const MOOD_LEVELS = Object.freeze([
  'Очень плохо',
  'Плохо',
  'Скорее плохо',
  'Нейтрально',
  'Скорее хорошо',
  'Хорошо',
  'Очень хорошо',
]);

export const MOOD_FACTORS = Object.freeze([
  'Здоровье', 'Тренировки', 'Сон', 'Работа', 'Учёба', 'Семья',
  'Отношения', 'Друзья', 'Финансы', 'Питание', 'Другое',
]);

export const GOALS = Object.freeze({
  muscle: 'Набор мышечной массы',
  fatloss: 'Снижение веса',
  maintain: 'Поддержание формы',
  strength: 'Увеличение силы',
  health: 'Общее здоровье',
});

export const ACTIVITY_LEVELS = Object.freeze({
  sedentary: 'Сидячая',
  moderate: 'Умеренная',
  high: 'Высокая',
});

export const EXPERIENCE_LEVELS = Object.freeze({
  beginner: 'Новичок',
  intermediate: 'Средний',
  advanced: 'Продвинутый',
});

export const WORKOUT_TIME_PREFS = Object.freeze({
  morning: 'Утро',
  day: 'День',
  evening: 'Вечер',
  any: 'По-разному',
});

export const SUPPLEMENT_SLOTS = Object.freeze({
  morning: { label: 'Утром', time: '08:00' },
  day: { label: 'Днём', time: '13:00' },
  evening: { label: 'Вечером', time: '20:00' },
  custom: { label: 'Своё время', time: null },
});

export const MEALS = Object.freeze(['Завтрак', 'Обед', 'Ужин', 'Перекус']);
