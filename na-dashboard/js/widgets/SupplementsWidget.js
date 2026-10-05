/**
 * SupplementsWidget — добавки: расписание, отметка «Принято сегодня»,
 * регулярность приёма за неделю/месяц. Без рекомендаций по добавкам.
 */
import { UIComponent } from '../core/UIComponent.js';
import { createId } from '../core/StorageService.js';
import { el, field, input, select, formValues, setFieldError, clearFormErrors, uid } from '../utils/dom.js';
import { today } from '../utils/date.js';
import { SUPPLEMENT_FREQUENCIES, isSupplementScheduled, supplementAdherence } from '../utils/calculations.js';
import { SUPPLEMENT_SLOTS } from '../data/catalog.js';
import { button, emptyState, iconButton, note, progressBar, segmented } from '../ui/kit.js';

const PERIODS = [
  { value: '7', label: 'Неделя' },
  { value: '30', label: 'Месяц' },
];

export class SupplementsWidget extends UIComponent {
  static type = 'supplements';
  static meta = {
    title: 'Добавки',
    icon: 'pill',
    accent: 'supplements',
    description: 'Расписание приёма и регулярность.',
  };

  constructor(config) {
    super(config);
    this.settings = { period: '7', ...this.settings };
    this.watchStorage(['supplements', 'supplementLog']);
  }

  get #storage() {
    return this.services.storage;
  }

  #slotTime(s) {
    return s.schedule === 'custom' ? s.time : SUPPLEMENT_SLOTS[s.schedule]?.time ?? '';
  }

  renderBody(body) {
    const supplements = this.#storage.list('supplements');
    const log = this.#storage.get('supplementLog');
    const takenToday = new Set(log[today()] ?? []);

    if (supplements.length) {
      const days = Number(this.settings.period);
      const adherence = supplementAdherence(supplements, log, days);
      body.append(
        el(
          'div',
          { class: 'adherence' },
          el('div', { class: 'adherence__head' }, el('p', { class: 'adherence__value', text: adherence.percent === null ? 'Регулярность — нет данных' : `Регулярность — ${adherence.percent}%` }), segmented({ action: 'period', options: PERIODS, value: this.settings.period, label: 'Период', size: 'sm' })),
          progressBar({ value: adherence.taken, max: adherence.scheduled || 1, accent: 'supplements', label: `Принято за ${days === 7 ? 'неделю' : 'месяц'}`, detail: `${adherence.taken} из ${adherence.scheduled}` }),
        ),
      );

      const scheduled = supplements.filter((s) => isSupplementScheduled(s, today())).sort((a, b) => this.#slotTime(a).localeCompare(this.#slotTime(b)));
      const other = supplements.filter((s) => !isSupplementScheduled(s, today()));

      body.append(el('h3', { class: 'section-title', text: 'Сегодня' }));
      if (scheduled.length) {
        body.append(el('ul', { class: 'list' }, scheduled.map((s) => this.#item(s, takenToday.has(s.id)))));
      } else {
        body.append(el('p', { class: 'muted small', text: 'Сегодня по расписанию ничего нет.' }));
      }
      if (other.length) {
        body.append(
          el('h3', { class: 'section-title', text: 'Не по расписанию сегодня' }),
          el('ul', { class: 'list list--muted' }, other.map((s) => this.#item(s, takenToday.has(s.id)))),
        );
      }
    } else {
      body.append(emptyState({ iconName: 'pill', title: 'Добавок пока нет', text: 'Добавьте то, что принимаете, чтобы отмечать приём и видеть регулярность.' }));
    }

    body.append(
      el(
        'details',
        { class: 'disclosure', dataset: { key: 'add-supplement' }, open: !supplements.length },
        el('summary', { class: 'disclosure__summary', text: 'Добавить добавку' }),
        el(
          'form',
          { class: 'form-grid', dataset: { form: 'supplement' }, attrs: { novalidate: true } },
          field('Название', input('name', { attrs: { maxlength: 50, autocomplete: 'off' }, required: true })),
          field('Дозировка', input('dose', { attrs: { maxlength: 30, placeholder: 'Например, 1 капсула' } })),
          field('Когда', select('schedule', Object.entries(SUPPLEMENT_SLOTS).map(([value, s]) => ({ value, label: s.label })))),
          field('Своё время', input('time', { type: 'time', value: '09:00' }), { hint: 'Для варианта «Своё время»' }),
          field('Частота', select('frequency', Object.entries(SUPPLEMENT_FREQUENCIES).map(([value, label]) => ({ value, label })))),
          button('Добавить', { type: 'submit', variant: 'primary', iconName: 'plus' }),
        ),
      ),
      note('N.A. не даёт рекомендаций по добавкам и дозировкам — только помогает отмечать приём.'),
    );
  }

  #item(s, taken) {
    const id = uid('sup');
    const when = s.schedule === 'custom' ? s.time : `${SUPPLEMENT_SLOTS[s.schedule]?.label ?? ''}`;
    return el(
      'li',
      { class: ['list__item', 'supplement', taken && 'is-done'] },
      el('input', { type: 'checkbox', id, class: 'check', checked: taken, dataset: { field: 'taken', id: s.id, focusKey: `taken-${s.id}` } }),
      el(
        'label',
        { for: id, class: 'list__main' },
        el('span', { class: 'list__title', text: s.name }),
        el('span', { class: 'list__sub', text: [s.dose, when, SUPPLEMENT_FREQUENCIES[s.frequency]].filter(Boolean).join(' · ') }),
        el('span', { class: 'sr-only', text: taken ? 'Принято сегодня' : 'Не принято сегодня' }),
      ),
      iconButton('trash', `Удалить добавку «${s.name}»`, { action: 'delete', dataset: { id: s.id }, variant: 'ghost' }),
    );
  }

  /* ------------------------- События ------------------------- */

  onChange(name, target) {
    if (name !== 'taken') return;
    const id = target.dataset.id;
    const date = today();
    this.#storage.update('supplementLog', (log) => {
      const set = new Set(log[date] ?? []);
      if (target.checked) set.add(id);
      else set.delete(id);
      return { ...log, [date]: [...set] };
    });
    if (target.checked) this.services.toast.show('Отмечено: принято сегодня');
  }

  async onAction(action, target) {
    if (action === 'period') this.updateSettings({ period: target.dataset.value });
    if (action === 'delete') {
      const s = this.#storage.list('supplements').find((x) => x.id === target.dataset.id);
      const ok = await this.services.modal.confirm({ title: `Удалить «${s?.name ?? 'добавку'}»?`, message: 'История отметок по этой добавке перестанет учитываться в регулярности.', confirmLabel: 'Удалить', danger: true });
      if (ok) this.#storage.removeItem('supplements', target.dataset.id);
    }
  }

  onSubmit(name, form) {
    if (name !== 'supplement') return;
    clearFormErrors(form);
    const v = formValues(form);
    if (!v.name || v.name.length < 2) {
      setFieldError(form.elements.name, 'Введите название');
      return;
    }
    if (v.schedule === 'custom' && !v.time) {
      setFieldError(form.elements.time, 'Укажите время');
      return;
    }
    form.reset();
    this.#storage.add('supplements', {
      id: createId('sup'),
      name: v.name.slice(0, 50),
      dose: v.dose.slice(0, 30),
      schedule: v.schedule,
      time: v.schedule === 'custom' ? v.time : SUPPLEMENT_SLOTS[v.schedule].time,
      frequency: v.frequency,
      startDate: today(),
    });
    this.services.toast.show(`Добавка «${v.name}» добавлена`);
  }
}
