/**
 * Компоненты верхней части разделов: Daily Insight, кольца прогресса,
 * список закономерностей. Тоже наследуются от UIComponent (общий жизненный цикл),
 * но используют «простую» рамку без кнопок управления виджетом.
 */
import { UIComponent } from '../core/UIComponent.js';
import { NOT_ENOUGH_DATA } from '../core/InsightEngine.js';
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { formatLongDate, isWithinLastDays, startOfWeek, today } from '../utils/date.js';
import { nutritionTotals } from '../utils/calculations.js';
import { fmt } from '../utils/format.js';
import { button, ring } from './kit.js';

const KIND_ACCENT = { recovery: 'recovery', window: 'training', training: 'training', progress: 'progress', mood: 'mood', cycle: 'cycle', hint: 'neutral' };
const KIND_ICON = { recovery: 'moon', window: 'clock', training: 'dumbbell', progress: 'chart', mood: 'smile', cycle: 'cycle', hint: 'info' };

/* ================================================================== */

export class DailyInsightCard extends UIComponent {
  static type = 'daily-insight';
  static meta = { title: 'Сегодня', icon: 'sparkles', accent: 'neutral', description: '' };

  constructor(config) {
    super(config);
    this.watchStorage(['profile', 'workouts', 'recovery', 'mood', 'cycle']);
    // Приветствие зависит от времени суток — обновляем раз в 10 минут.
    this.setInterval(() => this.refresh(), 10 * 60 * 1000);
  }

  renderFrame() {
    return this.createPlainFrame('section', 'insight-card');
  }

  renderBody(body) {
    const insight = this.services.insights.dailyInsight();
    body.setAttribute('aria-labelledby', `${this.id}-greeting`);
    body.append(
      el('p', { class: 'eyebrow insight-card__date', text: formatLongDate(today()) }),
      el('h2', { class: 'insight-card__greeting', id: `${this.id}-greeting`, text: insight.greeting }),
    );

    if (!insight.enough) {
      body.append(
        el('p', { class: 'insight-card__lead', text: NOT_ENOUGH_DATA }),
        el(
          'div',
          { class: 'actions' },
          button('Утренний check-in', { action: 'go', variant: 'primary', iconName: 'moon', dataset: { route: 'wellbeing' } }),
          button('Начать тренировку', { action: 'go', variant: 'secondary', iconName: 'dumbbell', dataset: { route: 'training' } }),
          button('Загрузить демо-данные', { action: 'demo', variant: 'ghost', iconName: 'database' }),
        ),
      );
    } else {
      body.append(
        el(
          'ul',
          { class: 'insight-card__lines' },
          insight.lines.map((line) =>
            el(
              'li',
              { class: ['insight-line', `accent-${KIND_ACCENT[line.kind] ?? 'neutral'}`] },
              el('span', { class: 'insight-line__icon', attrs: { 'aria-hidden': 'true' } }, icon(KIND_ICON[line.kind] ?? 'sparkles', { size: 16 })),
              el('span', { text: line.text }),
            ),
          ),
        ),
      );
      if (insight.window.status !== 'ok') body.append(el('p', { class: 'muted small', text: insight.window.message }));
    }
    body.append(el('p', { class: 'insight-card__disclaimer', text: `Rule-based анализ ваших записей, без ИИ. ${insight.disclaimer}` }));
  }

  onAction(action, target) {
    if (action === 'go') this.services.navigate?.(target.dataset.route);
    if (action === 'demo') this.services.loadDemo?.();
  }
}

/* ================================================================== */

export class ActivityRings extends UIComponent {
  static type = 'rings';
  static meta = { title: 'Кольца', icon: 'activity', accent: 'neutral', description: '' };

  constructor(config) {
    super(config);
    this.watchStorage(['profile', 'workouts', 'cardio', 'recovery', 'nutritionLog', 'nutritionGoals']);
  }

  renderFrame() {
    return this.createPlainFrame('section', 'rings-card');
  }

  #data() {
    const s = this.services.storage;
    const weekStart = startOfWeek(today());
    const goal = Math.max(1, Number(s.get('profile')?.workoutsPerWeek) || 3);
    const sessions =
      s.list('workouts').filter((w) => w.date >= weekStart).length + s.list('cardio').filter((c) => c.date >= weekStart).length;
    const recovery = s.list('recovery').find((r) => r.date === today());
    const goals = s.get('nutritionGoals');
    const kcal = nutritionTotals(s.list('nutritionLog').filter((e) => e.date === today())).kcal;

