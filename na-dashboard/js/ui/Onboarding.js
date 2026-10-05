/**
 * Onboarding — полноэкранный пошаговый мастер (5 шагов + приветствие).
 * Используется и при первом запуске, и для редактирования профиля.
 * Построен на <dialog>: фокус удерживается внутри, шаги переключаются без перезагрузки.
 */
import { el, field, input, select, uid, setFieldError, clearFormErrors } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { toNumber } from '../utils/format.js';
import { ACTIVITY_LEVELS, EXPERIENCE_LEVELS, GOALS, WORKOUT_TIME_PREFS } from '../data/catalog.js';

const STEPS = ['sex', 'body', 'goal', 'lifestyle', 'schedule'];

function choiceGroup({ name, legend, options, value, columns = 2 }) {
  return el(
    'fieldset',
    { class: 'choice-group' },
    el('legend', { class: 'choice-group__legend', text: legend }),
    el(
      'div',
      { class: `choice-group__grid cols-${columns}` },
      Object.entries(options).map(([v, label]) => {
        const id = uid('ch');
        return el(
          'span',
          { class: 'choice' },
          el('input', { type: 'radio', name, value: v, id, class: 'choice__input', checked: value === v }),
          el('label', { class: 'choice__label', for: id }, el('span', { text: label })),
        );
      }),
    ),
  );
}

export class Onboarding {
  #storage;
  #dialog = null;
  #form = null;
  #step = 0;
  #data;
  #editing;
  #onComplete;
  #onDemo;
  #resolve;

