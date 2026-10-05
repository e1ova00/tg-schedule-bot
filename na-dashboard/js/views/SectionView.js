/**
 * SectionView — один раздел приложения (Сегодня, Тренировки …).
 * Состоит из заголовка, необязательных «верхних» компонентов и собственного Dashboard.
 * destroy() уничтожает всё содержимое — используется Router при переключении.
 */
import { Dashboard } from '../core/Dashboard.js';
import { Modal } from '../ui/Modal.js';
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';

export class SectionView {
  #config;
  #ctx;
  #root = null;
  #dashboard = null;
  #components = [];
  #listeners = [];

  /**
   * @param {object} config
   * @param {string} config.key — ключ раздела и раскладки
   * @param {string} config.title
   * @param {string} config.subtitle
   * @param {Array} config.defaultLayout
   * @param {Function} [config.header] — (ctx) => UIComponent[] над сеткой виджетов
   */
  constructor(config, ctx) {
    this.#config = config;
    this.#ctx = ctx;
  }

  get dashboard() {
    return this.#dashboard;
  }

  mount(outlet) {
    const { key, title, subtitle } = this.#config;
    const addButton = el(
      'button',
      { type: 'button', class: 'btn btn--secondary', dataset: { sectionAction: 'add-widget' } },
      icon('plus', { size: 18 }),
      el('span', { text: 'Добавить виджет' }),
    );
    const header = el(
      'header',
      { class: 'section-header' },
      el('div', {}, el('h1', { class: 'section-header__title', text: title, attrs: { tabindex: '-1' } }), el('p', { class: 'section-header__subtitle', text: subtitle })),
      addButton,
    );
    const top = el('div', { class: 'section-top' });
    const widgetsHeading = el('h2', { class: 'sr-only', text: `Виджеты раздела «${title}»` });
    this.#root = el('section', { class: `section section--${key}`, attrs: { 'aria-labelledby': `section-title-${key}` } }, header, top, widgetsHeading);
    header.querySelector('h1').id = `section-title-${key}`;
    outlet.append(this.#root);

    for (const component of this.#config.header?.(this.#ctx) ?? []) {
      component.mount(top);
      this.#components.push(component);
    }
    if (!this.#components.length) top.remove();

    this.#dashboard = new Dashboard({
      registry: this.#ctx.registry,
      storage: this.#ctx.storage,
      layoutKey: key,
      defaultLayout: this.#config.defaultLayout,
      services: this.#ctx.services,
      announce: this.#ctx.services.announce,
      onAddRequest: () => this.openAddDialog(),
    });
    this.#dashboard.mount(this.#root);

    const onClick = (event) => {
      if (event.target.closest('[data-section-action="add-widget"]')) this.openAddDialog();
    };
    this.#root.addEventListener('click', onClick);
    this.#listeners.push(() => this.#root?.removeEventListener('click', onClick));
  }

  /** Каталог виджетов: выбор типа → dashboard.addWidget(type). */
  async openAddDialog() {
    const catalog = this.#ctx.registry.list();
    const list = el(
      'ul',
      { class: 'catalog' },
      catalog.map((item) =>
        el(
          'li',
          {},
          el(
            'button',
            { type: 'button', class: ['catalog__item', `accent-${item.accent}`], dataset: { modalValue: item.type } },
            el('span', { class: 'catalog__icon', attrs: { 'aria-hidden': 'true' } }, icon(item.icon, { size: 20 })),
            el('span', { class: 'catalog__text' }, el('span', { class: 'catalog__title', text: item.title }), el('span', { class: 'catalog__desc', text: item.description })),
            el('span', { class: 'catalog__plus', attrs: { 'aria-hidden': 'true' } }, icon('plus', { size: 18 })),
          ),
        ),
      ),
    );
    const modal = new Modal({
      title: 'Добавить виджет',
      description: `Виджет появится в конце раздела «${this.#config.title}». Порядок можно изменить кнопками ↑ ↓ или перетаскиванием.`,
      content: list,
    }).open();
    const type = await modal.closed;
    if (!this.#dashboard || !this.#ctx.registry.has(type)) return;
    const widget = this.#dashboard.addWidget(type);
    widget?.element.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest' });
    widget?.element.querySelector('.widget__title')?.focus({ preventScroll: true });
  }

  destroy() {
    this.#components.forEach((c) => c.destroy());
    this.#components = [];
    this.#dashboard?.destroy();
    this.#dashboard = null;
    this.#listeners.forEach((off) => off());
    this.#listeners = [];
    this.#root?.remove();
    this.#root = null;
  }
}
