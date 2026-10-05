/**
 * BiologicalRhythmWidget — контент зависит от пола в профиле.
 *
 * Женщины: календарный трекинг цикла и ориентировочная фаза; наблюдения —
 *   только сравнение с собственной историей записей.
 * Мужчины: циркадный контекст (сон, энергия, стресс, восстановление, время суток).
 *   Приложение не вычисляет гормоны. Лабораторный показатель можно только
 *   ввести вручную — без интерпретации.
 *
 * Два режима реализованы как отдельные «стратегии» отрисовки внутри одного виджета.
 */
import { UIComponent } from '../core/UIComponent.js';
import { el, field, input, select, formValues, setFieldError, clearFormErrors } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { addDays, daysBetween, formatHours, formatLongDate, formatShortDate, relativeDayLabel, timeToMinutes, today } from '../utils/date.js';
import { CYCLE_PHASES, cycleInfo, phaseForDay } from '../utils/calculations.js';
import { fmt, toNumber } from '../utils/format.js';
import { button, emptyState, iconButton, note, stat } from '../ui/kit.js';

const PHASE_TEXT = {
  menstrual: 'Первые дни цикла. Ориентируйтесь на собственное самочувствие.',
  follicular: 'Фаза после менструации и до овуляторного окна (по календарю).',
  ovulatory: 'Ориентировочное овуляторное окно по средней длине цикла.',
  luteal: 'Вторая половина цикла (по календарю).',
};

const LAB_UNITS = ['нмоль/л', 'нг/дл', 'пг/мл', 'другое'];

export class BiologicalRhythmWidget extends UIComponent {
  static type = 'rhythm';
  static meta = {
    title: 'Биоритмы',
    icon: 'cycle',
    accent: 'cycle',
    description: 'Цикл (для женщин) или суточный ритм и восстановление (для мужчин).',
  };

  #editing = false;

  constructor(config) {
    super(config);
    this.watchStorage(['profile', 'cycle', 'recovery', 'labs', 'workouts', 'mood']);
    // Обновляем «время суток» раз в 5 минут — интервал очищается в destroy().
    this.setInterval(() => {
      if ((this.services.storage.get('profile')?.sex ?? 'male') !== 'female') this.refresh();
    }, 5 * 60 * 1000);
  }

