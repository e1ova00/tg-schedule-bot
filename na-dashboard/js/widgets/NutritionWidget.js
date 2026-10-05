/**
 * NutritionWidget — дневник питания и воды.
 * Цели (kcal, БЖУ, вода) задаёт сам пользователь — приложение норму не рассчитывает.
 * Поиск продуктов — дочерний компонент FoodSearch (Open Food Facts).
 */
import { UIComponent } from '../core/UIComponent.js';
import { el, field, input, select, formValues, setFieldError, clearFormErrors } from '../utils/dom.js';
import { addDays, formatLongDate, relativeDayLabel, today } from '../utils/date.js';
import { nutritionTotals, scalePer100 } from '../utils/calculations.js';
import { fmt, toNumber } from '../utils/format.js';
import { MEALS } from '../data/catalog.js';
import { icon } from '../utils/icons.js';
import { button, emptyState, iconButton, note, progressBar, ring } from '../ui/kit.js';
import { FoodSearch } from './FoodSearch.js';

const WATER_STEP = 250;

export class NutritionWidget extends UIComponent {
  static type = 'nutrition';
  static meta = {
    title: 'Питание',
    icon: 'leaf',
    accent: 'nutrition',
    description: 'Калории, БЖУ, вода и поиск продуктов (Open Food Facts).',
  };

  #date = today();
  #editingGoals = false;
  #selected = null; // выбранный продукт из поиска (значения на 100 г)
  #search = null;

  constructor(config) {
    super(config);
    this.watchStorage(['nutritionGoals', 'nutritionLog', 'water']);
  }

