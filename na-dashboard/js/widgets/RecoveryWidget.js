/**
 * RecoveryWidget — утренний check-in и прозрачный «Индекс восстановления 0–100».
 * Формула вынесена в utils/calculations.js (recoveryScore) и раскрыта в интерфейсе.
 */
import { UIComponent } from '../core/UIComponent.js';
import { el, field, input, formValues, setFieldError, clearFormErrors } from '../utils/dom.js';
import { formatHours, formatShortDate, lastNDates, today } from '../utils/date.js';
import { RECOVERY_WEIGHTS, recoveryScore, sleepHoursBetween } from '../utils/calculations.js';
import { fmt } from '../utils/format.js';
import { chartContainer, chartFallback, createBarChart } from '../utils/charts.js';
import { button, note, progressBar, ring, scale } from '../ui/kit.js';

export class RecoveryWidget extends UIComponent {
  static type = 'recovery';
  static meta = {
    title: 'Восстановление',
    icon: 'moon',
    accent: 'recovery',
    description: 'Утренний check-in: сон, энергия, усталость, стресс.',
  };

  #editing = false;
  constructor(config) {
    super(config);
    this.watchStorage(['recovery', 'profile']);
    this.addEventListenerWithCleanup(this.services.theme, 'change', () => this.refresh());
  }

