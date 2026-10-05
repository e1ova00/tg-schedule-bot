/**
 * Dashboard — управляет коллекцией активных виджетов одного раздела.
 *
 * Работает с виджетами только через общий интерфейс UIComponent
 * (render / mount / destroy / toJSON / setPosition) — полиморфизм.
 * Конкретные классы создаёт WidgetRegistry.
 */
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';

export class Dashboard {
  #widgets = [];
  #registry;
  #storage;
  #layoutKey;
  #defaultLayout;
  #services;
  #announce;
  #root = null;
  #grid = null;
  #empty = null;
  #listeners = [];
  #draggedId = null;
  #onAddRequest;

  constructor({ registry, storage, layoutKey, defaultLayout = [], services = {}, announce = () => {}, onAddRequest = () => {} }) {
    this.#registry = registry;
    this.#storage = storage;
    this.#layoutKey = layoutKey;
    this.#defaultLayout = defaultLayout;
    this.#services = services;
    this.#announce = announce;
    this.#onAddRequest = onAddRequest;
  }

  /** Копия коллекции — снаружи изменить её нельзя. */
  get widgets() {
    return [...this.#widgets];
  }

  get element() {
    return this.#root;
  }

  /* ---------------------------------------------------------------- */

  render() {
    if (this.#root) return this.#root;
    this.#grid = el('div', { class: 'dashboard-grid', attrs: { 'data-layout': this.#layoutKey } });
    this.#empty = el(
      'div',
      { class: 'dashboard-empty', hidden: true },
      el('span', { class: 'dashboard-empty__icon', attrs: { 'aria-hidden': 'true' } }, icon('grid', { size: 28 })),
      el('p', { class: 'dashboard-empty__title', text: 'Здесь пока нет виджетов' }),
      el('p', { class: 'muted', text: 'Добавьте виджеты, которые нужны вам в этом разделе.' }),
      el('button', { type: 'button', class: 'btn btn--primary', dataset: { dashboardAction: 'add' } }, icon('plus', { size: 18 }), 'Добавить виджет'),
    );
    this.#root = el('div', { class: 'dashboard' }, this.#grid, this.#empty);
    this.#bindEvents();
    return this.#root;
  }

  /** Вставить панель в документ и восстановить сохранённую раскладку. */
  mount(container) {
    container.append(this.render());
    this.loadLayout();
  }

  #listen(target, type, handler) {
    target.addEventListener(type, handler);
    this.#listeners.push({ target, type, handler });
  }

