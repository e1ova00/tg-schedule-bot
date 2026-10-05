/**
 * Небольшой набор переиспользуемых визуальных элементов (без состояния).
 * Все функции возвращают DOM-узлы, текст вставляется только через textContent.
 */
import { el, uid } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { clamp } from '../utils/calculations.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** Сегментированный переключатель (кнопки с aria-pressed). */
export function segmented({ action, options, value, label, size = '' }) {
  return el(
    'div',
    { class: ['segmented', size && `segmented--${size}`], attrs: { role: 'group', 'aria-label': label } },
    options.map((o) =>
      el('button', {
        type: 'button',
        class: 'segmented__btn',
        text: o.label,
        dataset: { action, value: o.value },
        attrs: { 'aria-pressed': String(o.value === value) },
      }),
    ),
  );
}

/** Горизонтальный прогресс-бар с подписью. */
export function progressBar({ value, max, accent, label, detail }) {
  const ratio = max > 0 ? clamp(value / max, 0, 1) : 0;
  const pct = Math.round(ratio * 100);
  return el(
    'div',
    { class: ['progress', `accent-${accent}`] },
    el('div', { class: 'progress__head' }, el('span', { class: 'progress__label', text: label }), el('span', { class: 'progress__detail', text: detail })),
    el(
      'div',
      { class: 'progress__track', attrs: { role: 'progressbar', 'aria-label': label, 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(pct), 'aria-valuetext': detail } },
      el('div', { class: 'progress__fill', style: { width: `${pct}%` } }),
    ),
  );
}

/**
 * Кольцо прогресса (Apple-like) в SVG.
 * ratio 0…1 (может быть > 1 — кольцо остаётся полным).
 */
export function ring({ ratio, accent, size = 120, stroke = 14, gradient = true, label }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const id = uid('grad');
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('class', `ring accent-${accent}`);
  svg.setAttribute('aria-hidden', 'true');

  if (gradient) {
    const defs = document.createElementNS(SVG_NS, 'defs');
    const lg = document.createElementNS(SVG_NS, 'linearGradient');
    lg.setAttribute('id', id);
    lg.setAttribute('x1', '0');
    lg.setAttribute('y1', '0');
    lg.setAttribute('x2', '1');
    lg.setAttribute('y2', '1');
    for (const [offset, cls] of [['0%', 'ring__stop-a'], ['100%', 'ring__stop-b']]) {
      const stop = document.createElementNS(SVG_NS, 'stop');
      stop.setAttribute('offset', offset);
      stop.setAttribute('class', cls);
      lg.append(stop);
    }
    defs.append(lg);
    svg.append(defs);
  }

  const make = (cls) => {
    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('cx', String(size / 2));
    circle.setAttribute('cy', String(size / 2));
    circle.setAttribute('r', String(r));
    circle.setAttribute('fill', 'none');
    circle.setAttribute('stroke-width', String(stroke));
    circle.setAttribute('class', cls);
    return circle;
  };
  const track = make('ring__track');
  const value = make('ring__value');
  value.setAttribute('stroke-linecap', 'round');
  value.setAttribute('stroke-dasharray', String(c));
  // Начальное состояние «пусто» → затем анимируем к нужному значению.
  value.setAttribute('stroke-dashoffset', String(c));
  value.setAttribute('transform', `rotate(-90 ${size / 2} ${size / 2})`);
  if (gradient) value.setAttribute('stroke', `url(#${id})`);
  svg.append(track, value);

  const target = c * (1 - clamp(Number(ratio) || 0, 0, 1));
  requestAnimationFrame(() => requestAnimationFrame(() => value.setAttribute('stroke-dashoffset', String(target))));

  const wrapper = el('div', { class: 'ring-wrap', style: { width: `${size}px`, height: `${size}px` } }, svg);
  if (label) wrapper.append(label);
  return wrapper;
}

/** Плитка с крупным числом. */
export function stat({ label, value, unit, accent, hint }) {
  return el(
    'div',
    { class: ['stat', accent && `accent-${accent}`] },
    el('span', { class: 'stat__label', text: label }),
    el('span', { class: 'stat__value' }, el('span', { text: value }), unit ? el('span', { class: 'stat__unit', text: ` ${unit}` }) : null),
    hint ? el('span', { class: 'stat__hint', text: hint }) : null,
  );
}

/** Пустое состояние внутри виджета. */
export function emptyState({ iconName = 'inbox', title, text, action }) {
  return el(
    'div',
    { class: 'empty-state' },
    el('span', { class: 'empty-state__icon', attrs: { 'aria-hidden': 'true' } }, icon(iconName, { size: 24 })),
    el('p', { class: 'empty-state__title', text: title }),
    text ? el('p', { class: 'empty-state__text', text }) : null,
    action ?? null,
  );
}

/** Шкала 1–5 на радиокнопках (доступна с клавиатуры стрелками). */
export function scale({ name, legend, value, minLabel, maxLabel, max = 5 }) {
  return el(
    'fieldset',
    { class: 'scale' },
    el('legend', { class: 'scale__legend', text: legend }),
    el(
      'div',
      { class: 'scale__options' },
      Array.from({ length: max }, (_, i) => {
        const v = String(i + 1);
        const id = uid('sc');
        return el(
          'span',
          { class: 'scale__option' },
          el('input', { type: 'radio', name, value: v, id, checked: String(value) === v, class: 'scale__input', required: true }),
          el('label', { for: id, class: 'scale__label', text: v }),
        );
      }),
    ),
    el('div', { class: 'scale__ends', attrs: { 'aria-hidden': 'true' } }, el('span', { text: minLabel }), el('span', { text: maxLabel })),
  );
}

/** Кнопка с иконкой и текстом. */
export function button(label, { action, variant = 'secondary', iconName, type = 'button', size, dataset = {}, attrs, disabled } = {}) {
  return el(
    'button',
    { type, class: ['btn', `btn--${variant}`, size && `btn--${size}`], dataset: { action, ...dataset }, attrs, disabled },
    iconName ? icon(iconName, { size: 16 }) : null,
    el('span', { text: label }),
  );
}

/** Иконочная кнопка (обязательно с aria-label). */
export function iconButton(iconName, ariaLabel, { action, dataset = {}, variant = '' } = {}) {
  return el(
    'button',
    { type: 'button', class: ['icon-btn', variant && `icon-btn--${variant}`], dataset: { action, ...dataset }, attrs: { 'aria-label': ariaLabel } },
    icon(iconName, { size: 16 }),
  );
}

/** Сноска-дисклеймер. */
export function note(text) {
  return el('p', { class: 'note' }, icon('info', { size: 14 }), el('span', { text }));
}
