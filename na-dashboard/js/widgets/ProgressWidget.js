/**
 * ProgressWidget — графики прогресса (Chart.js).
 * Три вкладки: силовые / тело (замеры) / кардио. Пользователь выбирает
 * один показатель, чтобы график не был перегружен. Выбор хранится в settings
 * экземпляра — у двух виджетов прогресса независимое состояние.
 */
import { UIComponent } from '../core/UIComponent.js';
import { el, field, input, select, formValues, setFieldError, clearFormErrors } from '../utils/dom.js';
import { formatShortDate, today, relativeDayLabel } from '../utils/date.js';
import { exerciseStats, percentChange } from '../utils/calculations.js';
import { fmt, fmtSigned, toNumber } from '../utils/format.js';
import { BODY_METRICS, CARDIO_TYPES } from '../data/catalog.js';
import { findExercise } from '../data/exercises.js';
import { chartContainer, chartFallback, createBarChart, createLineChart } from '../utils/charts.js';
import { button, emptyState, iconButton, segmented, stat } from '../ui/kit.js';

const TABS = [
  { value: 'strength', label: 'Силовые' },
  { value: 'body', label: 'Тело' },
  { value: 'cardio', label: 'Кардио' },
];

const STRENGTH_METRICS = [
  { value: 'max', label: 'Максимальный вес', unit: 'кг', pick: (s) => s.maxWeight },
  { value: 'volume', label: 'Объём (вес × повторы)', unit: 'кг', pick: (s) => s.volume },
  { value: 'e1rm', label: 'Расчётный 1RM', unit: 'кг', pick: (s) => Math.round(s.e1rm * 10) / 10 },
];

const CARDIO_METRICS = [
  { value: 'duration', label: 'Длительность', unit: 'мин', chart: 'bar' },
  { value: 'distance', label: 'Дистанция', unit: 'км', chart: 'bar' },
  { value: 'avgHr', label: 'Средний пульс', unit: 'уд/мин', chart: 'line' },
  { value: 'intensity', label: 'Интенсивность (1–10)', unit: '', chart: 'line' },
];

const CUSTOM_PREFIX = 'custom:';

export class ProgressWidget extends UIComponent {
  static type = 'progress';
  static meta = {
    title: 'Прогресс',
    icon: 'chart',
    accent: 'progress',
    description: 'Графики силовых, замеров тела и кардио.',
  };

  constructor(config) {
    super(config);
    this.settings = { tab: 'strength', strengthMetric: 'max', bodyMetric: 'bodyWeight', cardioMetric: 'duration', ...this.settings };
    this.watchStorage(['workouts', 'measurements', 'cardio']);
    this.addEventListenerWithCleanup(this.services.theme, 'change', () => this.refresh());
  }

