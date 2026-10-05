/** Доступ к объединённому списку упражнений и шаблонов (встроенные + пользовательские). */
import { BUILTIN_EXERCISES, BUILTIN_TEMPLATES, MUSCLE_GROUPS } from './catalog.js';

export function allExercises(storage) {
  return [...BUILTIN_EXERCISES, ...storage.list('exercises')];
}

export function findExercise(storage, id) {
  return allExercises(storage).find((e) => e.id === id) ?? null;
}

export function allTemplates(storage) {
  return [...BUILTIN_TEMPLATES, ...storage.list('templates')];
}

/** Опции для <select> с группировкой по мышечным группам. */
export function exerciseOptions(storage) {
  const list = allExercises(storage);
  return MUSCLE_GROUPS.map((g) => ({
    group: g.label,
    options: list
      .filter((e) => e.muscle === g.id)
      .sort((a, b) => a.name.localeCompare(b.name, 'ru'))
      .map((e) => ({ value: e.id, label: e.name })),
  })).filter((g) => g.options.length);
}
