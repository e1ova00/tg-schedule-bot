/**
 * WgerService — публичный REST API wger (https://wger.de/api/v2/).
 * Ключ для чтения не требуется. Все ответы считаются недоверенными:
 * нормализуются в простые объекты со строками и числами.
 */
import { fetchJSON, safeNumber, safeString } from './ApiClient.js';
import { WGER_CATEGORIES } from '../data/catalog.js';
import { htmlToText } from '../utils/dom.js';

const BASE_URL = 'https://wger.de/api/v2';
const LANG_RU = 5;
const LANG_EN = 2;

const MUSCLE_RU = {
  'Anterior deltoid': 'Передняя дельта',
  'Biceps brachii': 'Бицепс',
  'Biceps femoris': 'Бицепс бедра',
  Brachialis: 'Плечевая мышца',
  Gastrocnemius: 'Икроножная',
  'Gluteus maximus': 'Большая ягодичная',
  'Latissimus dorsi': 'Широчайшая',
  'Obliquus externus abdominis': 'Косые мышцы живота',
  'Pectoralis major': 'Большая грудная',
  'Quadriceps femoris': 'Квадрицепс',
  'Rectus abdominis': 'Прямая мышца живота',
  'Serratus anterior': 'Передняя зубчатая',
  Soleus: 'Камбаловидная',
  Trapezius: 'Трапеция',
  'Triceps brachii': 'Трицепс',
};

export class WgerService {
  /** Категории упражнений. */
  async getCategories({ signal } = {}) {
    const data = await fetchJSON(`${BASE_URL}/exercisecategory/?format=json`, { signal });
    const results = Array.isArray(data?.results) ? data.results : [];
    return results
      .map((c) => {
        const id = safeNumber(c?.id, { min: 1 });
        if (!id) return null;
        const name = safeString(c?.name, 60);
        return { id, label: WGER_CATEGORIES[id]?.label ?? name };
      })
      .filter(Boolean);
  }

  /** Страница упражнений (exerciseinfo уже содержит переводы, мышцы и инвентарь). */
  async getExercises({ category = null, offset = 0, limit = 40, signal } = {}) {
    const params = new URLSearchParams({ format: 'json', limit: String(limit), offset: String(offset) });
    if (category) params.set('category', String(category));
    const data = await fetchJSON(`${BASE_URL}/exerciseinfo/?${params}`, { signal });
    const results = Array.isArray(data?.results) ? data.results : [];
    const total = safeNumber(data?.count) ?? results.length;
    return {
      items: results.map((raw) => WgerService.normalize(raw)).filter(Boolean),
      total,
      nextOffset: offset + results.length,
      hasMore: Boolean(data?.next) && results.length > 0,
    };
  }

  static #muscleName(m) {
    const en = safeString(m?.name_en, 60);
    const latin = safeString(m?.name, 60);
    return MUSCLE_RU[latin] ?? (en || latin);
  }

  /** Привести «сырое» упражнение к безопасному виду. null — если нет названия. */
  static normalize(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const id = safeNumber(raw.id, { min: 1 });
    // Поле переименовывалось между версиями API: translations (новое) / exercises (старое).
    const translations = Array.isArray(raw.translations) ? raw.translations : Array.isArray(raw.exercises) ? raw.exercises : [];
    const pick = translations.find((t) => t?.language === LANG_RU) ?? translations.find((t) => t?.language === LANG_EN);
    const name = safeString(pick?.name, 120);
    if (!id || !name) return null;

    const categoryId = safeNumber(raw.category?.id, { min: 1 });
    const muscles = (Array.isArray(raw.muscles) ? raw.muscles : []).map((m) => WgerService.#muscleName(m)).filter(Boolean);
    const secondary = (Array.isArray(raw.muscles_secondary) ? raw.muscles_secondary : []).map((m) => WgerService.#muscleName(m)).filter(Boolean);
    const equipment = (Array.isArray(raw.equipment) ? raw.equipment : []).map((e) => safeString(e?.name, 60)).filter(Boolean);

    return {
      id,
      name,
      language: pick?.language === LANG_RU ? 'ru' : 'en',
      description: htmlToText(typeof pick?.description === 'string' ? pick.description : '').slice(0, 600),
      categoryId,
      category: WGER_CATEGORIES[categoryId]?.label ?? safeString(raw.category?.name, 60),
      muscleGroup: WGER_CATEGORIES[categoryId]?.muscle ?? 'fullbody',
      muscles,
      secondary,
      equipment,
    };
  }
}
