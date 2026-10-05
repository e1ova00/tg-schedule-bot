/**
 * ExerciseLibraryWidget — библиотека упражнений из публичного API wger.
 *
 * Состояния: idle → loading → success | empty | error.
 * Смена категории во время загрузки отменяет устаревший запрос (AbortController),
 * destroy() отменяет текущий. Данные API выводятся только через textContent.
 */
import { UIComponent } from '../core/UIComponent.js';
import { createId } from '../core/StorageService.js';
import { ApiError, isAbortError } from '../services/ApiClient.js';
import { el, field, input, select } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { WGER_CATEGORIES, muscleLabel } from '../data/catalog.js';
import { AsyncView } from '../ui/AsyncView.js';
import { button } from '../ui/kit.js';
import { TrainingWidget } from './TrainingWidget.js';

const PAGE_SIZE = 40;

export class ExerciseLibraryWidget extends UIComponent {
  static type = 'library';
  static meta = {
    title: 'Библиотека упражнений',
    icon: 'book',
    accent: 'library',
    description: 'Поиск упражнений в открытой базе wger.',
  };

  #state = { status: 'idle', items: [], total: 0, nextOffset: 0, hasMore: false, error: null, loadingMore: false };
  #query = '';
  #searchTimer = null;
  #expandedId = null;
  #categories = Object.entries(WGER_CATEGORIES).map(([id, c]) => ({ id: Number(id), label: c.label }));

  constructor(config) {
    super(config);
    this.settings = { category: '', loaded: false, ...this.settings };
    this.watchStorage(['exercises', 'activeWorkout']);
  }

