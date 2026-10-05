/**
 * Безопасные DOM-хелперы.
 * Всё содержимое попадает в DOM только через textContent / createElement,
 * поэтому пользовательские строки и данные API не могут внедрить разметку.
 */

let idCounter = 0;

/** Уникальный id для связки label ↔ input. */
export function uid(prefix = 'na') {
  idCounter += 1;
  return `${prefix}-${idCounter.toString(36)}`;
}

/**
 * Создать элемент.
 * el('button', { class: 'btn', text: 'OK', attrs: { 'aria-label': '…' }, dataset: { action: 'save' } }, child1, child2)
 * Строки-потомки превращаются в текстовые узлы (никогда не в HTML).
 */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    switch (key) {
      case 'class':
        node.className = Array.isArray(value) ? value.filter(Boolean).join(' ') : value;
        break;
      case 'text':
        node.textContent = String(value);
        break;
      case 'dataset':
        for (const [dk, dv] of Object.entries(value)) {
          if (dv !== undefined && dv !== null) node.dataset[dk] = String(dv);
        }
        break;
      case 'attrs':
        for (const [ak, av] of Object.entries(value)) {
          if (av === undefined || av === null || av === false) continue;
          node.setAttribute(ak, av === true ? '' : String(av));
        }
        break;
      case 'style':
        for (const [sk, sv] of Object.entries(value)) node.style.setProperty(sk, sv);
        break;
      default:
        if (key in node) node[key] = value;
        else node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  append(node, children);
  return node;
}

/** Добавить потомков (строки → текстовые узлы, массивы разворачиваются). */
export function append(parent, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

/**
 * Поле формы с корректной связкой label/for, подсказкой и местом для ошибки.
 * control — уже созданный input/select/textarea.
 */
export function field(labelText, control, { hint, className } = {}) {
  if (!control.id) control.id = uid('f');
  const children = [el('label', { class: 'field__label', for: control.id, text: labelText }), control];
  if (hint) {
    const hintId = uid('h');
    control.setAttribute('aria-describedby', hintId);
    children.push(el('span', { class: 'field__hint', id: hintId, text: hint }));
  }
  return el('div', { class: ['field', className] }, children);
}

/** <select> из массива [{ value, label }] или групп [{ group, options }] */
export function select(name, options, { value, required, attrs } = {}) {
  const node = el('select', { class: 'input', name, required, attrs });
  for (const opt of options) {
    if (opt.group) {
      const group = el('optgroup', { label: opt.group });
      for (const o of opt.options) group.append(el('option', { value: o.value, text: o.label }));
      node.append(group);
    } else {
      node.append(el('option', { value: opt.value, text: opt.label }));
    }
  }
  if (value !== undefined && value !== null) node.value = String(value);
  return node;
}

export function input(name, { type = 'text', value, attrs, required, placeholder } = {}) {
  const node = el('input', { class: 'input', name, type, required, placeholder, attrs });
  if (value !== undefined && value !== null) node.value = String(value);
  return node;
}

/** Визуально скрытый текст для скринридеров. */
export function srOnly(text) {
  return el('span', { class: 'sr-only', text });
}

/** Прочитать значения формы в объект (числа приводятся вызывающим кодом). */
export function formValues(form) {
  const data = {};
  for (const [key, value] of new FormData(form).entries()) {
    data[key] = typeof value === 'string' ? value.trim() : value;
  }
  return data;
}

/** Показать ошибку валидации у поля (aria-invalid + текст). */
export function setFieldError(control, message) {
  const wrapper = control.closest('.field') ?? control.parentElement;
  let error = wrapper?.querySelector('.field__error');
  if (!message) {
    control.removeAttribute('aria-invalid');
    error?.remove();
    return;
  }
  control.setAttribute('aria-invalid', 'true');
  if (!error && wrapper) {
    error = el('span', { class: 'field__error', id: uid('e'), attrs: { role: 'alert' } });
    wrapper.append(error);
    const describedBy = control.getAttribute('aria-describedby');
    control.setAttribute('aria-describedby', [describedBy, error.id].filter(Boolean).join(' '));
  }
  if (error) error.textContent = message;
}

export function clearFormErrors(form) {
  form.querySelectorAll('[aria-invalid="true"]').forEach((c) => setFieldError(c, null));
}

/** Безопасно получить текст из HTML-строки API (без исполнения и без вставки разметки). */
export function htmlToText(html) {
  if (typeof html !== 'string' || html.length === 0) return '';
  const doc = new DOMParser().parseFromString(html, 'text/html');
  return (doc.body.textContent ?? '').replace(/\s+/g, ' ').trim();
}

export const prefersReducedMotion = () =>
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