    return [
      {
        key: 'activity',
        label: 'Активность',
        ratio: sessions / goal,
        value: `${sessions}/${goal}`,
        caption: 'тренировок за неделю',
        text: `Активность: ${sessions} из ${goal} тренировок за неделю`,
      },
      {
        key: 'recovery',
        label: 'Восстановление',
        ratio: recovery ? recovery.score / 100 : 0,
        value: recovery ? String(recovery.score) : '—',
        caption: recovery ? 'индекс сегодня' : 'нет check-in',
        text: recovery ? `Восстановление: индекс ${recovery.score} из 100` : 'Восстановление: сегодня нет check-in',
      },
      {
        key: 'nutrition',
        label: 'Питание',
        ratio: goals?.kcal ? kcal / goals.kcal : 0,
        value: goals?.kcal ? `${Math.round((kcal / goals.kcal) * 100)}%` : '—',
        caption: goals?.kcal ? `${fmt(kcal)} / ${fmt(goals.kcal)} kcal` : 'цели не заданы',
        text: goals?.kcal ? `Питание: ${fmt(kcal)} из ${fmt(goals.kcal)} kcal` : 'Питание: цели не заданы',
      },
    ];
  }

  renderBody(body) {
    body.setAttribute('aria-label', 'Кольца прогресса');
    body.append(
      el('p', { class: 'rings-card__title', attrs: { 'aria-hidden': 'true' } }, el('strong', { text: 'Кольца дня' })),
      el(
        'ul',
        { class: 'rings' },
        this.#data().map((r) =>
          el(
            'li',
            { class: ['rings__item', `accent-${r.key}`] },
            ring({
              ratio: r.ratio,
              accent: r.key,
              size: 124,
              stroke: 15,
              label: el('span', { class: 'ring-label' }, el('span', { class: 'ring-label__value', text: r.value })),
            }),
            el('p', { class: 'rings__label', text: r.label }),
            el('p', { class: 'rings__caption', text: r.caption }),
            el('span', { class: 'sr-only', text: r.text }),
          ),
        ),
      ),
    );
    // Числа в кольцах продублированы текстом для скринридеров, визуальные — скрыты.
    body.querySelectorAll('.ring-wrap, .rings__label, .rings__caption').forEach((n) => n.setAttribute('aria-hidden', 'true'));
  }
}

/* ================================================================== */

export class InsightsPanel extends UIComponent {
  static type = 'insights';
  static meta = { title: 'Закономерности', icon: 'sparkles', accent: 'neutral', description: '' };

  constructor(config) {
    super(config);
    this.watchStorage(['profile', 'workouts', 'recovery', 'mood', 'cycle']);
  }

  renderFrame() {
    return this.createPlainFrame('section', 'insights-panel');
  }

  renderBody(body) {
    const engine = this.services.insights;
    const list = engine.observations();
    const trainingWindow = engine.optimalWindow();
    const recentWorkouts = this.services.storage.list('workouts').filter((w) => isWithinLastDays(w.date, 56)).length;

    body.setAttribute('aria-labelledby', `${this.id}-title`);
    body.append(
      el('h2', { class: 'panel-title', id: `${this.id}-title` }, icon('sparkles', { size: 18 }), 'Закономерности в ваших данных'),
      el(
        'div',
        { class: ['window-card', trainingWindow.status === 'ok' && 'is-ok'] },
        el('p', { class: 'eyebrow', text: 'Оптимальное окно тренировки' }),
        el('p', { class: 'window-card__value', text: trainingWindow.status === 'ok' ? trainingWindow.label : '—' }),
        el('p', { class: 'muted small', text: trainingWindow.message }),
      ),
    );
    if (list.length) {
      body.append(el('ul', { class: 'insight-list' }, list.map((o) => el('li', { class: ['insight-list__item', `accent-${KIND_ACCENT[o.category] ?? 'neutral'}`], text: o.text }))));
    } else {
      body.append(el('p', { class: 'muted', text: NOT_ENOUGH_DATA }));
    }
    body.append(el('p', { class: 'insight-card__disclaimer', text: `Корреляция не означает причинно-следственную связь. Учтено тренировок за 8 недель: ${recentWorkouts}. Наблюдения не являются медицинскими рекомендациями.` }));
  }
}