  get #storage() {
    return this.services.storage;
  }

  onMount() {
    // Если пользователь уже загружал библиотеку — загружаем сразу.
    if (this.settings.loaded && this.#state.status === 'idle') this.load();
    this.#loadCategories();
  }

  /** Категории из API (если API недоступен — остаётся встроенный список). */
  async #loadCategories() {
    const controller = this.startRequest('categories');
    try {
      const list = await this.services.wger.getCategories({ signal: controller.signal });
      if (list.length) {
        this.#categories = list.sort((a, b) => a.label.localeCompare(b.label, 'ru'));
        this.refresh();
      }
    } catch (error) {
      if (!isAbortError(error)) console.info('wger: категории недоступны, используется встроенный список');
    } finally {
      this.finishRequest('categories', controller);
    }
  }

  renderBody(body) {
    body.append(
      el(
        'div',
        { class: 'controls-row' },
        field(
          'Категория',
          select('category', [{ value: '', label: 'Все категории' }, ...this.#categories.map((c) => ({ value: String(c.id), label: c.label }))], { value: this.settings.category, attrs: { 'data-field': 'category' } }),
        ),
        field('Поиск среди загруженных', input('search', { type: 'search', value: this.#query, attrs: { 'data-field': 'search', 'data-focus-key': 'library-search', autocomplete: 'off', placeholder: 'Название упражнения', 'aria-controls': `${this.id}-results` } })),
      ),
    );
    const results = el('div', { id: `${this.id}-results`, class: 'library-results', attrs: { 'aria-live': 'polite' } });
    body.append(results);
    this.#renderState(new AsyncView(results));
    body.append(el('p', { class: 'source', text: 'Источник: wger.de — открытая база упражнений (CC-BY-SA).' }));
  }

  #renderState(view) {
    const { status, items, error, total, hasMore, loadingMore } = this.#state;
    if (status === 'idle') {
      view.idle('Открытая база wger', 'Загрузите список упражнений, чтобы искать и добавлять их в свою библиотеку.', button('Загрузить список', { action: 'load', variant: 'primary', iconName: 'book' }));
      return;
    }
    if (status === 'loading') return view.loading('Загружаем упражнения из wger…');
    if (status === 'error') return view.error(error);
    if (status === 'empty') return view.empty('В этой категории упражнений нет', 'Выберите другую категорию.');

    const q = this.#query.toLowerCase();
    const filtered = q ? items.filter((i) => i.name.toLowerCase().includes(q) || i.muscles.some((m) => m.toLowerCase().includes(q))) : items;
    if (!filtered.length) {
      if (hasMore) view.success(...this.#emptyWithMore());
      else view.empty(`«${this.#query}» не найдено среди ${items.length} загруженных`, 'Измените запрос или категорию.');
      return;
    }

    const own = new Set(this.#storage.list('exercises').filter((e) => e.wgerId).map((e) => e.wgerId));
    view.success(
      el('p', { class: 'muted small', text: `Показано ${filtered.length} из ${total}` }),
      el('ul', { class: 'library-list' }, filtered.map((item) => this.#item(item, own.has(item.id)))),
      hasMore
        ? button(loadingMore ? 'Загружаем…' : 'Показать ещё', { action: 'more', variant: 'secondary', disabled: loadingMore, dataset: { focusKey: 'library-more' } })
        : null,
    );
  }

  #emptyWithMore() {
    return [
      el('p', { class: 'muted small', text: `«${this.#query}» не найдено среди ${this.#state.items.length} загруженных.` }),
      button(this.#state.loadingMore ? 'Загружаем…' : 'Загрузить ещё', { action: 'more', variant: 'secondary', disabled: this.#state.loadingMore, dataset: { focusKey: 'library-more' } }),
    ];
  }

  #item(item, added) {
    const expanded = this.#expandedId === item.id;
    const detailsId = `${this.id}-ex-${item.id}`;
    const hasWorkout = Boolean(this.#storage.get('activeWorkout'));
    return el(
      'li',
      { class: ['library-item', expanded && 'is-expanded'] },
      el(
        'button',
        { type: 'button', class: 'library-item__toggle', dataset: { action: 'toggle', id: String(item.id) }, attrs: { 'aria-expanded': String(expanded), 'aria-controls': detailsId } },
        el('span', { class: 'library-item__name', text: item.name }),
        el('span', { class: 'library-item__meta', text: [item.category, item.muscles[0]].filter(Boolean).join(' · ') }),
        added ? el('span', { class: 'badge badge--library', text: 'В библиотеке' }) : null,
        icon('chevronDown', { size: 16, className: 'library-item__chevron' }),
      ),
      expanded
        ? el(
            'div',
            { class: 'library-item__details', id: detailsId },
            this.#detailRow('Категория', item.category),
            this.#detailRow('Основные мышцы', item.muscles.join(', ')),
            this.#detailRow('Вспомогательные', item.secondary.join(', ')),
            this.#detailRow('Инвентарь', item.equipment.join(', ')),
            item.description ? el('p', { class: 'library-item__desc', text: item.description }) : null,
            item.language === 'en' ? el('p', { class: 'muted small', text: 'Описание доступно только на английском.' }) : null,
            el(
              'div',
              { class: 'actions' },
              button(added ? 'Уже в библиотеке' : 'В мою библиотеку', { action: 'add', variant: 'tinted', iconName: added ? 'check' : 'plus', disabled: added, dataset: { id: String(item.id) } }),
              hasWorkout ? button('В текущую тренировку', { action: 'add-workout', variant: 'secondary', iconName: 'dumbbell', dataset: { id: String(item.id) } }) : null,
            ),
          )
        : null,
    );
  }

  #detailRow(label, value) {
    if (!value) return null;
    return el('p', { class: 'detail-row' }, el('span', { class: 'muted', text: `${label}: ` }), el('span', { text: value }));
  }

  /* ------------------------- Загрузка ------------------------- */

  async load({ append = false } = {}) {
    // startRequest отменяет предыдущий незавершённый запрос (например, при быстрой смене категории).
    const controller = this.startRequest('list');
    const offset = append ? this.#state.nextOffset : 0;
    if (append) this.#state = { ...this.#state, loadingMore: true };
    else this.#state = { status: 'loading', items: [], total: 0, nextOffset: 0, hasMore: false, error: null, loadingMore: false };
    this.refresh();

    try {
      const page = await this.services.wger.getExercises({ category: this.settings.category || null, offset, limit: PAGE_SIZE, signal: controller.signal });
      const items = append ? [...this.#state.items, ...page.items.filter((i) => !this.#state.items.some((x) => x.id === i.id))] : page.items;
      this.#state = {
        status: items.length ? 'success' : 'empty',
        items,
        total: page.total,
        nextOffset: page.nextOffset,
        hasMore: page.hasMore,
        error: null,
        loadingMore: false,
      };
      if (!this.settings.loaded) this.updateSettings({ loaded: true }, { rerender: false });
    } catch (error) {
      if (isAbortError(error)) return; // устаревший запрос отменён — новый уже идёт
      const message = error instanceof ApiError ? error.userMessage : 'Неизвестная ошибка.';
      this.#state = append ? { ...this.#state, loadingMore: false } : { ...this.#state, status: 'error', error: message };
      if (append) this.services.toast.show(message, { variant: 'error' });
    } finally {
      this.finishRequest('list', controller);
    }
    if (!this.isDestroyed) this.refresh();
  }

  /* ------------------------- События ------------------------- */

  onChange(name, target) {
    if (name === 'category') {
      this.#expandedId = null;
      this.updateSettings({ category: target.value }, { rerender: false });
      this.load();
    }
  }

  onInput(name, target) {
    if (name === 'search') {
      this.#query = target.value.trim();
      // Фильтрация локальная и мгновенная; небольшая задержка — чтобы не перерисовывать на каждый символ.
      this.clearTimeout(this.#searchTimer);
      this.#searchTimer = this.setTimeout(() => this.refresh(), 200);
    }
  }

  onAction(action, target) {
    const id = Number(target.dataset.id);
    const item = this.#state.items.find((i) => i.id === id);
    switch (action) {
      case 'load':
      case 'retry':
        this.load();
        break;
      case 'more':
        this.load({ append: true });
        break;
      case 'toggle':
        this.#expandedId = this.#expandedId === id ? null : id;
        this.refresh();
        break;
      case 'add':
        if (item) this.#addToLibrary(item);
        break;
      case 'add-workout':
        if (item) {
          const exercise = this.#addToLibrary(item, { silent: true });
          if (TrainingWidget.addToActiveWorkout(this.#storage, exercise)) this.services.toast.show(`«${item.name}» добавлено в тренировку`);
        }
        break;
      default:
    }
  }

  #addToLibrary(item, { silent = false } = {}) {
    const existing = this.#storage.list('exercises').find((e) => e.wgerId === item.id);
    if (existing) return existing;
    const exercise = { id: createId('ex'), name: item.name, muscle: item.muscleGroup, source: 'wger', wgerId: item.id };
    this.#storage.add('exercises', exercise);
    if (!silent) this.services.toast.show(`«${item.name}» добавлено в библиотеку · ${muscleLabel(item.muscleGroup)}`);
    return exercise;
  }

  get status() {
    return this.#state.status;
  }
}