  get #storage() {
    return this.services.storage;
  }

  renderBody(body) {
    const goals = this.#storage.get('nutritionGoals');
    if (!goals || this.#editingGoals) {
      this.#renderGoalsForm(body, goals);
      return;
    }

    const entries = this.#storage.list('nutritionLog').filter((e) => e.date === this.#date);
    const totals = nutritionTotals(entries);
    const water = this.#storage.get('water')[this.#date] ?? 0;
    const isToday = this.#date === today();

    body.append(
      el(
        'div',
        { class: 'day-nav' },
        iconButton('chevronLeft', 'Предыдущий день', { action: 'prev-day' }),
        el('p', { class: 'day-nav__label', attrs: { 'aria-live': 'polite' } }, el('strong', { text: relativeDayLabel(this.#date) }), el('span', { class: 'muted small', text: formatLongDate(this.#date) })),
        el('button', { type: 'button', class: 'icon-btn', dataset: { action: 'next-day' }, disabled: isToday, attrs: { 'aria-label': 'Следующий день' } }, icon('chevronRight', { size: 16 })),
      ),
      el(
        'div',
        { class: 'kcal-hero' },
        el('p', { class: 'kcal-hero__value' }, el('span', { class: 'big-number', text: fmt(totals.kcal) }), el('span', { class: 'muted', text: ` / ${fmt(goals.kcal)} kcal` })),
        progressBar({ value: totals.kcal, max: goals.kcal, accent: 'nutrition', label: 'Калории', detail: `${Math.round((totals.kcal / goals.kcal) * 100) || 0}%` }),
      ),
      el(
        'div',
        { class: 'macros' },
        progressBar({ value: totals.protein, max: goals.protein, accent: 'protein', label: 'Белки', detail: `${fmt(totals.protein)} / ${fmt(goals.protein)} г` }),
        progressBar({ value: totals.fat, max: goals.fat, accent: 'fat', label: 'Жиры', detail: `${fmt(totals.fat)} / ${fmt(goals.fat)} г` }),
        progressBar({ value: totals.carbs, max: goals.carbs, accent: 'carbs', label: 'Углеводы', detail: `${fmt(totals.carbs)} / ${fmt(goals.carbs)} г` }),
      ),
      el(
        'div',
        { class: 'water' },
        ring({ ratio: water / goals.water, accent: 'water', size: 56, stroke: 7, gradient: false }),
        el('div', { class: 'water__text' }, el('p', { class: 'water__title', text: 'Вода' }), el('p', { class: 'muted small', text: `${fmt(water)} / ${fmt(goals.water)} мл` })),
        iconButton('minus', `Убрать ${WATER_STEP} мл воды`, { action: 'water-minus', variant: 'tinted' }),
        iconButton('plus', `Добавить ${WATER_STEP} мл воды`, { action: 'water-plus', variant: 'tinted' }),
      ),
    );

    body.append(el('h3', { class: 'section-title', text: 'Приёмы пищи' }));
    if (entries.length) {
      body.append(
        el(
          'ul',
          { class: 'list' },
          entries.map((e) =>
            el(
              'li',
              { class: 'list__item' },
              el(
                'div',
                { class: 'list__main' },
                el('span', { class: 'list__title', text: e.name }),
                el('span', { class: 'list__sub', text: `${e.meal ?? ''}${e.grams ? ` · ${fmt(e.grams)} г` : ''} · Б ${fmt(e.protein, 1)} · Ж ${fmt(e.fat, 1)} · У ${fmt(e.carbs, 1)}` }),
              ),
              el('span', { class: 'list__meta', text: `${fmt(e.kcal)} kcal` }),
              iconButton('trash', `Удалить «${e.name}»`, { action: 'delete-entry', dataset: { id: e.id }, variant: 'ghost' }),
            ),
          ),
        ),
      );
    } else {
      body.append(emptyState({ iconName: 'leaf', title: 'Записей за этот день нет', text: 'Добавьте приём пищи вручную или найдите продукт.' }));
    }

    this.#search ??= this.addChild(new FoodSearch({ services: this.services, onSelect: (p) => this.#selectProduct(p) }));

    body.append(
      el(
        'details',
        { class: 'disclosure', dataset: { key: 'add-food' } },
        el('summary', { class: 'disclosure__summary', text: 'Добавить приём пищи' }),
        this.#search.render(),
        this.#selected ? this.#selectedCard() : null,
        this.#entryForm(),
      ),
      el(
        'div',
        { class: 'actions' },
        button('Изменить цели', { action: 'edit-goals', variant: 'ghost', iconName: 'edit' }),
      ),
    );
  }

  #selectedCard() {
    const p = this.#selected;
    return el(
      'div',
      { class: 'selected-product' },
      el('p', { class: 'small' }, el('strong', { text: `Выбрано: ${p.name}` }), el('span', { class: 'muted', text: ` · на 100 г: ${fmt(p.per100.kcal)} kcal, Б ${fmt(p.per100.protein, 1)}, Ж ${fmt(p.per100.fat, 1)}, У ${fmt(p.per100.carbs, 1)}` })),
      iconButton('x', 'Сбросить выбранный продукт', { action: 'clear-product', variant: 'ghost' }),
    );
  }

  #entryForm() {
    return el(
      'form',
      { class: 'form-grid', dataset: { form: 'entry' }, attrs: { novalidate: true } },
      field('Название', input('name', { attrs: { maxlength: 80, autocomplete: 'off' }, required: true })),
      field('Приём пищи', select('meal', MEALS.map((m) => ({ value: m, label: m })))),
      field('Порция, г', input('grams', { type: 'number', attrs: { min: 1, max: 3000, inputmode: 'numeric', 'data-field': 'grams' } }), { hint: this.#selected ? 'Пересчитается от значений на 100 г' : undefined }),
      field('Калории, kcal', input('kcal', { type: 'number', attrs: { min: 0, max: 5000, inputmode: 'decimal' }, required: true })),
      field('Белки, г', input('protein', { type: 'number', attrs: { min: 0, max: 500, step: 0.1, inputmode: 'decimal' } })),
      field('Жиры, г', input('fat', { type: 'number', attrs: { min: 0, max: 500, step: 0.1, inputmode: 'decimal' } })),
      field('Углеводы, г', input('carbs', { type: 'number', attrs: { min: 0, max: 800, step: 0.1, inputmode: 'decimal' } })),
      button('Добавить', { type: 'submit', variant: 'primary', iconName: 'plus' }),
    );
  }

  #renderGoalsForm(body, goals) {
    body.append(
      el('p', { class: 'lead', text: goals ? 'Изменить цели' : 'Задайте свои цели по питанию' }),
      el(
        'form',
        { class: 'form-grid', dataset: { form: 'goals' }, attrs: { novalidate: true } },
        field('Калории, kcal', input('kcal', { type: 'number', value: goals?.kcal ?? '', attrs: { min: 800, max: 6000, inputmode: 'numeric' }, required: true })),
        field('Белки, г', input('protein', { type: 'number', value: goals?.protein ?? '', attrs: { min: 0, max: 500, inputmode: 'numeric' }, required: true })),
        field('Жиры, г', input('fat', { type: 'number', value: goals?.fat ?? '', attrs: { min: 0, max: 400, inputmode: 'numeric' }, required: true })),
        field('Углеводы, г', input('carbs', { type: 'number', value: goals?.carbs ?? '', attrs: { min: 0, max: 900, inputmode: 'numeric' }, required: true })),
        field('Вода, мл', input('water', { type: 'number', value: goals?.water ?? 2000, attrs: { min: 500, max: 6000, step: 50, inputmode: 'numeric' } })),
        el(
          'div',
          { class: 'actions' },
          button('Сохранить цели', { type: 'submit', variant: 'primary', iconName: 'check' }),
          goals ? button('Отмена', { action: 'cancel-goals', variant: 'ghost' }) : null,
        ),
      ),
      note('N.A. не рассчитывает персональную норму КБЖУ. Укажите цели, которые вы выбрали сами или согласовали со специалистом.'),
    );
  }

  /* ------------------------- События ------------------------- */

  #selectProduct(product) {
    this.#selected = product;
    this.refresh();
    const form = this.body.querySelector('form[data-form="entry"]');
    if (!form) return;
    form.elements.name.value = product.brand ? `${product.name} (${product.brand})` : product.name;
    if (!form.elements.grams.value) form.elements.grams.value = '100';
    this.#applyPortion(form);
    form.elements.grams.focus();
    this.services.announce?.(`Выбран продукт ${product.name}. Укажите порцию.`);
  }

  #applyPortion(form) {
    if (!this.#selected) return;
    const grams = toNumber(form.elements.grams.value);
    if (!grams) return;
    const v = scalePer100(this.#selected.per100, grams);
    for (const key of ['kcal', 'protein', 'fat', 'carbs']) form.elements[key].value = v[key] ?? '';
  }

  onInput(name, target) {
    if (name === 'grams') this.#applyPortion(target.form);
  }

  async onAction(action, target) {
    switch (action) {
      case 'prev-day':
        this.#date = addDays(this.#date, -1);
        this.refresh();
        break;
      case 'next-day':
        if (this.#date < today()) this.#date = addDays(this.#date, 1);
        this.refresh();
        break;
      case 'water-plus':
      case 'water-minus': {
        const delta = action === 'water-plus' ? WATER_STEP : -WATER_STEP;
        this.#storage.update('water', (w) => ({ ...w, [this.#date]: Math.max(0, (w[this.#date] ?? 0) + delta) }));
        break;
      }
      case 'delete-entry':
        if (await this.services.modal.confirm({ title: 'Удалить запись?', message: 'Запись о приёме пищи будет удалена.', confirmLabel: 'Удалить', danger: true })) {
          this.#storage.removeItem('nutritionLog', target.dataset.id);
        }
        break;
      case 'edit-goals':
        this.#editingGoals = true;
        this.refresh();
        this.body.querySelector('input')?.focus();
        break;
      case 'cancel-goals':
        this.#editingGoals = false;
        this.refresh();
        break;
      case 'clear-product':
        this.#selected = null;
        this.refresh();
        break;
      default:
    }
  }

  onSubmit(name, form) {
    clearFormErrors(form);
    const v = formValues(form);

    if (name === 'goals') {
      const goals = {};
      let ok = true;
      for (const [key, min, max] of [['kcal', 800, 6000], ['protein', 0, 500], ['fat', 0, 400], ['carbs', 0, 900], ['water', 500, 6000]]) {
        const n = toNumber(v[key]);
        if (n === null || n < min || n > max) {
          setFieldError(form.elements[key], `Введите число от ${min} до ${max}`);
          ok = false;
        }
        goals[key] = n;
      }
      if (!ok) return;
      this.#editingGoals = false;
      this.#storage.set('nutritionGoals', goals);
      this.services.toast.show('Цели сохранены');
      return;
    }

    if (name === 'entry') {
      const kcal = toNumber(v.kcal);
      let ok = true;
      if (!v.name) {
        setFieldError(form.elements.name, 'Введите название');
        ok = false;
      }
      if (kcal === null || kcal < 0 || kcal > 5000) {
        setFieldError(form.elements.kcal, 'Калории от 0 до 5000');
        ok = false;
      }
      if (!ok) return;
      const entry = {
        date: this.#date,
        meal: v.meal,
        name: v.name.slice(0, 80),
        grams: toNumber(v.grams),
        kcal,
        protein: toNumber(v.protein) ?? 0,
        fat: toNumber(v.fat) ?? 0,
        carbs: toNumber(v.carbs) ?? 0,
        source: this.#selected ? 'openfoodfacts' : 'manual',
      };
      form.reset();
      this.#selected = null;
      this.#storage.add('nutritionLog', entry);
      this.services.toast.show(`Добавлено: ${entry.name}`);
    }
  }
}
