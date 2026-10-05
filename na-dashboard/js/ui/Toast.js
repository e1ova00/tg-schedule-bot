/**
 * Ненавязчивые уведомления (вместо alert). Озвучиваются скринридером через aria-live.
 */
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';

const ICONS = { success: 'check', info: 'info', error: 'alert', pr: 'trophy' };

export class Toast {
  #region;

  constructor() {
    this.#region = el('div', { class: 'toast-region', attrs: { role: 'status', 'aria-live': 'polite' } });
    document.body.append(this.#region);
  }

  show(message, { variant = 'success', duration = 3800 } = {}) {
    const node = el(
      'div',
      { class: ['toast', `toast--${variant}`] },
      el('span', { class: 'toast__icon', attrs: { 'aria-hidden': 'true' } }, icon(ICONS[variant] ?? 'info', { size: 18 })),
      el('span', { class: 'toast__text', text: message }),
    );
    this.#region.append(node);
    requestAnimationFrame(() => node.classList.add('is-visible'));
    window.setTimeout(() => {
      node.classList.remove('is-visible');
      window.setTimeout(() => node.remove(), 300);
    }, duration);
  }
}