  #bindEvents() {
    // Запросы от виджетов (всплывающие события): удалить, переместить, состояние изменилось.
    this.#listen(this.#grid, 'component:request', (event) => {
      const { id, action } = event.detail;
      if (!this.#widgets.some((w) => w.id === id)) return;
      event.stopPropagation();
      if (action === 'remove') this.#confirmRemove(id);
      else if (action === 'move-up') this.moveWidget(id, this.#indexOf(id) - 1, { keepFocus: 'move-up' });
      else if (action === 'move-down') this.moveWidget(id, this.#indexOf(id) + 1, { keepFocus: 'move-down' });
      else if (action === 'state') this.saveLayout();
    });

    this.#listen(this.#root, 'click', (event) => {
      if (event.target.closest('[data-dashboard-action="add"]')) this.#onAddRequest();
    });

    // Drag-and-drop (мышь). Альтернатива для клавиатуры — кнопки ↑ ↓ в заголовке.
    this.#listen(this.#grid, 'pointerdown', (event) => {
      const handle = event.target.closest('[data-drag-handle]');
      const card = handle?.closest('[data-widget-id]');
      if (card) card.draggable = true;
    });
    this.#listen(this.#grid, 'dragstart', (event) => {
      const card = event.target.closest?.('[data-widget-id]');
      if (!card || !card.draggable) return;
      this.#draggedId = card.dataset.widgetId;
      card.classList.add('is-dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', this.#draggedId);
    });
    this.#listen(this.#grid, 'dragover', (event) => {
      if (!this.#draggedId) return;
      event.preventDefault();
      const over = event.target.closest('[data-widget-id]');
      this.#grid.querySelectorAll('.is-drop-target').forEach((n) => n.classList.remove('is-drop-target'));
      if (over && over.dataset.widgetId !== this.#draggedId) over.classList.add('is-drop-target');
    });
    this.#listen(this.#grid, 'drop', (event) => {
      if (!this.#draggedId) return;
      event.preventDefault();
      const over = event.target.closest('[data-widget-id]');
      if (over && over.dataset.widgetId !== this.#draggedId) {
        this.moveWidget(this.#draggedId, this.#indexOf(over.dataset.widgetId));
      }
      this.#endDrag();
    });
    this.#listen(this.#grid, 'dragend', () => this.#endDrag());
  }

  #endDrag() {
    this.#grid.querySelectorAll('[draggable="true"], .is-dragging, .is-drop-target').forEach((n) => {
      n.draggable = false;
      n.classList.remove('is-dragging', 'is-drop-target');
    });
    this.#draggedId = null;
  }

  #indexOf(id) {
    return this.#widgets.findIndex((w) => w.id === id);
  }

  async #confirmRemove(id) {
    const widget = this.#widgets[this.#indexOf(id)];
    const { modal } = this.#services;
    const ok = modal
      ? await modal.confirm({
          title: `Удалить виджет «${widget.title}»?`,
          message: 'Ваши записи останутся в приложении — удаляется только карточка с панели.',
          confirmLabel: 'Удалить',
          danger: true,
        })
      : true;
    if (ok) this.removeWidget(id);
  }

  /* ---------------------------------------------------------------- */
  /* Публичный API                                                     */
  /* ---------------------------------------------------------------- */

  /** Добавить виджет указанного типа. Возвращает созданный экземпляр. */
  addWidget(widgetType, config = {}, { save = true, announce = true } = {}) {
    if (!this.#registry.has(widgetType)) {
      console.warn(`Dashboard: тип «${widgetType}» не зарегистрирован`);
      return null;
    }
    const widget = this.#registry.create(widgetType, { ...config, services: this.#services });
    this.#widgets.push(widget);
    widget.mount(this.#grid);
    this.#syncPositions();
    if (save) this.saveLayout();
    if (announce) this.#announce(`Виджет «${widget.title}» добавлен`);
    return widget;
  }

  /** Удалить виджет по id: destroy() освобождает все его ресурсы. */
  removeWidget(widgetId) {
    const index = this.#indexOf(widgetId);
    if (index === -1) return false;
    const [widget] = this.#widgets.splice(index, 1);
    const title = widget.title;
    widget.destroy();
    this.#syncPositions();
    this.saveLayout();
    this.#announce(`Виджет «${title}» удалён`);
    // Переносим фокус на соседний виджет, чтобы клавиатурный пользователь не «потерялся».
    const neighbour = this.#widgets[Math.min(index, this.#widgets.length - 1)];
    (neighbour?.element.querySelector('.widget__title') ?? this.#empty.querySelector('button'))?.focus?.();
    return true;
  }

  /** Переместить виджет на позицию toIndex. */
  moveWidget(widgetId, toIndex, { keepFocus } = {}) {
    const from = this.#indexOf(widgetId);
    if (from === -1) return false;
    const to = Math.max(0, Math.min(this.#widgets.length - 1, toIndex));
    if (from === to) return false;
    const [widget] = this.#widgets.splice(from, 1);
    this.#widgets.splice(to, 0, widget);
    const next = this.#widgets[to + 1]?.element ?? null;
    this.#grid.insertBefore(widget.element, next);
    this.#syncPositions();
    this.saveLayout();
    this.#announce(`«${widget.title}»: позиция ${to + 1} из ${this.#widgets.length}`);
    if (keepFocus) {
      const btn = widget.element.querySelector(`[data-control="${keepFocus}"]`);
      (btn && !btn.disabled ? btn : widget.element.querySelector('[data-control="move-up"]:not(:disabled), [data-control="move-down"]:not(:disabled)'))?.focus();
    }
    return true;
  }

  saveLayout() {
    this.#storage.saveLayout(this.#layoutKey, this.#widgets.map((w) => w.toJSON()));
  }

  loadLayout() {
    this.#widgets.forEach((w) => w.destroy());
    this.#widgets = [];
    const saved = this.#storage.getLayout(this.#layoutKey);
    const layout = Array.isArray(saved) ? saved : this.#defaultLayout;
    for (const entry of layout) {
      if (!this.#registry.has(entry.type)) continue;
      this.addWidget(entry.type, entry, { save: false, announce: false });
    }
    this.#syncPositions();
    if (!Array.isArray(saved)) this.saveLayout();
  }

  #syncPositions() {
    this.#widgets.forEach((w, i) => w.setPosition(i, this.#widgets.length));
    if (this.#empty) this.#empty.hidden = this.#widgets.length > 0;
  }

  destroy() {
    this.#widgets.forEach((w) => w.destroy());
    this.#widgets = [];
    for (const { target, type, handler } of this.#listeners) target.removeEventListener(type, handler);
    this.#listeners = [];
    this.#root?.remove();
    this.#root = null;
  }
}
