/**
 * AsyncView — отображение четырёх обязательных состояний API-запроса:
 * loading / success / empty / error (+ idle до первого запроса).
 * Кнопка «Повторить» использует data-action, поэтому её обрабатывает
 * делегированный обработчик компонента-владельца.
 */
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';

export class AsyncView {
  #root;

  constructor(container) {
    this.#root = container;
    this.#root.classList.add('async-view');
  }

  #set(state, ...children) {
    this.#root.dataset.state = state;
    this.#root.setAttribute('aria-busy', String(state === 'loading'));
    this.#root.replaceChildren(...children);
  }

  #message(iconName, title, text, extra) {
    return el(
      'div',
      { class: 'state-message' },
      el('span', { class: 'state-message__icon', attrs: { 'aria-hidden': 'true' } }, icon(iconName, { size: 22 })),
      el('p', { class: 'state-message__title', text: title }),
      text ? el('p', { class: 'state-message__text', text }) : null,
      extra,
    );
  }

  idle(title, text, action) {
    this.#set('idle', this.#message('search', title, text, action));
  }

  loading(text = 'Загружаем…') {
    const skeleton = el(
      'div',
      { class: 'skeleton-list', attrs: { 'aria-hidden': 'true' } },
      Array.from({ length: 3 }, () => el('div', { class: 'skeleton-row' }, el('span', { class: 'skeleton skeleton--title' }), el('span', { class: 'skeleton skeleton--line' }))),
    );
    this.#set('loading', el('p', { class: 'sr-only', text }), el('p', { class: 'async-view__loading-text', text, attrs: { 'aria-hidden': 'true' } }), skeleton);
  }

  empty(title = 'Ничего не найдено', text = 'Попробуйте изменить запрос.') {
    this.#set('empty', this.#message('inbox', title, text));
  }

  error(text, { retryAction = 'retry' } = {}) {
    const retry = el(
      'button',
      { type: 'button', class: 'btn btn--secondary btn--sm', dataset: { action: retryAction } },
      icon('refresh', { size: 16 }),
      'Повторить',
    );
    this.#set('error', this.#message('alert', 'Не удалось загрузить данные', text, retry));
    this.#root.querySelector('.state-message')?.setAttribute('role', 'alert');
  }

  success(...children) {
    this.#set('success', ...children);
  }
}
