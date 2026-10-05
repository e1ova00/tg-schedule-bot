/**
 * FoodService — публичный read API Open Food Facts (https://world.openfoodfacts.org/).
 * Ключ не требуется. Полнотекстовый поиск — /cgi/search.pl (лимит ≈10 запросов в минуту),
 * поиск по штрихкоду — /api/v2/product/{code}.
 * Данные краудсорсинговые и считаются недоверенными: проверяем типы и диапазоны.
 */
import { fetchJSON, safeNumber, safeString } from './ApiClient.js';

const BASE_URL = 'https://world.openfoodfacts.org';
const FIELDS = 'code,product_name,product_name_ru,generic_name,brands,quantity,nutriments';

export class FoodService {
  async search(query, { signal, pageSize = 12 } = {}) {
    const q = String(query ?? '').trim();
    if (!q) return [];
    if (/^\d{8,14}$/.test(q)) return this.#byBarcode(q, { signal });

    const params = new URLSearchParams({
      search_terms: q,
      search_simple: '1',
      action: 'process',
      json: '1',
      page_size: String(pageSize),
      fields: FIELDS,
      lc: 'ru',
    });
    const data = await fetchJSON(`${BASE_URL}/cgi/search.pl?${params}`, { signal, timeout: 20000 });
    const products = Array.isArray(data?.products) ? data.products : [];
    return products.map((p) => FoodService.normalize(p)).filter(Boolean);
  }

  async #byBarcode(code, { signal }) {
    const data = await fetchJSON(`${BASE_URL}/api/v2/product/${encodeURIComponent(code)}.json?fields=${FIELDS}`, { signal });
    if (data?.status !== 1 || !data.product) return [];
    const product = FoodService.normalize({ code, ...data.product });
    return product ? [product] : [];
  }

  /** Нормализация продукта: значения на 100 г. */
  static normalize(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const name = safeString(raw.product_name_ru, 120) || safeString(raw.product_name, 120) || safeString(raw.generic_name, 120);
    if (!name) return null;
    const n = raw.nutriments && typeof raw.nutriments === 'object' ? raw.nutriments : {};
    const kcalDirect = safeNumber(n['energy-kcal_100g'], { max: 950 });
    const kj = safeNumber(n.energy_100g, { max: 4000 });
    const kcal = kcalDirect ?? (kj !== null ? Math.round(kj / 4.184) : null);
    const grams = (v) => safeNumber(v, { max: 100 });
    const per100 = {
      kcal: kcal !== null ? Math.round(kcal) : null,
      protein: grams(n.proteins_100g),
      fat: grams(n.fat_100g),
      carbs: grams(n.carbohydrates_100g),
      sugars: grams(n.sugars_100g),
      fiber: grams(n.fiber_100g),
    };
    return {
      code: safeString(String(raw.code ?? ''), 20),
      name,
      brand: safeString(raw.brands, 80).split(',')[0]?.trim() ?? '',
      quantity: safeString(raw.quantity, 40),
      per100,
      hasNutrition: per100.kcal !== null || per100.protein !== null,
    };
  }
}
