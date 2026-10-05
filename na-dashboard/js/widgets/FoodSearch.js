/**
 * FoodSearch — встроенный компонент поиска продуктов (Open Food Facts).
 * Дочерний компонент NutritionWidget: уничтожается вместе с ним.
 *
 * Состояния: idle → loading → success | empty | error.
 * Новый поиск отменяет предыдущий незавершённый запрос (AbortController).
 * Данные API выводятся только через textContent.
 */
import { UIComponent } from '../core/UIComponent.js';
import { ApiError, isAbortError } from '../services/ApiClient.js';
import { el, field, input } from '../utils/dom.js';
import { fmt } from '../utils/format.js';
import { AsyncView } from '../ui/AsyncView.js';
import { button } from '../ui/kit.js';

export class FoodSearch extends UIComponent {
  static type = 'food-search';
  static meta = { title: 'Поиск продуктов', icon: 'search', accent: 'nutrition', description: '' };

  #state = { status: 'idle', query: '', items: [], error: null };
  #onSelect;

  constructor({ onSelect, ...config }) {
    super(config);
    this.#onSelect = onSelect;
  }

  renderFrame() {
    return this.createPlainFrame('div', 'food-search');
  }

  renderBody(body) {
    body.append(
      el(
        'form',
        { class: 'search-form', dataset: { form: 'food-search' }, attrs: { role: 'search' } },
        field('Найти продукт в Open Food Facts', input('q', { type: 'search', attrs: { maxlength: 80, autocomplete: 'off', placeholder: 'Например, греческий йогурт или штрихкод', 'aria-controls': `${this.id}-results` } })),
        button('Найти', { type: 'submit', variant: 'secondary', iconName: 'search' }),
      ),
    );
    const results = el('div', { id: `${this.id}-results`, class: 'food-results', attrs: { 'aria-live': 'polite' } });
    body.append(results);
    this.#renderState(new AsyncView(results));
  }

  #renderState(view) {
    const { status, items, error, query } = this.#state;
    if (status === 'idle') {
      view.idle('Поиск по открытой базе продуктов', 'Найдите продукт, чтобы подставить калорийность и БЖУ на 100 г.');
    } else if (status === 'loading') {
      view.loading(`Ищем «${query}»…`);
    } else if (status === 'error') {
      view.error(error);
    } else if (status === 'empty') {
      view.empty(`По запросу «${query}» ничего не найдено`, 'Попробуйте другое название, бренд или штрихкод.');
    } else {
      view.success(
        el('p', { class: 'muted small', text: `Найдено: ${items.length}. Значения на 100 г.` }),
        el(
          'ul',
          { class: 'list list--results' },
          items.map((p, i) =>
            el(
              'li',
              { class: 'list__item' },
              el(
                'div',
                { class: 'list__main' },
                el('span', { class: 'list__title', text: p.name }),
                el('span', {
                  class: 'list__sub',
                  text: p.hasNutrition
                    ? `${p.brand ? `${p.brand} · ` : ''}${fmt(p.per100.kcal)} kcal · Б ${fmt(p.per100.protein, 1)} · Ж ${fmt(p.per100.fat, 1)} · У ${fmt(p.per100.carbs, 1)}`
                    : `${p.brand ? `${p.brand} · ` : ''}нет данных о пищевой ценности`,
                }),
              ),
              button('Выбрать', { action: 'pick', variant: 'tinted', size: 'sm', dataset: { index: i, id: `pick-${i}` }, disabled: !p.hasNutrition, attrs: { 'aria-label': `Выбрать ${p.name}` } }),
            ),
          ),
        ),
      );
    }
  }

  onSubmit(name, form) {
    if (name !== 'food-search') return;
    const query = form.elements.q.value.trim();
    if (query.length < 2) {
      form.elements.q.focus();
      return;
    }
    this.search(query);
  }

  onAction(action, target) {
    if (action === 'retry' && this.#state.query) this.search(this.#state.query);
    if (action === 'pick') {
      const product = this.#state.items[Number(target.dataset.index)];
      if (product) this.#onSelect?.(product);
    }
  }

  async search(query) {
    // Устаревший запрос (если пользователь искал что-то ещё) отменяется здесь.
    const controller = this.startRequest('search');
    this.#state = { status: 'loading', query, items: [], error: null };
    this.refresh();
    try {
      const items = await this.services.food.search(query, { signal: controller.signal });
      this.#state = { status: items.length ? 'success' : 'empty', query, items, error: null };
    } catch (error) {
      if (isAbortError(error)) return; // отмена — не ошибка для пользователя
      this.#state = { status: 'error', query, items: [], error: error instanceof ApiError ? error.userMessage : 'Неизвестная ошибка.' };
    } finally {
      this.finishRequest('search', controller);
    }
    if (!this.isDestroyed) this.refresh();
  }

  /** Состояние для тестов/диагностики. */
  get status() {
    return this.#state.status;
  }
}