  get #storage() {
    return this.services.storage;
  }

  renderBody(body) {
    body.append(segmented({ action: 'tab', options: TABS, value: this.settings.tab, label: 'Тип показателей' }));
    const panel = el('div', { class: 'tab-panel' });
    body.append(panel);
    if (this.settings.tab === 'body') this.#renderBody(panel);
    else if (this.settings.tab === 'cardio') this.#renderCardio(panel);
    else this.#renderStrength(panel);
  }

  #chartBlock(summary, factory, height) {
    const { wrapper, canvas } = chartContainer(summary, { height });
    this.queueChart(canvas, factory);
    return window.Chart ? wrapper : chartFallback(summary);
  }

  #summary({ values, unit, label }) {
    const first = values[0];
    const last = values.at(-1);
    const best = Math.max(...values);
    const change = percentChange(first, last);
    return el(
      'div',
      { class: 'stat-row' },
      stat({ label: 'Сейчас', value: fmt(last, 1), unit, accent: 'progress' }),
      stat({ label: 'Лучший', value: fmt(best, 1), unit }),
      stat({ label: 'Изменение', value: change === null ? '—' : fmtSigned(change), hint: label }),
    );
  }

  /* ------------------------- Силовые ------------------------- */

  #strengthSeries(exerciseId, metric) {
    const pick = STRENGTH_METRICS.find((m) => m.value === metric)?.pick ?? STRENGTH_METRICS[0].pick;
    const points = [];
    for (const w of this.#storage.list('workouts').sort((a, b) => a.startedAt.localeCompare(b.startedAt))) {
      for (const ex of w.exercises) {
        if (ex.exerciseId !== exerciseId) continue;
        const stats = exerciseStats(ex);
        if (stats) points.push({ date: w.date, value: pick(stats) });
      }
    }
    return points;
  }

  #renderStrength(panel) {
    const workouts = this.#storage.list('workouts');
    const counts = new Map();
    for (const w of workouts) for (const ex of w.exercises) counts.set(ex.exerciseId, { name: ex.name, n: (counts.get(ex.exerciseId)?.n ?? 0) + 1 });
    if (!counts.size) {
      panel.append(emptyState({ iconName: 'chart', title: 'Нет силовых данных', text: 'Сохраните тренировку — здесь появится график рабочего веса, объёма и расчётного 1RM.' }));
      return;
    }
    const exercises = [...counts.entries()].sort((a, b) => b[1].n - a[1].n);
    const exerciseId = counts.has(this.settings.exerciseId) ? this.settings.exerciseId : exercises[0][0];
    const metric = STRENGTH_METRICS.find((m) => m.value === this.settings.strengthMetric) ?? STRENGTH_METRICS[0];

    panel.append(
      el(
        'div',
        { class: 'controls-row' },
        field('Упражнение', select('exercise', exercises.map(([id, { name }]) => ({ value: id, label: findExercise(this.#storage, id)?.name ?? name })), { value: exerciseId, attrs: { 'data-field': 'exercise' } })),
        field('Показатель', select('metric', STRENGTH_METRICS.map(({ value, label }) => ({ value, label })), { value: metric.value, attrs: { 'data-field': 'strength-metric' } })),
      ),
    );

    const points = this.#strengthSeries(exerciseId, metric.value);
    const name = counts.get(exerciseId).name;
    if (points.length < 2) {
      panel.append(emptyState({ iconName: 'chart', title: 'Нужно минимум две тренировки', text: `Для «${name}» пока одна запись.` }));
      return;
    }
    const values = points.map((p) => p.value);
    panel.append(
      this.#summary({ values, unit: metric.unit, label: `с ${formatShortDate(points[0].date)}` }),
      this.#chartBlock(
        `${metric.label}, ${name}: от ${fmt(values[0], 1)} до ${fmt(values.at(-1), 1)} ${metric.unit}, ${points.length} тренировок`,
        (canvas) => createLineChart(canvas, { labels: points.map((p) => formatShortDate(p.date)), datasets: [{ label: metric.label, data: values, accent: 'progress' }] }),
      ),
    );
    if (metric.value === 'e1rm') panel.append(el('p', { class: 'note-inline', text: 'Расчётный 1RM — оценка по формуле Эпли: вес × (1 + повторы / 30).' }));
  }

  /* ------------------------- Тело ------------------------- */

  #bodyMetrics() {
    const custom = [...new Set(this.#storage.list('measurements').filter((m) => m.metric.startsWith(CUSTOM_PREFIX)).map((m) => m.metric))];
    return [
      ...BODY_METRICS.map((m) => ({ value: m.id, label: m.label, unit: m.unit })),
      ...custom.map((id) => ({ value: id, label: id.slice(CUSTOM_PREFIX.length), unit: '' })),
    ];
  }

  #renderBody(panel) {
    const metrics = this.#bodyMetrics();
    const metric = metrics.find((m) => m.value === this.settings.bodyMetric) ?? metrics[0];
    const points = this.#storage
      .list('measurements')
      .filter((m) => m.metric === metric.value)
      .sort((a, b) => a.date.localeCompare(b.date));

    panel.append(
      el('div', { class: 'controls-row' }, field('Замер', select('bodyMetric', metrics, { value: metric.value, attrs: { 'data-field': 'body-metric' } }))),
    );

    if (points.length >= 2) {
      const values = points.map((p) => p.value);
      panel.append(
        this.#summary({ values, unit: metric.unit, label: `с ${formatShortDate(points[0].date)}` }),
        this.#chartBlock(
          `${metric.label}: от ${fmt(values[0], 1)} до ${fmt(values.at(-1), 1)} ${metric.unit}`,
          (canvas) => createLineChart(canvas, { labels: points.map((p) => formatShortDate(p.date)), datasets: [{ label: metric.label, data: values, accent: 'progress' }] }),
        ),
      );
    } else {
      panel.append(emptyState({ iconName: 'chart', title: points.length ? 'Нужен ещё один замер' : 'Замеров пока нет', text: 'Добавьте минимум два замера, чтобы увидеть динамику.' }));
    }

    panel.append(
      el(
        'details',
        { class: 'disclosure', dataset: { key: 'add-measurement' } },
        el('summary', { class: 'disclosure__summary', text: 'Добавить замер' }),
        el(
          'form',
          { class: 'form-grid', dataset: { form: 'measurement' }, attrs: { novalidate: true } },
          field('Показатель', select('metric', [...metrics.map(({ value, label }) => ({ value, label })), { value: '__new', label: 'Свой замер…' }], { value: metric.value })),
          field('Название своего замера', input('customName', { attrs: { maxlength: 30, placeholder: 'Например, голень' } }), { hint: 'Только для варианта «Свой замер»' }),
          field('Значение', input('value', { type: 'number', attrs: { min: 0, max: 500, step: 0.1, inputmode: 'decimal' }, required: true })),
          field('Дата', input('date', { type: 'date', value: today(), attrs: { max: today() } })),
          button('Сохранить замер', { type: 'submit', variant: 'secondary', iconName: 'plus' }),
        ),
      ),
    );
  }

  /* ------------------------- Кардио ------------------------- */

  #renderCardio(panel) {
    const sessions = this.#storage.list('cardio').sort((a, b) => a.date.localeCompare(b.date));
    const metric = CARDIO_METRICS.find((m) => m.value === this.settings.cardioMetric) ?? CARDIO_METRICS[0];

    panel.append(
      el('div', { class: 'controls-row' }, field('Показатель', select('cardioMetric', CARDIO_METRICS.map(({ value, label }) => ({ value, label })), { value: metric.value, attrs: { 'data-field': 'cardio-metric' } }))),
    );

    const points = sessions.filter((s) => Number.isFinite(s[metric.value]));
    if (points.length >= 2) {
      const values = points.map((p) => p[metric.value]);
      const labels = points.map((p) => formatShortDate(p.date));
      const summary = `${metric.label}: ${points.length} записей, последнее значение ${fmt(values.at(-1), 1)} ${metric.unit}`;
      panel.append(
        this.#chartBlock(summary, (canvas) =>
          metric.chart === 'bar'
            ? createBarChart(canvas, { labels, data: values, accent: 'progress', label: metric.label })
            : createLineChart(canvas, { labels, datasets: [{ label: metric.label, data: values, accent: 'progress' }] }),
        ),
      );
    } else {
      panel.append(emptyState({ iconName: 'activity', title: 'Мало кардио-записей', text: 'Добавьте минимум две сессии, чтобы увидеть график.' }));
    }

    const recent = [...sessions].reverse().slice(0, 3);
    if (recent.length) {
      panel.append(
        el(
          'ul',
          { class: 'list' },
          recent.map((s) =>
            el(
              'li',
              { class: 'list__item' },
              el(
                'div',
                { class: 'list__main' },
                el('span', { class: 'list__title', text: s.type }),
                el('span', { class: 'list__sub', text: [`${fmt(s.duration)} мин`, s.distance ? `${fmt(s.distance, 1)} км` : null, s.avgHr ? `${fmt(s.avgHr)} уд/мин` : null, s.intensity ? `интенсивность ${s.intensity}/10` : null].filter(Boolean).join(' · ') }),
              ),
              el('span', { class: 'list__meta', text: relativeDayLabel(s.date) }),
              iconButton('trash', `Удалить кардио «${s.type}» от ${relativeDayLabel(s.date)}`, { action: 'delete-cardio', dataset: { id: s.id }, variant: 'ghost' }),
            ),
          ),
        ),
      );
    }

    panel.append(
      el(
        'details',
        { class: 'disclosure', dataset: { key: 'add-cardio' } },
        el('summary', { class: 'disclosure__summary', text: 'Добавить кардио' }),
        el(
          'form',
          { class: 'form-grid', dataset: { form: 'cardio' }, attrs: { novalidate: true } },
          field('Тип', select('type', CARDIO_TYPES.map((t) => ({ value: t, label: t })))),
          field('Дата', input('date', { type: 'date', value: today(), attrs: { max: today() } })),
          field('Длительность, мин', input('duration', { type: 'number', attrs: { min: 1, max: 600, inputmode: 'numeric' }, required: true })),
          field('Дистанция, км', input('distance', { type: 'number', attrs: { min: 0, max: 300, step: 0.1, inputmode: 'decimal' } })),
          field('Средний пульс', input('avgHr', { type: 'number', attrs: { min: 40, max: 220, inputmode: 'numeric' } })),
          field('Интенсивность 1–10', input('intensity', { type: 'number', attrs: { min: 1, max: 10, inputmode: 'numeric' } }), { hint: 'Субъективная оценка нагрузки' }),
          button('Сохранить', { type: 'submit', variant: 'secondary', iconName: 'plus' }),
        ),
      ),
    );
  }

  /* ------------------------- События ------------------------- */

  onAction(action, target) {
    if (action === 'tab') this.updateSettings({ tab: target.dataset.value });
    if (action === 'delete-cardio') this.#deleteCardio(target.dataset.id);
  }

  async #deleteCardio(id) {
    if (await this.services.modal.confirm({ title: 'Удалить кардио-запись?', message: 'Это действие нельзя отменить.', confirmLabel: 'Удалить', danger: true })) {
      this.#storage.removeItem('cardio', id);
    }
  }

  onChange(name, target) {
    if (name === 'exercise') this.updateSettings({ exerciseId: target.value });
    if (name === 'strength-metric') this.updateSettings({ strengthMetric: target.value });
    if (name === 'body-metric') this.updateSettings({ bodyMetric: target.value });
    if (name === 'cardio-metric') this.updateSettings({ cardioMetric: target.value });
  }

  onSubmit(name, form) {
    const v = formValues(form);
    clearFormErrors(form);

    if (name === 'measurement') {
      const value = toNumber(v.value);
      let metric = v.metric;
      if (metric === '__new') {
        if (!v.customName) {
          setFieldError(form.elements.customName, 'Введите название своего замера');
          return;
        }
        metric = CUSTOM_PREFIX + v.customName.slice(0, 30);
      }
      if (value === null || value <= 0 || value > 500) {
        setFieldError(form.elements.value, 'Введите значение больше 0');
        return;
      }
      form.elements.value.value = '';
      form.elements.customName.value = '';
      this.settings.bodyMetric = metric;
      this.emit('state');
      this.#storage.add('measurements', { date: v.date || today(), metric, value });
      this.services.toast.show('Замер сохранён');
    }

    if (name === 'cardio') {
      const duration = toNumber(v.duration);
      if (duration === null || duration <= 0 || duration > 600) {
        setFieldError(form.elements.duration, 'Длительность от 1 до 600 минут');
        return;
      }
      const intensity = toNumber(v.intensity);
      if (intensity !== null && (intensity < 1 || intensity > 10)) {
        setFieldError(form.elements.intensity, 'Оценка от 1 до 10');
        return;
      }
      const avgHr = toNumber(v.avgHr);
      if (avgHr !== null && (avgHr < 40 || avgHr > 220)) {
        setFieldError(form.elements.avgHr, 'Пульс от 40 до 220');
        return;
      }
      ['duration', 'distance', 'avgHr', 'intensity'].forEach((k) => {
        form.elements[k].value = '';
      });
      this.#storage.add('cardio', { date: v.date || today(), type: v.type, duration, distance: toNumber(v.distance), avgHr, intensity });
      this.services.toast.show('Кардио сохранено');
    }
  }

}