  get #storage() {
    return this.services.storage;
  }

  renderBody(body) {
    const sex = this.#storage.get('profile')?.sex;
    if (sex === 'female') this.#renderCycle(body);
    else this.#renderCircadian(body);
  }

  /* ================================================================ */
  /* Женский режим — цикл                                              */
  /* ================================================================ */

  #renderCycle(body) {
    const cycle = this.#storage.get('cycle');
    if (!cycle?.starts?.length || this.#editing) {
      this.#renderCycleForm(body, cycle);
      return;
    }
    const info = cycleInfo(cycle);
    if (!info) {
      this.#renderCycleForm(body, cycle);
      return;
    }

    body.append(
      el(
        'div',
        { class: 'cycle-hero' },
        el('p', { class: 'eyebrow', text: 'Цикл' }),
        el('p', { class: 'cycle-hero__day' }, el('span', { class: 'big-number', text: String(info.day) }), el('span', { class: 'muted', text: ` день из ~${info.cycleLength}` })),
        el('p', { class: 'cycle-hero__phase', text: `${CYCLE_PHASES[info.phase]} фаза` }, el('span', { class: 'tag', text: 'ориентировочно' })),
        el('p', { class: 'muted small', text: info.isLate ? 'Цикл длится дольше вашей средней длины — отметьте начало нового цикла, когда он начнётся.' : PHASE_TEXT[info.phase] }),
      ),
      this.#cycleTrack(info),
      el(
        'div',
        { class: 'stat-row' },
        stat({ label: 'Следующий цикл', value: info.isLate ? '—' : formatShortDate(info.nextStart), hint: info.isLate ? '' : `через ${daysBetween(today(), info.nextStart)} дн.` }),
        stat({ label: 'Средняя длина', value: String(info.cycleLength), unit: 'дн.' }),
      ),
    );

    const observations = this.services.insights.cycleObservations();
    body.append(el('h3', { class: 'section-title', text: 'В вашей истории' }));
    if (observations.length) {
      body.append(el('ul', { class: 'insight-list' }, observations.map((o) => el('li', { class: 'insight-list__item accent-cycle', text: o.text }))));
    } else {
      body.append(el('p', { class: 'muted small', text: 'Пока недостаточно записей, чтобы сопоставить фазы цикла с вашей энергией и тренировками. Продолжайте делать утренний check-in.' }));
    }

    body.append(
      el(
        'div',
        { class: 'actions' },
        button('Начался новый цикл сегодня', { action: 'new-cycle', variant: 'tinted', iconName: 'plus' }),
        button('Параметры', { action: 'edit-cycle', variant: 'ghost', iconName: 'edit' }),
      ),
      note('Расчёт календарный и ориентировочный. Это не медицинский прогноз, не диагностика и не метод контрацепции.'),
    );
  }

  /** Полоса цикла с цветными фазами и отметкой «сегодня». */
  #cycleTrack(info) {
    const segments = [];
    let current = null;
    for (let d = 1; d <= info.cycleLength; d += 1) {
      const phase = phaseForDay(d, info.cycleLength, info.periodLength);
      if (current?.phase === phase) current.days += 1;
      else {
        current = { phase, days: 1 };
        segments.push(current);
      }
    }
    const marker = Math.min(info.day, info.cycleLength);
    return el(
      'div',
      { class: 'cycle-track', attrs: { role: 'img', 'aria-label': `День ${info.day} цикла, ${CYCLE_PHASES[info.phase].toLowerCase()} фаза (ориентировочно)` } },
      el(
        'div',
        { class: 'cycle-track__bar' },
        segments.map((s) => el('span', { class: `cycle-track__seg phase-${s.phase}`, style: { flex: String(s.days) }, attrs: { title: CYCLE_PHASES[s.phase] } })),
        el('span', { class: 'cycle-track__marker', style: { left: `${((marker - 0.5) / info.cycleLength) * 100}%` } }),
      ),
      el(
        'div',
        { class: 'cycle-track__legend', attrs: { 'aria-hidden': 'true' } },
        Object.entries(CYCLE_PHASES).map(([k, v]) => el('span', { class: 'legend-item' }, el('span', { class: `legend-dot phase-${k}` }), v)),
      ),
    );
  }

  #renderCycleForm(body, cycle) {
    const lastStart = cycle?.starts?.slice().sort().at(-1) ?? '';
    body.append(
      el('p', { class: 'lead', text: 'Укажите параметры цикла — N.A. покажет ориентировочную фазу и сопоставит её с вашими записями.' }),
      el(
        'form',
        { class: 'form-grid', dataset: { form: 'cycle' }, attrs: { novalidate: true } },
        field('Первый день последней менструации', input('lastStart', { type: 'date', value: lastStart, attrs: { max: today() }, required: true })),
        field('Средняя длина цикла, дней', input('cycleLength', { type: 'number', value: cycle?.cycleLength ?? 28, attrs: { min: 21, max: 45, inputmode: 'numeric' } }), { hint: 'Обычно 21–35 дней' }),
        field('Продолжительность менструации, дней', input('periodLength', { type: 'number', value: cycle?.periodLength ?? 5, attrs: { min: 2, max: 10, inputmode: 'numeric' } })),
        el(
          'div',
          { class: 'actions' },
          button('Сохранить', { type: 'submit', variant: 'primary', iconName: 'check' }),
          this.#editing ? button('Отмена', { action: 'cancel-edit', variant: 'ghost' }) : null,
        ),
      ),
      note('Данные хранятся только в этом браузере. Приложение не ставит диагнозов.'),
    );
  }

  /* ================================================================ */
  /* Мужской режим — циркадный контекст                                */
  /* ================================================================ */

  #renderCircadian(body) {
    const profile = this.#storage.get('profile') ?? {};
    const entry = this.#storage.list('recovery').find((r) => r.date === today());
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    const wake = timeToMinutes(entry?.wakeTime ?? profile.wakeTime ?? '07:00');
    const awakeHours = wake === null ? null : (((minutes - wake) % 1440) + 1440) % 1440 / 60;
    const part = minutes < 300 ? 'Ночь' : minutes < 720 ? 'Утро' : minutes < 1020 ? 'День' : minutes < 1380 ? 'Вечер' : 'Ночь';

    body.append(
      el(
        'div',
        { class: 'circadian' },
        el('div', { class: 'circadian__dial', attrs: { 'aria-hidden': 'true' } }, el('span', { class: 'circadian__hand', style: { transform: `rotate(${(minutes / 1440) * 360}deg)` } }), icon(part === 'Ночь' || part === 'Вечер' ? 'moon' : 'sun', { size: 22 })),
        el(
          'div',
          {},
          el('p', { class: 'eyebrow', text: 'Суточный контекст' }),
          el('p', { class: 'circadian__title', text: `${part}, ${now.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}` }),
          el('p', { class: 'muted', text: awakeHours !== null ? `С пробуждения прошло около ${formatHours(Math.round(awakeHours * 2) / 2)}` : 'Укажите время пробуждения в профиле' }),
        ),
      ),
    );

    if (entry) {
      body.append(
        el(
          'div',
          { class: 'stat-grid' },
          stat({ label: 'Сон', value: formatHours(entry.sleepHours), accent: 'recovery' }),
          stat({ label: 'Энергия', value: `${entry.energy}/5`, accent: 'recovery' }),
          stat({ label: 'Стресс', value: `${entry.stress}/5`, accent: 'recovery' }),
          stat({ label: 'Восстановление', value: String(entry.score), unit: '/100', accent: 'recovery' }),
        ),
      );
    } else {
      body.append(emptyState({ iconName: 'moon', title: 'Нет check-in за сегодня', text: 'Отметьте сон, энергию и стресс в виджете «Восстановление» — данные появятся здесь.' }));
    }

    const trainingWindow = this.services.insights.optimalWindow();
    body.append(
      el('p', { class: 'small', text: trainingWindow.status === 'ok' ? `В вашей истории тренировки эффективнее в окне ${trainingWindow.label}.` : trainingWindow.message }),
      note('Многие физиологические показатели имеют суточные ритмы. N.A. не измеряет и не оценивает уровень гормонов — здесь только ваши собственные записи.'),
    );
    this.#renderLabs(body);
  }

  #renderLabs(body) {
    const labs = this.#storage.list('labs').sort((a, b) => b.date.localeCompare(a.date));
    body.append(
      el(
        'details',
        { class: 'disclosure', dataset: { key: 'labs' } },
        el('summary', { class: 'disclosure__summary', text: `Лабораторные показатели (${labs.length})` }),
        el('p', { class: 'muted small', text: 'Только ручной ввод ваших результатов анализов — для личной истории. Приложение их не интерпретирует: обсуждайте результаты с врачом.' }),
        labs.length
          ? el(
              'ul',
              { class: 'list' },
              labs.map((l) =>
                el(
                  'li',
                  { class: 'list__item' },
                  el('div', { class: 'list__main' }, el('span', { class: 'list__title', text: l.name }), el('span', { class: 'list__sub', text: `${fmt(l.value, 1)} ${l.unit}` })),
                  el('span', { class: 'list__meta', text: formatShortDate(l.date) }),
                  iconButton('trash', `Удалить запись «${l.name}» от ${formatShortDate(l.date)}`, { action: 'delete-lab', dataset: { id: l.id }, variant: 'ghost' }),
                ),
              ),
            )
          : null,
        el(
          'form',
          { class: 'form-grid', dataset: { form: 'lab' }, attrs: { novalidate: true } },
          field('Показатель', input('name', { value: 'Тестостерон общий', attrs: { maxlength: 60 } })),
          field('Значение', input('value', { type: 'number', attrs: { min: 0, step: 0.01, inputmode: 'decimal' }, required: true })),
          field('Единицы', select('unit', LAB_UNITS.map((u) => ({ value: u, label: u })))),
          field('Дата анализа', input('date', { type: 'date', value: today(), attrs: { max: today() } })),
          button('Добавить', { type: 'submit', variant: 'secondary', iconName: 'plus' }),
        ),
      ),
    );
  }

  /* ================================================================ */
  /* События                                                          */
  /* ================================================================ */

  async onAction(action, target) {
    const { modal, toast } = this.services;
    switch (action) {
      case 'edit-cycle':
        this.#editing = true;
        this.refresh();
        this.body.querySelector('input')?.focus();
        break;
      case 'cancel-edit':
        this.#editing = false;
        this.refresh();
        break;
      case 'new-cycle': {
        const ok = await modal.confirm({ title: 'Отметить начало цикла?', message: `Сегодня, ${formatLongDate(today())}, будет отмечено как первый день нового цикла.`, confirmLabel: 'Отметить' });
        if (!ok) return;
        this.#storage.update('cycle', (c) => ({ ...c, starts: [...new Set([...(c?.starts ?? []), today()])].sort() }));
        toast.show('Новый цикл отмечен');
        break;
      }
      case 'delete-lab':
        if (await modal.confirm({ title: 'Удалить запись анализа?', message: 'Это действие нельзя отменить.', confirmLabel: 'Удалить', danger: true })) {
          this.#storage.removeItem('labs', target.dataset.id);
        }
        break;
      default:
    }
  }

  onSubmit(name, form) {
    clearFormErrors(form);
    const v = formValues(form);
    if (name === 'cycle') {
      const cycleLength = toNumber(v.cycleLength);
      const periodLength = toNumber(v.periodLength);
      let ok = true;
      if (!v.lastStart || v.lastStart > today()) {
        setFieldError(form.elements.lastStart, 'Укажите дату не позже сегодняшней');
        ok = false;
      }
      if (cycleLength === null || cycleLength < 21 || cycleLength > 45) {
        setFieldError(form.elements.cycleLength, 'От 21 до 45 дней');
        ok = false;
      }
      if (periodLength === null || periodLength < 2 || periodLength > 10) {
        setFieldError(form.elements.periodLength, 'От 2 до 10 дней');
        ok = false;
      }
      if (!ok) return;
      this.#editing = false;
      this.#storage.update('cycle', (c) => ({
        starts: [...new Set([...(c?.starts ?? []).filter((s) => s < addDays(v.lastStart, -10)), v.lastStart])].sort(),
        cycleLength,
        periodLength,
      }));
      this.services.toast.show('Параметры цикла сохранены');
    }

    if (name === 'lab') {
      const value = toNumber(v.value);
      if (!v.name) {
        setFieldError(form.elements.name, 'Укажите название показателя');
        return;
      }
      if (value === null || value < 0) {
        setFieldError(form.elements.value, 'Введите числовое значение');
        return;
      }
      form.elements.value.value = '';
      this.#storage.add('labs', { name: v.name.slice(0, 60), value, unit: v.unit, date: v.date || today() });
      this.services.toast.show(`Запись добавлена · ${relativeDayLabel(v.date || today())}`);
    }
  }
}
