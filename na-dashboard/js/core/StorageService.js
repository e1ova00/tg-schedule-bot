/**
 * StorageService — единый слой хранения поверх localStorage.
 *
 * - Все ключи и значения по умолчанию описаны в одном месте (SCHEMA).
 * - Данные версионируются (schemaVersion) и мигрируются при обновлении структуры.
 * - Наружу отдаются копии (structuredClone), чтобы никто не мутировал кэш напрямую.
 * - При каждой записи генерируется событие 'change' с ключом — компоненты
 *   подписываются на него и перерисовываются.
 * - Если localStorage недоступен (приватный режим, квота) — работаем в памяти.
 */

export const SCHEMA_VERSION = 1;
const PREFIX = 'na.';

const SCHEMA = Object.freeze({
  profile: null,
  preferences: { theme: 'light' },
  workouts: [],
  activeWorkout: null,
  exercises: [], // пользовательские упражнения и добавленные из wger
  templates: [], // пользовательские шаблоны тренировок
  cardio: [],
  measurements: [],
  mood: [],
  recovery: [],
  nutritionGoals: null,
  nutritionLog: [],
  water: {}, // { 'YYYY-MM-DD': мл }
  supplements: [],
  supplementLog: {}, // { 'YYYY-MM-DD': [supplementId, …] }
  cycle: null, // { starts: [], cycleLength, periodLength }
  labs: [],
  layouts: {}, // { sectionKey: [{ id, type, size, isMinimized, settings }] }
});

export const STORAGE_KEYS = Object.freeze(Object.keys(SCHEMA));

/** Миграции: номер версии → функция, приводящая данные к этой версии. */
const MIGRATIONS = {
  1: () => {
    /* первая версия схемы — миграция не требуется */
  },
};

function createBackend() {
  try {
    const probe = `${PREFIX}__probe`;
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
    return window.localStorage;
  } catch {
    const memory = new Map();
    return {
      getItem: (k) => (memory.has(k) ? memory.get(k) : null),
      setItem: (k, v) => memory.set(k, String(v)),
      removeItem: (k) => memory.delete(k),
      key: (i) => [...memory.keys()][i] ?? null,
      get length() {
        return memory.size;
      },
    };
  }
}

let idSeq = 0;
export function createId(prefix = 'id') {
  idSeq = (idSeq + 1) % 1e6;
  return `${prefix}_${Date.now().toString(36)}${idSeq.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export class StorageService extends EventTarget {
  #backend;
  #cache = new Map();
  #persistent = true;

  constructor(backend = createBackend()) {
    super();
    this.#backend = backend;
    this.#persistent = backend === globalThis.localStorage;
    this.#migrate();
  }

  /** false — данные живут только до закрытия вкладки. */
  get isPersistent() {
    return this.#persistent;
  }

  #assertKey(key) {
    if (!(key in SCHEMA)) throw new Error(`StorageService: неизвестный ключ «${key}»`);
  }

  #migrate() {
    const raw = Number(this.#backend.getItem(`${PREFIX}schemaVersion`));
    const current = Number.isFinite(raw) && raw > 0 ? raw : 0;
    for (let v = current + 1; v <= SCHEMA_VERSION; v += 1) MIGRATIONS[v]?.(this);
    this.#backend.setItem(`${PREFIX}schemaVersion`, String(SCHEMA_VERSION));
  }

  get(key) {
    this.#assertKey(key);
    if (!this.#cache.has(key)) {
      let value = structuredClone(SCHEMA[key]);
      const raw = this.#backend.getItem(PREFIX + key);
      if (raw !== null) {
        try {
          value = JSON.parse(raw);
        } catch {
          console.warn(`StorageService: повреждённые данные «${key}», используется значение по умолчанию`);
        }
      }
      this.#cache.set(key, value);
    }
    return structuredClone(this.#cache.get(key));
  }

  set(key, value, { silent = false } = {}) {
    this.#assertKey(key);
    this.#cache.set(key, structuredClone(value));
    try {
      this.#backend.setItem(PREFIX + key, JSON.stringify(value));
    } catch (error) {
      console.error('StorageService: не удалось сохранить данные', error);
      this.dispatchEvent(new CustomEvent('error', { detail: { key, error } }));
    }
    if (!silent) this.dispatchEvent(new CustomEvent('change', { detail: { key } }));
  }

  update(key, updater) {
    const next = updater(this.get(key));
    this.set(key, next);
    return next;
  }

  /* --------- коллекции (массивы объектов с id) --------- */

  list(key) {
    const value = this.get(key);
    return Array.isArray(value) ? value : [];
  }

  add(key, item) {
    const record = { id: item.id ?? createId(key.slice(0, 3)), createdAt: new Date().toISOString(), ...item };
    this.update(key, (list) => [...(Array.isArray(list) ? list : []), record]);
    return record;
  }

  updateItem(key, id, patch) {
    this.update(key, (list) => list.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  removeItem(key, id) {
    this.update(key, (list) => list.filter((item) => item.id !== id));
  }

  /* --------- раскладка дашбордов --------- */

  getLayout(name) {
    return this.get('layouts')[name] ?? null;
  }

  saveLayout(name, layout) {
    const layouts = this.get('layouts');
    layouts[name] = layout;
    this.set('layouts', layouts);
  }

  /* --------- массовые операции --------- */

  /** Записать набор ключей одним действием (демо-данные). */
  importSnapshot(snapshot) {
    for (const [key, value] of Object.entries(snapshot)) {
      if (key in SCHEMA) this.set(key, value, { silent: true });
    }
    this.dispatchEvent(new CustomEvent('change', { detail: { key: '*' } }));
  }

  /** Удалить все данные приложения (только ключи с префиксом приложения). */
  clearAll() {
    const keys = [];
    for (let i = 0; i < this.#backend.length; i += 1) {
      const k = this.#backend.key(i);
      if (k?.startsWith(PREFIX)) keys.push(k);
    }
    keys.forEach((k) => this.#backend.removeItem(k));
    this.#cache.clear();
    this.#migrate();
    this.dispatchEvent(new CustomEvent('change', { detail: { key: '*' } }));
  }

  /** Есть ли пользовательские записи (для подтверждения перед демо-данными). */
  hasUserData() {
    return ['workouts', 'mood', 'recovery', 'nutritionLog', 'measurements', 'cardio'].some(
      (k) => this.list(k).length > 0,
    );
  }
}