  get #storage() {
    return this.services.storage;
  }

  renderBody(body) {
    const entry = this.#storage.list('recovery').find((r) => r.date === today());
    if (!entry || this.#editing) this.#renderForm(body, entry);
    else this.#renderResult(body, entry);
  }

  #renderForm(body, entry) {
    const profile = this.#storage.get('profile') ?? {};
    const bed = entry?.bedTime ?? profile.sleepTime ?? '23:00';
    const wake = entry?.wakeTime ?? profile.wakeTime ?? '07:00';
    const hours = sleepHoursBetween(bed, wake);

    body.append(
      el('p', { class: 'lead', text: entry ? 'Изменить утренний check-in' : 'Утренний check-in — около 20 секунд.' }),
      el(
        'form',
        { class: 'stack', dataset: { form: 'checkin' }, attrs: { novalidate: true } },
        el(
          'div',
          { class: 'form-grid form-grid--2' },
          field('Время засыпания', input('bedTime', { type: 'time', value: bed, attrs: { 'data-field': 'sleep-time' }, required: true })),
          field('Время пробуждения', input('wakeTime', { type: 'time', value: wake, attrs: { 'data-field': 'sleep-time' }, required: true })),
        ),
        el('p', { class: 'sleep-preview', attrs: { 'aria-live': 'polite' } }, 'Продолжительность сна: ', el('strong', { class: 'js-sleep-hours', text: formatHours(hours) })),
        scale({ name: 'sleepQuality', legend: 'Качество сна', value: entry?.sleepQuality, minLabel: 'Плохо', maxLabel: 'Отлично' }),
        scale({ name: 'energy', legend: 'Энергия', value: entry?.energy, minLabel: 'Мало', maxLabel: 'Много' }),
        scale({ name: 'soreness', legend: 'Мышечная усталость', value: entry?.soreness, minLabel: 'Нет', maxLabel: 'Сильная' }),
        scale({ name: 'stress', legend: 'Стресс', value: entry?.stress, minLabel: 'Низкий', maxLabel: 'Высокий' }),
        el(
          'div',
          { class: 'actions' },
          button('Рассчитать индекс', { type: 'submit', variant: 'primary', iconName: 'check' }),
          entry ? button('Отмена', { action: 'cancel-edit', variant: 'ghost' }) : null,
        ),
      ),
    );
  }

  #renderResult(body, entry) {
    const { score, parts } = recoveryScore(entry);
    const level = score >= 75 ? 'Хорошее восстановление' : score >= 50 ? 'Умеренное восстановление' : 'Восстановление ниже обычного';
    body.append(
      el(
        'div',
        { class: 'score-hero' },
        ring({
          ratio: score / 100,
          accent: 'recovery',
          size: 132,
          stroke: 14,
          label: el('div', { class: 'ring-label' }, el('span', { class: 'ring-label__value', text: String(score) }), el('span', { class: 'ring-label__unit', text: 'из 100' })),
        }),
        el(
          'div',
          { class: 'score-hero__text' },
          el('p', { class: 'eyebrow', text: 'Индекс восстановления' }),
          el('p', { class: 'score-hero__title', text: level }),
          el('p', { class: 'muted', text: `Сон ${formatHours(entry.sleepHours)} · ${entry.bedTime} → ${entry.wakeTime}` }),
        ),
      ),
      el(
        'div',
        { class: 'breakdown' },
        parts.map((p) => progressBar({ value: p.value, max: p.max, accent: 'recovery', label: p.label, detail: `${fmt(p.value, 1)} / ${p.max}` })),
      ),
      el(
        'details',
        { class: 'disclosure', dataset: { key: 'formula' } },
        el('summary', { class: 'disclosure__summary', text: 'Как считается индекс?' }),
        el(
          'ul',
          { class: 'formula' },
          el('li', { text: `${RECOVERY_WEIGHTS.sleep.label}: до ${RECOVERY_WEIGHTS.sleep.max} баллов — часы сна / 8 (не больше 1).` }),
          el('li', { text: `${RECOVERY_WEIGHTS.sleepQuality.label}: до ${RECOVERY_WEIGHTS.sleepQuality.max} — (оценка − 1) / 4.` }),
          el('li', { text: `${RECOVERY_WEIGHTS.energy.label}: до ${RECOVERY_WEIGHTS.energy.max} — (оценка − 1) / 4.` }),
          el('li', { text: `${RECOVERY_WEIGHTS.soreness.label}: до ${RECOVERY_WEIGHTS.soreness.max} — (5 − оценка) / 4.` }),
          el('li', { text: `${RECOVERY_WEIGHTS.stress.label}: до ${RECOVERY_WEIGHTS.stress.max} — (5 − оценка) / 4.` }),
        ),
        note('Индекс — сводка ваших собственных ответов, а не медицинский показатель.'),
      ),
      button('Изменить check-in', { action: 'edit', variant: 'secondary', iconName: 'edit' }),
    );
    this.#renderTrend(body);
  }

  #renderTrend(body) {
    const records = new Map(this.#storage.list('recovery').map((r) => [r.date, r.score]));
    const dates = lastNDates(14);
    const data = dates.map((d) => records.get(d) ?? null);
    if (data.filter((v) => v !== null).length < 2) return;
    const summary = `Индекс восстановления за 14 дней: ${data.filter((v) => v !== null).join(', ')}`;
    body.append(el('h3', { class: 'section-title', text: '14 дней' }));
    if (!window.Chart) {
      body.append(chartFallback(summary));
      return;
    }
    const { wrapper, canvas } = chartContainer(summary, { height: 140 });
    this.queueChart(canvas, (c) => createBarChart(c, { labels: dates.map(formatShortDate), data, accent: 'recovery', label: 'Индекс', suggestedMax: 100 }));
    body.append(wrapper);
  }

  /* ------------------------- События ------------------------- */

  onInput(name) {
    if (name !== 'sleep-time') return;
    const form = this.body.querySelector('form[data-form="checkin"]');
    const hours = sleepHoursBetween(form.elements.bedTime.value, form.elements.wakeTime.value);
    const node = this.body.querySelector('.js-sleep-hours');
    if (node) node.textContent = formatHours(hours);
  }

  onAction(action) {
    if (action === 'edit') {
      this.#editing = true;
      this.refresh();
      this.body.querySelector('input')?.focus();
    }
    if (action === 'cancel-edit') {
      this.#editing = false;
      this.refresh();
    }
  }

  onSubmit(name, form) {
    if (name !== 'checkin') return;
    clearFormErrors(form);
    const v = formValues(form);
    const sleepHours = sleepHoursBetween(v.bedTime, v.wakeTime);
    if (sleepHours === null || sleepHours > 16) {
      setFieldError(form.elements.wakeTime, 'Проверьте время сна');
      return;
    }
    const missing = ['sleepQuality', 'energy', 'soreness', 'stress'].filter((k) => !v[k]);
    if (missing.length) {
      const first = form.querySelector(`input[name="${missing[0]}"]`);
      first?.focus();
      this.services.toast.show('Отметьте все четыре шкалы', { variant: 'error' });
      return;
    }
    const record = {
      date: today(),
      bedTime: v.bedTime,
      wakeTime: v.wakeTime,
      sleepHours,
      sleepQuality: Number(v.sleepQuality),
      energy: Number(v.energy),
      soreness: Number(v.soreness),
      stress: Number(v.stress),
    };
    record.score = recoveryScore(record).score;
    this.#editing = false;
    const existing = this.#storage.list('recovery').find((r) => r.date === record.date);
    if (existing) this.#storage.updateItem('recovery', existing.id, record);
    else this.#storage.add('recovery', record);
    this.services.toast.show(`Индекс восстановления: ${record.score}`);
  }

}
