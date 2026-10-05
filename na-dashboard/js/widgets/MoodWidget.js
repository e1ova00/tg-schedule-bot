/**
 * MoodWidget — самочувствие: «сейчас» и «день в целом».
 * Шаг 1 — плавный слайдер «Очень плохо ←→ Очень хорошо» (7 уровней),
 * шаг 2 — факторы влияния (множественный выбор) и необязательная заметка.
 * История отображается на графике.
 */
import { UIComponent } from '../core/UIComponent.js';
import { el, field } from '../utils/dom.js';
import { addDays, formatShortDate, formatTime, lastNDates, relativeDayLabel, today } from '../utils/date.js';
import { mean } from '../utils/calculations.js';
import { fmt } from '../utils/format.js';
import { MOOD_FACTORS, MOOD_LEVELS } from '../data/catalog.js';
import { chartContainer, chartFallback, createLineChart } from '../utils/charts.js';
import { button, emptyState, segmented } from '../ui/kit.js';

const KINDS = [
  { value: 'now', label: 'Сейчас', question: 'Как вы чувствуете себя сейчас?' },
  { value: 'day', label: 'День в целом', question: 'Как прошёл день в целом?' },
];

export class MoodWidget extends UIComponent {
  static type = 'mood';
  static meta = {
    title: 'Самочувствие',
    icon: 'smile',
    accent: 'mood',
    description: 'Оценка состояния, факторы влияния и график настроения.',
  };

  // Независимое локальное состояние экземпляра.
  #value = 4;
  #step = 'rate';
  #factors = new Set();
  #note = '';
  constructor(config) {
    super(config);
    this.settings = { kind: 'now', ...this.settings };
    this.watchStorage(['mood']);
    this.addEventListenerWithCleanup(this.services.theme, 'change', () => this.refresh());
  }