  constructor({ storage, onComplete, onDemo, editing = false }) {
    this.#storage = storage;
    this.#onComplete = onComplete;
    this.#onDemo = onDemo;
    this.#editing = editing;
    this.#data = {
      sex: '',
      name: '',
      age: '',
      height: '',
      weight: '',
      goal: '',
      occupation: '',
      activity: '',
      workoutsPerWeek: 3,
      experience: '',
      sleepTime: '23:00',
      wakeTime: '07:00',
      preferredWorkoutTime: 'evening',
      mealsPerDay: 3,
      ...(storage.get('profile') ?? {}),
    };
    this.#step = editing ? 1 : 0;
  }

  open() {
    this.#dialog = el('dialog', { class: 'onboarding', attrs: { 'aria-labelledby': 'onb-title' } });
    this.#dialog.addEventListener('cancel', (e) => {
      e.preventDefault();
      if (this.#editing) this.close();
    });
    this.#dialog.addEventListener('click', (e) => this.#onClick(e));
    this.#dialog.addEventListener('submit', (e) => {
      e.preventDefault();
      this.#next();
    });
    document.body.append(this.#dialog);
    this.#dialog.showModal();
    this.#render();
    return new Promise((resolve) => {
      this.#resolve = resolve;
    });
  }

  close(result = null) {
    this.#dialog?.close();
    this.#dialog?.remove();
    this.#dialog = null;
    this.#resolve?.(result);
  }

  #render() {
    const stepIndex = this.#step - 1;
    const content = this.#step === 0 ? this.#welcome() : this.#stepContent(STEPS[stepIndex]);
    const progress =
      this.#step === 0
        ? null
        : el(
            'div',
            { class: 'onboarding__progress' },
            el('p', { class: 'onboarding__counter', text: `Шаг ${this.#step} из ${STEPS.length}` }),
            el(
              'div',
              { class: 'onboarding__dots', attrs: { 'aria-hidden': 'true' } },
              STEPS.map((_, i) => el('span', { class: ['dot', i < this.#step && 'is-done', i === stepIndex && 'is-current'] })),
            ),
          );

    this.#form = el('form', { class: 'onboarding__form', attrs: { novalidate: true } }, content.body, this.#step === 0 ? null : this.#nav());
    const panel = el(
      'div',
      { class: 'onboarding__panel', dataset: { step: String(this.#step) } },
      this.#editing ? el('button', { type: 'button', class: 'icon-btn onboarding__close', dataset: { onb: 'close' }, attrs: { 'aria-label': 'Закрыть без сохранения' } }, icon('x', { size: 18 })) : null,
      progress,
      el('h2', { class: 'onboarding__title', id: 'onb-title', text: content.title, attrs: { tabindex: '-1' } }),
      content.subtitle ? el('p', { class: 'onboarding__subtitle', text: content.subtitle }) : null,
      this.#form,
    );
    this.#dialog.replaceChildren(panel);
    (this.#form.querySelector('input:checked, input:not([type=radio]), select') ?? this.#dialog.querySelector('#onb-title'))?.focus();
  }

  #welcome() {
    return {
      title: 'N.A. — ваш дневник силы и самочувствия',
      subtitle: 'Тренировки, восстановление, настроение и питание в одном месте. Пять коротких шагов — и панель настроена под вас.',
      body: el(
        'div',
        { class: 'welcome' },
        el(
          'ul',
          { class: 'welcome__features' },
          [
            ['dumbbell', 'training', 'Тренировки, PR и прогресс'],
            ['moon', 'recovery', 'Сон и индекс восстановления'],
            ['smile', 'mood', 'Самочувствие и закономерности'],
            ['leaf', 'nutrition', 'Питание, вода и добавки'],
          ].map(([i, accent, text]) => el('li', { class: `accent-${accent}` }, el('span', { class: 'welcome__icon', attrs: { 'aria-hidden': 'true' } }, icon(i, { size: 18 })), el('span', { text }))),
        ),
        el('p', { class: 'muted small', text: 'Все данные хранятся только в этом браузере. N.A. не является медицинским сервисом.' }),
        el(
          'div',
          { class: 'actions actions--center' },
          el('button', { type: 'submit', class: 'btn btn--primary btn--lg' }, 'Начать', icon('chevronRight', { size: 18 })),
          el('button', { type: 'button', class: 'btn btn--ghost', dataset: { onb: 'demo' } }, icon('database', { size: 16 }), 'Посмотреть с демо-данными'),
        ),
      ),
    };
  }

  #stepContent(step) {
    const d = this.#data;
    switch (step) {
      case 'sex':
        return {
          title: 'Расскажите о себе',
          subtitle: 'Пол влияет только на содержимое виджета «Биоритмы».',
          body: el(
            'div',
            { class: 'stack' },
            choiceGroup({ name: 'sex', legend: 'Пол', options: { female: 'Женщина', male: 'Мужчина' }, value: d.sex }),
            field('Как к вам обращаться? (необязательно)', input('name', { value: d.name, attrs: { maxlength: 30, autocomplete: 'given-name' } })),
          ),
        };
      case 'body':
        return {
          title: 'Основные данные',
          subtitle: 'Используются для контекста ваших записей.',
          body: el(
            'div',
            { class: 'form-grid form-grid--3' },
            field('Возраст', input('age', { type: 'number', value: d.age, attrs: { min: 14, max: 100, inputmode: 'numeric' }, required: true }), { hint: 'лет' }),
            field('Рост', input('height', { type: 'number', value: d.height, attrs: { min: 120, max: 230, inputmode: 'numeric' }, required: true }), { hint: 'см' }),
            field('Вес', input('weight', { type: 'number', value: d.weight, attrs: { min: 30, max: 300, step: 0.1, inputmode: 'decimal' }, required: true }), { hint: 'кг' }),
          ),
        };
      case 'goal':
        return {
          title: 'Ваша основная цель',
          subtitle: 'Её можно изменить в любой момент в профиле.',
          body: choiceGroup({ name: 'goal', legend: 'Цель', options: GOALS, value: d.goal, columns: 1 }),
        };
      case 'lifestyle':
        return {
          title: 'Образ жизни',
          body: el(
            'div',
            { class: 'stack' },
            field('Профессия или тип занятости', input('occupation', { value: d.occupation, attrs: { maxlength: 60, placeholder: 'Например, дизайнер, студент' } })),
            choiceGroup({ name: 'activity', legend: 'Повседневная активность', options: ACTIVITY_LEVELS, value: d.activity, columns: 3 }),
            el(
              'div',
              { class: 'form-grid form-grid--2' },
              field('Тренировок в неделю', input('workoutsPerWeek', { type: 'number', value: d.workoutsPerWeek, attrs: { min: 0, max: 14, inputmode: 'numeric' } })),
            ),
            choiceGroup({ name: 'experience', legend: 'Тренировочный опыт', options: EXPERIENCE_LEVELS, value: d.experience, columns: 3 }),
          ),
        };
      default:
        return {
          title: 'Режим дня',
          subtitle: 'КБЖУ вы сможете указать позже в разделе «Питание».',
          body: el(
            'div',
            { class: 'form-grid form-grid--2' },
            field('Обычно засыпаю в', input('sleepTime', { type: 'time', value: d.sleepTime, required: true })),
            field('Обычно просыпаюсь в', input('wakeTime', { type: 'time', value: d.wakeTime, required: true })),
            field('Предпочитаемое время тренировки', select('preferredWorkoutTime', Object.entries(WORKOUT_TIME_PREFS).map(([value, label]) => ({ value, label })), { value: d.preferredWorkoutTime })),
            field('Приёмов пищи в день', input('mealsPerDay', { type: 'number', value: d.mealsPerDay, attrs: { min: 1, max: 8, inputmode: 'numeric' } })),
          ),
        };
    }
  }

  #nav() {
    const last = this.#step === STEPS.length;
    return el(
      'div',
      { class: 'onboarding__nav' },
      this.#step > 1 || !this.#editing
        ? el('button', { type: 'button', class: 'btn btn--ghost', dataset: { onb: 'back' } }, icon('chevronLeft', { size: 18 }), 'Назад')
        : el('span'),
      el('button', { type: 'submit', class: 'btn btn--primary btn--lg' }, last ? 'Готово' : 'Далее', icon(last ? 'check' : 'chevronRight', { size: 18 })),
    );
  }

  #onClick(event) {
    const action = event.target.closest('[data-onb]')?.dataset.onb;
    if (action === 'back') {
      this.#step = Math.max(0, this.#step - 1);
      this.#render();
    } else if (action === 'close') {
      this.close();
    } else if (action === 'demo') {
      this.close('demo');
      this.#onDemo?.();
    }
  }

  /** Валидация и сохранение значений текущего шага. */
  #collect() {
    const form = this.#form;
    clearFormErrors(form);
    const step = STEPS[this.#step - 1];
    const get = (n) => form.elements[n]?.value?.trim?.() ?? '';
    const radio = (n) => form.querySelector(`input[name="${n}"]:checked`)?.value ?? '';
    const fail = (control, message) => {
      setFieldError(control, message);
      control.focus();
      return false;
    };
    const failGroup = (name, message) => {
      const first = form.querySelector(`input[name="${name}"]`);
      const group = first.closest('fieldset');
      group.querySelector('.field__error')?.remove();
      group.append(el('p', { class: 'field__error', text: message, attrs: { role: 'alert' } }));
      first.focus();
      return false;
    };

    if (step === 'sex') {
      if (!radio('sex')) return failGroup('sex', 'Выберите вариант');
      this.#data.sex = radio('sex');
      this.#data.name = get('name').slice(0, 30);
    }
    if (step === 'body') {
      const checks = [
        ['age', 14, 100, 'Возраст от 14 до 100 лет'],
        ['height', 120, 230, 'Рост от 120 до 230 см'],
        ['weight', 30, 300, 'Вес от 30 до 300 кг'],
      ];
      for (const [name, min, max, message] of checks) {
        const n = toNumber(get(name));
        if (n === null || n < min || n > max) return fail(form.elements[name], message);
        this.#data[name] = n;
      }
    }
    if (step === 'goal') {
      if (!radio('goal')) return failGroup('goal', 'Выберите основную цель');
      this.#data.goal = radio('goal');
    }
    if (step === 'lifestyle') {
      if (!radio('activity')) return failGroup('activity', 'Выберите уровень активности');
      if (!radio('experience')) return failGroup('experience', 'Выберите опыт');
      const per = toNumber(get('workoutsPerWeek'));
      if (per === null || per < 0 || per > 14) return fail(form.elements.workoutsPerWeek, 'От 0 до 14');
      Object.assign(this.#data, { occupation: get('occupation').slice(0, 60), activity: radio('activity'), experience: radio('experience'), workoutsPerWeek: per });
    }
    if (step === 'schedule') {
      if (!get('sleepTime')) return fail(form.elements.sleepTime, 'Укажите время');
      if (!get('wakeTime')) return fail(form.elements.wakeTime, 'Укажите время');
      const meals = toNumber(get('mealsPerDay'));
      if (meals === null || meals < 1 || meals > 8) return fail(form.elements.mealsPerDay, 'От 1 до 8');
      Object.assign(this.#data, { sleepTime: get('sleepTime'), wakeTime: get('wakeTime'), preferredWorkoutTime: get('preferredWorkoutTime'), mealsPerDay: meals });
    }
    return true;
  }

  #next() {
    if (this.#step === 0) {
      this.#step = 1;
      this.#render();
      return;
    }
    if (!this.#collect()) return;
    if (this.#step < STEPS.length) {
      this.#step += 1;
      this.#render();
      return;
    }
    const profile = { ...this.#data, updatedAt: new Date().toISOString(), createdAt: this.#data.createdAt ?? new Date().toISOString() };
    this.#storage.set('profile', profile);
    this.close('saved');
    this.#onComplete?.(profile);
  }
}
