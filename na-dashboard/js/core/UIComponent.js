/**
 * UIComponent — базовый (абстрактный) класс всех компонентов и виджетов.
 *
 * Отвечает за общий жизненный цикл:
 *   render()  → создаёт DOM (рамку + тело через абстрактный renderBody())
 *   mount()   → вставляет в контейнер
 *   refresh() → перерисовывает тело, сохраняя фокус и введённые значения
 *   destroy() → удаляет DOM, слушатели, таймеры, графики, отменяет запросы
 *
 * Обработка событий построена на делегировании: на корневой элемент
 * вешается по одному слушателю на тип события, а элементы внутри
 * описываются атрибутами data-action / data-form / data-field.
 * Поэтому повторная отрисовка тела не плодит слушатели.
 */
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';

export const SIZES = Object.freeze(['small', 'medium', 'large']);
const SIZE_LABELS = { small: 'маленький', medium: 'средний', large: 'большой' };
const SIZE_SHORT = { small: 'S', medium: 'M', large: 'L' };

let instanceCounter = 0;

export class UIComponent {
  /** Тип компонента (используется в реестре и сохранённой раскладке). */
  static type = 'component';

  /** Описание для каталога виджетов. Переопределяется в наследниках. */
  static meta = { title: 'Компонент', icon: 'grid', accent: 'neutral', description: '' };

  #listeners = [];
  #timeouts = new Set();
  #intervals = new Set();
  #controllers = new Map();
  #charts = new Set();
  #pendingCharts = [];
  #children = new Set();
  #destroyed = false;

  constructor({ id, title, size = 'medium', isMinimized = false, settings = {}, services = {} } = {}) {
    if (new.target === UIComponent) {
      throw new TypeError('UIComponent — абстрактный класс, создавайте наследников');
    }
    instanceCounter += 1;
    this.id = id ?? `${this.constructor.type}-${Date.now().toString(36)}-${instanceCounter}`;
    this.title = title ?? this.constructor.meta.title;
    this.size = SIZES.includes(size) ? size : 'medium';
    this.isMinimized = Boolean(isMinimized);
    this.settings = { ...settings };
    this.services = services;
    this.element = null;
    this.body = null;
  }

  get type() {
    return this.constructor.type;
  }

  get meta() {
    return this.constructor.meta;
  }

  get isDestroyed() {
    return this.#destroyed;
  }

  /* ================================================================ */
  /* Отрисовка                                                         */
  /* ================================================================ */

  /** Вернуть корневой DOM-элемент (создаётся один раз). */
  render() {
    if (this.#destroyed) throw new Error(`Компонент ${this.id} уже уничтожен`);
    if (this.element) return this.element;
    this.element = this.renderFrame();
    this.element.dataset.componentRoot = this.id;
    this.#bindDelegatedEvents();
    this.renderBody(this.body);
    this.#applyState();
    return this.element;
  }

  /**
   * Рамка виджета: заголовок с общими кнопками управления и тело.
   * Компоненты без «карточки» переопределяют метод (см. createPlainFrame).
   */
  renderFrame() {
    const { icon: iconName, accent } = this.meta;
    const titleId = `${this.id}-title`;
    const bodyId = `${this.id}-body`;

    const controls = el(
      'div',
      { class: 'widget__controls' },
      el('span', { class: 'widget__grip', attrs: { 'data-drag-handle': '', 'aria-hidden': 'true', title: 'Перетащите, чтобы изменить порядок' } }, icon('grip', { size: 18 })),
      this.#controlButton('move-up', 'chevronUp', `Переместить «${this.title}» выше`),
      this.#controlButton('move-down', 'chevronDown', `Переместить «${this.title}» ниже`),
      this.#controlButton('resize', 'resize', ''),
      this.#controlButton('toggle-minimize', 'collapse', ''),
      this.#controlButton('remove', 'x', `Удалить виджет «${this.title}»`),
    );

    const header = el(
      'header',
      { class: 'widget__header' },
      el('span', { class: 'widget__icon', attrs: { 'aria-hidden': 'true' } }, icon(iconName, { size: 18 })),
      el('h2', { class: 'widget__title', id: titleId, text: this.title, attrs: { tabindex: '-1' } }),
      controls,
    );

    this.body = el('div', { class: 'widget__body', id: bodyId });
    return el(
      'article',
      {
        class: ['widget', `accent-${accent}`],
        attrs: { 'aria-labelledby': titleId, 'data-widget-id': this.id, 'data-widget-type': this.type },
      },
      header,
      this.body,
    );
  }

  /** Простая рамка без заголовка и кнопок (для встроенных компонентов). */
  createPlainFrame(tag = 'div', className = '') {
    this.body = el(tag, { class: className });
    return this.body;
  }

  #controlButton(action, iconName, label) {
    return el(
      'button',
      { type: 'button', class: 'icon-btn icon-btn--sm', dataset: { action, control: action }, attrs: { 'aria-label': label } },
      icon(iconName, { size: 16 }),
    );
  }