  get #storage() {
    return this.services.storage;
  }

  renderBody(body) {
    const kind = KINDS.find((k) => k.value === this.settings.kind) ?? KINDS[0];
    body.append(segmented({ action: 'kind', options: KINDS, value: kind.value, label: 'Тип записи' }));
    if (this.#step === 'rate') this.#renderRate(body, kind);
    else this.#renderFactors(body);
    this.#renderHistory(body);
  }

  #renderRate(body, kind) {
    const label = MOOD_LEVELS[this.#value - 1];
    const slider = el('input', {
      type: 'range',
      class: 'mood-slider',
      min: 1,
      max: 7,
      step: 1,
      value: this.#value,
      dataset: { field: 'mood-value', focusKey: 'mood-slider' },
      attrs: { 'aria-valuetext': label, 'aria-describedby': `${this.id}-mood-label` },
      style: { '--mood-pos': `${((this.#value - 1) / 6) * 100}%` },
    });
    slider.id = `${this.id}-slider`;
    body.append(
      el(
        'div',
        { class: 'mood' },
        el('h3', { class: 'mood__question', text: kind.question }),
        el('div', { class: 'mood__orb', dataset: { level: this.#value }, attrs: { 'aria-hidden': 'true' } }),
        el('p', { class: 'mood__label', id: `${this.id}-mood-label`, text: label, attrs: { 'aria-live': 'polite' } }),
        el('label', { class: 'sr-only', for: slider.id, text: kind.question }),
        slider,
        el('div', { class: 'mood__ends', attrs: { 'aria-hidden': 'true' } }, el('span', { text: 'Очень плохо' }), el('span', { text: 'Очень хорошо' })),
        button('Далее', { action: 'next', variant: 'primary', dataset: { focusKey: 'mood-next' } }),
      ),
    );
  }

  #renderFactors(body) {
    body.append(
      el(
        'div',
        { class: 'mood' },
        el('p', { class: 'mood__summary' }, el('span', { class: 'mood__dot', dataset: { level: this.#value }, attrs: { 'aria-hidden': 'true' } }), MOOD_LEVELS[this.#value - 1]),
        el('h3', { class: 'mood__question', id: `${this.id}-factors`, text: 'Что больше всего влияет на ваше состояние?' }),
        el(
          'div',
          { class: 'chips chips--toggle', attrs: { role: 'group', 'aria-labelledby': `${this.id}-factors` } },
          MOOD_FACTORS.map((f) =>
            el('button', {
              type: 'button',
              class: 'chip chip--toggle',
              text: f,
              dataset: { action: 'factor', value: f, focusKey: `factor-${f}` },
              attrs: { 'aria-pressed': String(this.#factors.has(f)) },
            }),
          ),
        ),
        field(
          'Заметка (необязательно)',
          el('textarea', { class: 'input', name: 'note', rows: 2, value: this.#note, dataset: { field: 'mood-note', focusKey: 'mood-note' }, attrs: { maxlength: 300 } }),
        ),
        el('div', { class: 'actions' }, button('Назад', { action: 'back', variant: 'ghost' }), button('Сохранить', { action: 'save', variant: 'primary', iconName: 'check' })),
      ),
    );
  }

  #renderHistory(body) {
    const entries = this.#storage.list('mood');
    body.append(el('h3', { class: 'section-title', text: 'История за 30 дней' }));
    if (!entries.length) {
      body.append(emptyState({ iconName: 'smile', title: 'Записей пока нет', text: 'Отметьте самочувствие — здесь появится график.' }));
      return;
    }
    const dates = lastNDates(30);
    const series = (kind) =>
      dates.map((d) => {
        const values = entries.filter((e) => e.date === d && e.kind === kind).map((e) => e.value);
        return values.length ? Math.round(mean(values) * 10) / 10 : null;
      });
    const now = series('now');
    const day = series('day');
    const filled = [...now, ...day].filter((v) => v !== null);
    const avg = mean(filled);
    const summary = `Самочувствие за 30 дней: ${filled.length} отметок, в среднем ${fmt(avg, 1)} из 7`;

    if (window.Chart) {
      const { wrapper, canvas } = chartContainer(summary, { height: 170 });
      this.queueChart(canvas, (c) =>
          createLineChart(c, {
            labels: dates.map(formatShortDate),
            datasets: [
              { label: 'Сейчас', data: now, accent: 'mood' },
              { label: 'День в целом', data: day, accent: 'mood-alt', dashed: true },
            ],
            suggestedMin: 1,
            suggestedMax: 7,
            stepSize: 1,
            legend: true,
          }),
      );
      body.append(wrapper);
    } else {
      body.append(chartFallback(summary));
    }

    const last = [...entries].sort((a, b) => b.at.localeCompare(a.at))[0];
    body.append(
      el(
        'p',
        { class: 'muted small' },
        `Последняя запись: ${MOOD_LEVELS[last.value - 1]} · ${relativeDayLabel(last.date)}, ${formatTime(last.at)}`,
        last.factors?.length ? ` · ${last.factors.join(', ')}` : '',
      ),
    );
    const weekAvg = mean(entries.filter((e) => e.date > addDays(today(), -7)).map((e) => e.value));
    if (weekAvg !== null) body.append(el('p', { class: 'muted small', text: `Среднее за 7 дней: ${fmt(weekAvg, 1)} из 7` }));
  }

  /* ------------------------- События ------------------------- */

  onInput(name, target) {
    if (name === 'mood-value') {
      // Обновляем только нужные узлы — без полной перерисовки, чтобы слайдер не терял фокус.
      this.#value = Number(target.value);
      const label = MOOD_LEVELS[this.#value - 1];
      target.setAttribute('aria-valuetext', label);
      target.style.setProperty('--mood-pos', `${((this.#value - 1) / 6) * 100}%`);
      const labelNode = this.body.querySelector('.mood__label');
      if (labelNode) labelNode.textContent = label;
      const orb = this.body.querySelector('.mood__orb');
      if (orb) orb.dataset.level = String(this.#value);
    }
    if (name === 'mood-note') this.#note = target.value;
  }

  onAction(action, target) {
    switch (action) {
      case 'kind':
        this.updateSettings({ kind: target.dataset.value });
        break;
      case 'next':
        this.#step = 'factors';
        this.refresh();
        this.body.querySelector('.chip--toggle')?.focus();
        break;
      case 'back':
        this.#step = 'rate';
        this.refresh();
        this.body.querySelector('.mood-slider')?.focus();
        break;
      case 'factor': {
        const f = target.dataset.value;
        if (this.#factors.has(f)) this.#factors.delete(f);
        else this.#factors.add(f);
        target.setAttribute('aria-pressed', String(this.#factors.has(f)));
        break;
      }
      case 'save':
        this.#save();
        break;
      default:
    }
  }

  #save() {
    const now = new Date();
    const entry = {
      at: now.toISOString(),
      date: today(),
      kind: this.settings.kind,
      value: this.#value,
      factors: [...this.#factors],
      note: this.#note.trim().slice(0, 300),
    };
    this.#step = 'rate';
    this.#factors.clear();
    this.#note = '';
    this.#value = 4;
    this.#storage.add('mood', entry);
    this.services.toast.show('Самочувствие сохранено');
    this.body?.querySelector('.mood-slider')?.focus();
  }

}
