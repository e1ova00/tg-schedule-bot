/**
 * Модальные окна на основе нативного <dialog>:
 * showModal() даёт ловушку фокуса, Esc и inert-фон «из коробки».
 * После закрытия фокус возвращается на элемент, который открыл окно.
 */
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';

export class Modal {
  #dialog;
  #opener;
  #resolve;
  #onClose;
  closed;

  /**
   * @param {object} options
   * @param {string} options.title
   * @param {Node|Node[]} options.content
   * @param {{label:string, value:string, variant?:string, autofocus?:boolean}[]} [options.actions]
   * @param {boolean} [options.dismissible=true] — можно ли закрыть по Esc / клику по фону
   */
  constructor({ title, description, content, actions = [], dismissible = true, className = '', onClose } = {}) {
    this.#opener = document.activeElement;
    this.#onClose = onClose;
    this.closed = new Promise((resolve) => {
      this.#resolve = resolve;
    });

    const titleId = `modal-title-${Math.random().toString(36).slice(2, 8)}`;
    const descId = description ? `${titleId}-desc` : undefined;
    const header = el(
      'header',
      { class: 'modal__header' },
      el('h2', { class: 'modal__title', id: titleId, text: title }),
      dismissible
        ? el('button', { type: 'button', class: 'icon-btn', dataset: { modalValue: 'cancel' }, attrs: { 'aria-label': 'Закрыть' } }, icon('x', { size: 18 }))
        : null,
    );
    const footer = actions.length
      ? el(
          'footer',
          { class: 'modal__footer' },
          actions.map((a) =>
            el('button', {
              type: 'button',
              class: ['btn', `btn--${a.variant ?? 'secondary'}`],
              text: a.label,
              autofocus: a.autofocus,
              dataset: { modalValue: a.value },
            }),
          ),
        )
      : null;

    this.#dialog = el(
      'dialog',
      { class: ['modal', className], attrs: { 'aria-labelledby': titleId, 'aria-describedby': descId } },
      el(
        'div',
        { class: 'modal__panel' },
        header,
        description ? el('p', { class: 'modal__description', id: descId, text: description }) : null,
        el('div', { class: 'modal__content' }, content),
        footer,
      ),
    );

    this.#dialog.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-modal-value]');
      if (btn) this.close(btn.dataset.modalValue);
      else if (event.target === this.#dialog && dismissible) this.close('cancel');
    });
    this.#dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      if (dismissible) this.close('cancel');
    });
  }

  get element() {
    return this.#dialog;
  }

  open() {
    document.body.append(this.#dialog);
    this.#dialog.showModal();
    const auto = this.#dialog.querySelector('[autofocus]');
    (auto ?? this.#dialog.querySelector('input, select, textarea, button'))?.focus();
    return this;
  }

  close(value = 'cancel') {
    if (!this.#dialog.open) return;
    this.#dialog.close();
    this.#dialog.remove();
    this.#onClose?.(value);
    this.#resolve(value);
    if (this.#opener?.isConnected) this.#opener.focus();
  }

  /** Подтверждение действия. Возвращает Promise<boolean>. */
  static async confirm({ title, message, confirmLabel = 'Подтвердить', cancelLabel = 'Отмена', danger = false }) {
    const modal = new Modal({
      title,
      className: 'modal--compact',
      content: el('p', { class: 'modal__message', text: message }),
      actions: [
        { label: cancelLabel, value: 'cancel', variant: 'secondary', autofocus: true },
        { label: confirmLabel, value: 'confirm', variant: danger ? 'danger' : 'primary' },
      ],
    }).open();
    return (await modal.closed) === 'confirm';
  }
}