  /** Абстрактный метод: заполнить тело компонента. */
  renderBody(body) {
    throw new Error(`${this.constructor.name} должен реализовать renderBody()`);
  }

  /** Вставить компонент в контейнер. */
  mount(container, { before = null } = {}) {
    const node = this.render();
    container.insertBefore(node, before);
    this.onMount();
    this.#flushCharts();
    return node;
  }

  /** Хук после вставки в DOM (например, для графиков, которым нужны размеры). */
  onMount() {}

  /**
   * Перерисовать тело. Значения незаполненных форм, открытые <details>
   * и фокус сохраняются, чтобы обновление данных не мешало вводу.
   */
  refresh() {
    if (!this.element || this.#destroyed) return;
    const snapshot = this.#captureUiState();
    this.destroyCharts();
    this.#pendingCharts = [];
    this.body.replaceChildren();
    this.renderBody(this.body);
    this.#restoreUiState(snapshot);
    this.#flushCharts();
  }

  #ownNodes(selector) {
    return [...this.body.querySelectorAll(selector)].filter(
      (node) => node.closest('[data-component-root]') === this.element,
    );
  }

  #formKey(form) {
    return `${form.dataset.form}|${form.dataset.key ?? ''}`;
  }

  #captureUiState() {
    const forms = new Map();
    for (const form of this.#ownNodes('form[data-form]')) {
      const values = {};
      for (const control of form.elements) {
        if (!control.name || control.dataset.noPersist !== undefined) continue;
        if (control.type === 'checkbox' || control.type === 'radio') {
          if (control.checked) values[`${control.name}=${control.value}`] = true;
          else values[`${control.name}=${control.value}`] = false;
        } else if (control.type !== 'submit' && control.type !== 'button') {
          values[control.name] = control.value;
        }
      }
      forms.set(this.#formKey(form), values);
    }
    const details = new Map(this.#ownNodes('details[data-key]').map((d) => [d.dataset.key, d.open]));

    let focus = null;
    const active = document.activeElement;
    if (active && this.body.contains(active)) {
      if (active.dataset.focusKey) focus = `[data-focus-key="${CSS.escape(active.dataset.focusKey)}"]`;
      else if (active.name && active.form?.dataset.form) {
        const f = active.form;
        const keyPart = f.dataset.key ? `[data-key="${CSS.escape(f.dataset.key)}"]` : '';
        focus = `form[data-form="${CSS.escape(f.dataset.form)}"]${keyPart} [name="${CSS.escape(active.name)}"]`;
      } else if (active.dataset.action) {
        const idPart = active.dataset.id ? `[data-id="${CSS.escape(active.dataset.id)}"]` : '';
        focus = `[data-action="${CSS.escape(active.dataset.action)}"]${idPart}`;
      }
    }
    return { forms, details, focus };
  }

  #restoreUiState({ forms, details, focus }) {
    for (const form of this.#ownNodes('form[data-form]')) {
      const values = forms.get(this.#formKey(form));
      if (!values) continue;
      for (const control of form.elements) {
        if (!control.name || control.dataset.noPersist !== undefined) continue;
        if (control.type === 'checkbox' || control.type === 'radio') {
          const v = values[`${control.name}=${control.value}`];
          if (v !== undefined) control.checked = v;
        } else if (control.name in values) {
          control.value = values[control.name];
        }
      }
    }
    for (const d of this.#ownNodes('details[data-key]')) {
      if (details.has(d.dataset.key)) d.open = details.get(d.dataset.key);
    }
    if (focus) this.body.querySelector(focus)?.focus({ preventScroll: true });
  }

  /* ================================================================ */
  /* Делегирование событий                                             */
  /* ================================================================ */

  #ownsTarget(node) {
    return node && node.closest('[data-component-root]') === this.element;
  }

  #bindDelegatedEvents() {
    this.addEventListenerWithCleanup(this.element, 'click', (event) => {
      const target = event.target.closest('[data-action]');
      if (!target || !this.#ownsTarget(target) || target.disabled) return;
      const { action } = target.dataset;
      switch (action) {
        case 'toggle-minimize':
          this.isMinimized ? this.restore() : this.minimize();
          break;
        case 'resize':
          this.setSize(SIZES[(SIZES.indexOf(this.size) + 1) % SIZES.length]);
          break;
        case 'move-up':
        case 'move-down':
        case 'remove':
          this.emit(action);
          break;
        default:
          this.onAction(action, target, event);
      }
    });

    this.addEventListenerWithCleanup(this.element, 'submit', (event) => {
      const form = event.target.closest('form[data-form]');
      if (!form || !this.#ownsTarget(form)) return;
      event.preventDefault();
      this.onSubmit(form.dataset.form, form, event);
    });

    for (const type of ['input', 'change']) {
      this.addEventListenerWithCleanup(this.element, type, (event) => {
        const target = event.target.closest('[data-field]');
        if (!target || !this.#ownsTarget(target)) return;
        if (type === 'input') this.onInput(target.dataset.field, target, event);
        else this.onChange(target.dataset.field, target, event);
      });
    }
  }

  /** Обработчики для наследников (полиморфизм через переопределение). */
  onAction(action, target, event) {}
  onSubmit(name, form, event) {}
  onInput(name, target, event) {}
  onChange(name, target, event) {}

  /** Запрос к «хозяину» (Dashboard) — всплывающее событие, без прямой ссылки на него. */
  emit(action, detail = {}) {
    this.element?.dispatchEvent(
      new CustomEvent('component:request', { bubbles: true, detail: { id: this.id, action, ...detail } }),
    );
  }

  /* ================================================================ */
  /* Общие действия виджета                                            */
  /* ================================================================ */

  minimize() {
    if (this.isMinimized) return;
    this.isMinimized = true;
    this.#applyState();
    this.emit('state');
  }

  restore() {
    if (!this.isMinimized) return;
    this.isMinimized = false;
    this.#applyState();
    this.emit('state');
    this.#flushCharts();
    this.onRestore();
  }

  /** Хук: после разворачивания (например, пересчитать график). */
  onRestore() {}

  setSize(size) {
    if (!SIZES.includes(size) || size === this.size) return;
    this.size = size;
    this.#applyState();
    this.emit('state');
  }

  /** Сохранить настройки экземпляра (независимое состояние виджета). */
  updateSettings(patch, { rerender = true } = {}) {
    this.settings = { ...this.settings, ...patch };
    this.emit('state');
    if (rerender) this.refresh();
  }

  #applyState() {
    if (!this.element) return;
    this.element.dataset.size = this.size;
    this.element.classList.toggle('is-minimized', this.isMinimized);
    if (this.body && this.element.classList.contains('widget')) this.body.hidden = this.isMinimized;

    const minBtn = this.element.querySelector(':scope > .widget__header [data-control="toggle-minimize"]');
    if (minBtn) {
      minBtn.setAttribute('aria-expanded', String(!this.isMinimized));
      minBtn.setAttribute('aria-controls', this.body.id);
      minBtn.setAttribute('aria-label', this.isMinimized ? `Развернуть «${this.title}»` : `Свернуть «${this.title}»`);
      minBtn.replaceChildren(icon(this.isMinimized ? 'expand' : 'collapse', { size: 16 }));
    }
    const sizeBtn = this.element.querySelector(':scope > .widget__header [data-control="resize"]');
    if (sizeBtn) {
      sizeBtn.setAttribute('aria-label', `Размер виджета: ${SIZE_LABELS[this.size]}. Изменить`);
      sizeBtn.replaceChildren(el('span', { class: 'size-badge', text: SIZE_SHORT[this.size], attrs: { 'aria-hidden': 'true' } }));
    }
  }

  /** Обновить доступность кнопок ↑ ↓ (вызывает Dashboard). */
  setPosition(index, total) {
    const up = this.element?.querySelector('[data-control="move-up"]');
    const down = this.element?.querySelector('[data-control="move-down"]');
    if (up) up.disabled = index === 0;
    if (down) down.disabled = index === total - 1;
  }

  /* ================================================================ */
  /* Управление ресурсами                                              */
  /* ================================================================ */

  /** Подписка, которая гарантированно снимается в destroy(). Возвращает функцию отписки. */
  addEventListenerWithCleanup(target, type, handler, options) {
    target.addEventListener(type, handler, options);
    const record = { target, type, handler, options };
    this.#listeners.push(record);
    return () => {
      target.removeEventListener(type, handler, options);
      this.#listeners = this.#listeners.filter((r) => r !== record);
    };
  }

  /** Подписка на изменения хранилища по списку ключей ('*' — массовое изменение). */
  watchStorage(keys, handler = () => this.refresh()) {
    const storage = this.services.storage;
    if (!storage) return () => {};
    const set = new Set(keys);
    return this.addEventListenerWithCleanup(storage, 'change', (event) => {
      const { key } = event.detail;
      if (key === '*' || set.has(key)) handler(key);
    });
  }

  setTimeout(fn, ms) {
    const handle = window.setTimeout(() => {
      this.#timeouts.delete(handle);
      fn();
    }, ms);
    this.#timeouts.add(handle);
    return handle;
  }

  clearTimeout(handle) {
    window.clearTimeout(handle);
    this.#timeouts.delete(handle);
  }

  setInterval(fn, ms) {
    const handle = window.setInterval(fn, ms);
    this.#intervals.add(handle);
    return handle;
  }

  clearInterval(handle) {
    window.clearInterval(handle);
    this.#intervals.delete(handle);
  }

  /**
   * Начать запрос: создаёт новый AbortController для канала `key`
   * и отменяет предыдущий незавершённый запрос этого канала (устаревший).
   */
  startRequest(key = 'default') {
    this.#controllers.get(key)?.abort();
    const controller = new AbortController();
    this.#controllers.set(key, controller);
    return controller;
  }

  /** Запрос завершён — controller больше не нужно отменять. */
  finishRequest(key, controller) {
    if (this.#controllers.get(key) === controller) this.#controllers.delete(key);
  }

  /** Отменить все незавершённые запросы компонента. */
  abortRequests() {
    for (const controller of this.#controllers.values()) controller.abort();
    this.#controllers.clear();
  }

  get pendingRequests() {
    return this.#controllers.size;
  }

  /** Зарегистрировать экземпляр Chart.js — будет уничтожен при перерисовке/удалении. */
  registerChart(chart) {
    if (chart) this.#charts.add(chart);
    return chart;
  }

  /**
   * Отложенное создание графика: Chart.js нужен canvas, уже вставленный
   * в документ и видимый. factory(canvas) должна вернуть экземпляр Chart.
   */
  queueChart(canvas, factory) {
    this.#pendingCharts.push({ canvas, factory });
  }

  #flushCharts() {
    if (this.isMinimized || !this.element?.isConnected) return;
    const queue = this.#pendingCharts;
    this.#pendingCharts = [];
    for (const { canvas, factory } of queue) {
      if (canvas.isConnected) this.registerChart(factory(canvas));
    }
  }

  destroyCharts() {
    for (const chart of this.#charts) chart.destroy();
    this.#charts.clear();
  }

  /** Дочерний компонент: уничтожается вместе с родителем. */
  addChild(child) {
    this.#children.add(child);
    return child;
  }

  removeChild(child) {
    if (this.#children.delete(child)) child.destroy();
  }

  /** Хук для очистки собственных ресурсов наследника. */
  onDestroy() {}

  destroy() {
    if (this.#destroyed) return;
    this.onDestroy();
    this.#destroyed = true;
    for (const child of this.#children) child.destroy();
    this.#children.clear();
    this.abortRequests();
    for (const handle of this.#timeouts) window.clearTimeout(handle);
    for (const handle of this.#intervals) window.clearInterval(handle);
    this.#timeouts.clear();
    this.#intervals.clear();
    this.destroyCharts();
    this.#pendingCharts = [];
    for (const { target, type, handler, options } of this.#listeners) {
      target.removeEventListener(type, handler, options);
    }
    this.#listeners = [];
    this.element?.remove();
    this.element = null;
    this.body = null;
  }

  /** Диагностика: сколько ресурсов удерживает компонент (используется в проверках). */
  get resourceStats() {
    return {
      listeners: this.#listeners.length,
      timeouts: this.#timeouts.size,
      intervals: this.#intervals.size,
      requests: this.#controllers.size,
      charts: this.#charts.size,
      children: this.#children.size,
    };
  }

  /** Сериализация для сохранения раскладки. */
  toJSON() {
    return { id: this.id, type: this.type, size: this.size, isMinimized: this.isMinimized, settings: this.settings };
  }
}
