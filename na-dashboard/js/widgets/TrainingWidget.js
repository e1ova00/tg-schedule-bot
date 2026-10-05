/**
 * TrainingWidget — силовые тренировки: старт по шаблону, упражнения, подходы
 * (вес, повторы, RPE), заметка, сохранение, определение PR, шаблоны и свои упражнения.
 *
 * Черновик текущей тренировки хранится в StorageService ('activeWorkout'),
 * поэтому переживает перезагрузку страницы и переключение разделов.
 */
import { UIComponent } from '../core/UIComponent.js';
import { createId } from '../core/StorageService.js';
import { el, field, input, select, formValues, setFieldError, clearFormErrors } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { formatClock, isWithinLastDays, relativeDayLabel, today } from '../utils/date.js';
import { detectPRs, workoutVolume } from '../utils/calculations.js';
import { fmt, fmtKg, plural, toNumber } from '../utils/format.js';
import { MUSCLE_GROUPS, muscleLabel } from '../data/catalog.js';
import { allTemplates, exerciseOptions, findExercise } from '../data/exercises.js';
import { button, emptyState, iconButton, stat } from '../ui/kit.js';
import { Modal } from '../ui/Modal.js';

const RPE_OPTIONS = [{ value: '', label: '—' }, ...['6', '6.5', '7', '7.5', '8', '8.5', '9', '9.5', '10'].map((v) => ({ value: v, label: v }))];

export class TrainingWidget extends UIComponent {
  static type = 'training';
  static meta = {
    title: 'Тренировки',
    icon: 'dumbbell',
    accent: 'training',
    description: 'Подходы, вес, повторы, RPE, шаблоны и личные рекорды.',
  };

  #tick = null;
  #timerNode = null;

  constructor(config) {
    super(config);
    this.watchStorage(['activeWorkout', 'workouts', 'exercises', 'templates']);
  }

  get #storage() {
    return this.services.storage;
  }

  renderBody(body) {
    const active = this.#storage.get('activeWorkout');
    if (active) this.#renderActive(body, active);
    else this.#renderIdle(body);
    this.#syncTimer(active);
  }

  /* ------------------------------------------------------------ */
  /* Нет активной тренировки                                       */
  /* ------------------------------------------------------------ */

  #renderIdle(body) {
    const workouts = this.#storage.list('workouts').sort((a, b) => b.startedAt.localeCompare(a.startedAt));
    const week = workouts.filter((w) => isWithinLastDays(w.date, 7));
    const templateOptions = [
      { value: '', label: 'Пустая тренировка' },
      ...allTemplates(this.#storage).map((t) => ({ value: t.id, label: t.name })),
    ];

    body.append(
      el(
        'div',
        { class: 'stat-row' },
        stat({ label: 'За 7 дней', value: String(week.length), unit: plural(week.length, ['тренировка', 'тренировки', 'тренировок']), accent: 'training' }),
        stat({ label: 'Объём за 7 дней', value: fmt(week.reduce((s, w) => s + workoutVolume(w), 0)), unit: 'кг', accent: 'training' }),
      ),
      el(
        'form',
        { class: 'inline-form', dataset: { form: 'start' } },
        field('Шаблон', select('template', templateOptions)),
        button('Начать тренировку', { type: 'submit', variant: 'primary', iconName: 'play' }),
      ),
    );

    const prs = workouts.flatMap((w) => (w.prs ?? []).map((pr) => ({ ...pr, date: w.date }))).slice(0, 3);
    if (prs.length) {
      body.append(
        el('h3', { class: 'section-title', text: 'Последние рекорды' }),
        el(
          'ul',
          { class: 'list' },
          prs.map((pr) =>
            el(
              'li',
              { class: 'list__item' },
              el('span', { class: 'badge badge--training' }, icon('trophy', { size: 14 }), 'PR'),
              el('span', { class: 'list__main', text: `${pr.name}: ${fmtKg(pr.weight)}` }),
              el('span', { class: 'list__meta', text: relativeDayLabel(pr.date) }),
            ),
          ),
        ),
      );
    }

    body.append(el('h3', { class: 'section-title', text: 'История' }));
    if (!workouts.length) {
      body.append(emptyState({ iconName: 'dumbbell', title: 'Тренировок пока нет', text: 'Начните первую тренировку — история и рекорды появятся здесь.' }));
    } else {
      body.append(
        el(
          'ul',
          { class: 'list' },
          workouts.slice(0, 4).map((w) =>
            el(
              'li',
              { class: 'list__item' },
              el(
                'div',
                { class: 'list__main' },
                el('span', { class: 'list__title', text: w.name }),
                el('span', { class: 'list__sub', text: `${w.exercises.length} ${plural(w.exercises.length, ['упражнение', 'упражнения', 'упражнений'])} · ${fmt(workoutVolume(w))} кг` }),
              ),
              el('span', { class: 'list__meta', text: relativeDayLabel(w.date) }),
              iconButton('trash', `Удалить тренировку «${w.name}» от ${relativeDayLabel(w.date)}`, { action: 'delete-workout', dataset: { id: w.id }, variant: 'ghost' }),
            ),
          ),
        ),
      );
    }

    body.append(this.#customExerciseForm(), this.#customTemplatesList());
  }

  #customExerciseForm() {
    const custom = this.#storage.list('exercises');
    return el(
      'details',
      { class: 'disclosure', dataset: { key: 'custom-exercise' } },
      el('summary', { class: 'disclosure__summary', text: `Свои упражнения (${custom.length})` }),
      el(
        'form',
        { class: 'form-grid', dataset: { form: 'custom-exercise' }, attrs: { novalidate: true } },
        field('Название', input('name', { attrs: { maxlength: 60, autocomplete: 'off' }, required: true })),
        field('Мышечная группа', select('muscle', MUSCLE_GROUPS.map((m) => ({ value: m.id, label: m.label })))),
        button('Создать упражнение', { type: 'submit', variant: 'secondary', iconName: 'plus' }),
      ),
      custom.length
        ? el(
            'ul',
            { class: 'chips' },
            custom.map((e) => el('li', { class: 'chip', text: `${e.name} · ${muscleLabel(e.muscle)}${e.source === 'wger' ? ' · wger' : ''}` })),
          )
        : null,
    );
  }

  #customTemplatesList() {
    const templates = this.#storage.list('templates');
    if (!templates.length) return null;
    return el(
      'details',
      { class: 'disclosure', dataset: { key: 'templates' } },
      el('summary', { class: 'disclosure__summary', text: `Мои шаблоны (${templates.length})` }),
      el(
        'ul',
        { class: 'list' },
        templates.map((t) =>
          el(
            'li',
            { class: 'list__item' },
            el('span', { class: 'list__main', text: `${t.name} · ${t.exerciseIds.length} упр.` }),
            iconButton('trash', `Удалить шаблон «${t.name}»`, { action: 'delete-template', dataset: { id: t.id }, variant: 'ghost' }),
          ),
        ),
      ),
    );
  }

  /* ------------------------------------------------------------ */
  /* Активная тренировка                                           */
  /* ------------------------------------------------------------ */

  #renderActive(body, workout) {
    this.#timerNode = el('span', { class: 'timer', attrs: { 'aria-label': 'Длительность тренировки' } }, this.#elapsed(workout));
    body.append(
      el(
        'div',
        { class: 'active-head' },
        el('span', { class: 'live-dot', attrs: { 'aria-hidden': 'true' } }),
        el('div', { class: 'active-head__text' }, el('p', { class: 'active-head__title', text: workout.name }), el('p', { class: 'muted', text: 'Тренировка идёт' })),
        this.#timerNode,
      ),
    );

    if (!workout.exercises.length) {
      body.append(emptyState({ iconName: 'dumbbell', title: 'Добавьте первое упражнение', text: 'Выберите упражнение из списка ниже.' }));
    }

    workout.exercises.forEach((ex, index) => body.append(this.#exerciseCard(ex, index)));

    body.append(
      el(
        'form',
        { class: 'inline-form', dataset: { form: 'add-exercise' } },
        field('Упражнение', select('exerciseId', exerciseOptions(this.#storage))),
        button('Добавить', { type: 'submit', variant: 'secondary', iconName: 'plus' }),
      ),
      el(
        'form',
        { class: 'stack', dataset: { form: 'note' } },
        field('Заметка к тренировке', el('textarea', { class: 'input', name: 'note', rows: 2, value: workout.note ?? '', dataset: { field: 'note' }, attrs: { maxlength: 500, placeholder: 'Самочувствие, техника, что изменить в следующий раз' } })),
      ),
      el(
        'div',
        { class: 'actions' },
        button('Сохранить тренировку', { action: 'finish', variant: 'primary', iconName: 'check' }),
        button('Сохранить как шаблон', { action: 'save-template', variant: 'secondary', disabled: !workout.exercises.length }),
        button('Отменить', { action: 'cancel', variant: 'ghost' }),
      ),
    );
  }

  #exerciseCard(ex, index) {
    const sets = ex.sets.length
      ? el(
          'table',
          { class: 'sets' },
          el('caption', { class: 'sr-only', text: `Подходы: ${ex.name}` }),
          el('thead', {}, el('tr', {}, ['№', 'Вес', 'Повт.', 'RPE', ''].map((h) => el('th', { scope: 'col', text: h })))),
          el(
            'tbody',
            {},
            ex.sets.map((s, i) =>
              el(
                'tr',
                {},
                el('td', { text: String(i + 1) }),
                el('td', { text: fmtKg(s.weight) }),
                el('td', { text: String(s.reps) }),
                el('td', { text: s.rpe ? String(s.rpe) : '—' }),
                el('td', {}, iconButton('x', `Удалить подход ${i + 1}: ${ex.name}`, { action: 'remove-set', dataset: { index, set: i, id: `${index}-${i}` }, variant: 'ghost' })),
              ),
            ),
          ),
        )
      : el('p', { class: 'muted small', text: 'Подходов пока нет' });

    const last = ex.sets.at(-1);
    return el(
      'section',
      { class: 'exercise-card', attrs: { 'aria-label': ex.name } },
      el(
        'header',
        { class: 'exercise-card__head' },
        el('div', {}, el('h3', { class: 'exercise-card__title', text: ex.name }), el('span', { class: 'tag', text: muscleLabel(ex.muscle) })),
        iconButton('trash', `Убрать упражнение «${ex.name}»`, { action: 'remove-exercise', dataset: { index, id: String(index) }, variant: 'ghost' }),
      ),
      sets,
      el(
        'form',
        { class: 'set-form', dataset: { form: 'add-set', key: String(index) }, attrs: { novalidate: true } },
        field('Вес, кг', input('weight', { type: 'number', value: last?.weight ?? '', attrs: { min: 0, max: 500, step: 0.5, inputmode: 'decimal' } })),
        field('Повторы', input('reps', { type: 'number', value: last?.reps ?? '', attrs: { min: 1, max: 100, step: 1, inputmode: 'numeric' } })),
        field('RPE', select('rpe', RPE_OPTIONS)),
        button('Подход', { type: 'submit', variant: 'tinted', iconName: 'plus' }),
      ),
    );
  }

  #elapsed(workout) {
    return formatClock((Date.now() - new Date(workout.startedAt).getTime()) / 1000);
  }

  /** Один интервал на виджет; останавливается, когда тренировка не активна, и в destroy(). */
  #syncTimer(active) {
    if (active && this.#tick === null) {
      this.#tick = this.setInterval(() => {
        const current = this.#storage.get('activeWorkout');
        if (current && this.#timerNode?.isConnected) this.#timerNode.textContent = this.#elapsed(current);
      }, 1000);
    } else if (!active && this.#tick !== null) {
      this.clearInterval(this.#tick);
      this.#tick = null;
    }
  }

  /* ------------------------------------------------------------ */
  /* Обработчики                                                   */
  /* ------------------------------------------------------------ */

  #updateDraft(fn) {
    this.#storage.update('activeWorkout', (draft) => (draft ? fn(draft) : draft));
  }

  #exerciseEntry(exerciseId) {
    const ex = findExercise(this.#storage, exerciseId);
    return ex ? { exerciseId: ex.id, name: ex.name, muscle: ex.muscle, sets: [] } : null;
  }

  onSubmit(name, form) {
    const values = formValues(form);
    clearFormErrors(form);

    if (name === 'start') {
      const template = allTemplates(this.#storage).find((t) => t.id === values.template);
      const now = new Date();
      this.#storage.set('activeWorkout', {
        id: createId('wo'),
        name: template?.name ?? 'Тренировка',
        date: today(),
        startedAt: now.toISOString(),
        exercises: (template?.exerciseIds ?? []).map((id) => this.#exerciseEntry(id)).filter(Boolean),
        note: '',
      });
      this.services.announce?.('Тренировка начата');
      return;
    }

    if (name === 'add-exercise') {
      const entry = this.#exerciseEntry(values.exerciseId);
      if (entry) this.#updateDraft((d) => ({ ...d, exercises: [...d.exercises, entry] }));
      return;
    }

    if (name === 'add-set') {
      const index = Number(form.dataset.key);
      const weight = toNumber(values.weight);
      const reps = toNumber(values.reps);
      let ok = true;
      if (weight === null || weight < 0 || weight > 500) {
        setFieldError(form.elements.weight, 'Вес от 0 до 500 кг');
        ok = false;
      }
      if (reps === null || reps < 1 || reps > 100 || !Number.isInteger(reps)) {
        setFieldError(form.elements.reps, 'Целое число от 1 до 100');
        ok = false;
      }
      if (!ok) return;
      const set = { weight, reps, rpe: toNumber(values.rpe) };
      this.#updateDraft((d) => {
        d.exercises[index]?.sets.push(set);
        return d;
      });
      this.services.announce?.(`Подход добавлен: ${fmtKg(weight)} × ${reps}`);
      return;
    }

    if (name === 'custom-exercise') {
      if (!values.name || values.name.length < 2) {
        setFieldError(form.elements.name, 'Введите название (минимум 2 символа)');
        return;
      }
      const exists = this.#storage.list('exercises').some((e) => e.name.toLowerCase() === values.name.toLowerCase());
      if (exists) {
        setFieldError(form.elements.name, 'Такое упражнение уже есть');
        return;
      }
      form.reset();
      this.#storage.add('exercises', { id: createId('ex'), name: values.name, muscle: values.muscle, source: 'custom' });
      this.services.toast?.show(`Упражнение «${values.name}» создано`);
    }
  }

  onChange(name, target) {
    if (name === 'note') this.#updateDraft((d) => ({ ...d, note: target.value.slice(0, 500) }));
  }

  async onAction(action, target) {
    const { modal, toast } = this.services;
    const index = Number(target.dataset.index);

    switch (action) {
      case 'remove-set':
        this.#updateDraft((d) => {
          d.exercises[index]?.sets.splice(Number(target.dataset.set), 1);
          return d;
        });
        break;
      case 'remove-exercise':
        this.#updateDraft((d) => {
          d.exercises.splice(index, 1);
          return d;
        });
        break;
      case 'finish':
        this.#finish();
        break;
      case 'save-template':
        this.#saveTemplate();
        break;
      case 'cancel':
        if (await modal.confirm({ title: 'Отменить тренировку?', message: 'Несохранённые подходы будут удалены.', confirmLabel: 'Отменить тренировку', cancelLabel: 'Продолжить', danger: true })) {
          this.#storage.set('activeWorkout', null);
          toast.show('Тренировка отменена', { variant: 'info' });
        }
        break;
      case 'delete-workout':
        if (await modal.confirm({ title: 'Удалить тренировку?', message: 'Запись исчезнет из истории и графиков. Это действие нельзя отменить.', confirmLabel: 'Удалить', danger: true })) {
          this.#storage.removeItem('workouts', target.dataset.id);
          toast.show('Тренировка удалена', { variant: 'info' });
        }
        break;
      case 'delete-template':
        if (await modal.confirm({ title: 'Удалить шаблон?', message: 'Сохранённые тренировки не изменятся.', confirmLabel: 'Удалить', danger: true })) {
          this.#storage.removeItem('templates', target.dataset.id);
        }
        break;
      default:
    }
  }

  #finish() {
    const draft = this.#storage.get('activeWorkout');
    if (!draft) return;
    const exercises = draft.exercises.filter((e) => e.sets.length);
    if (!exercises.length) {
      this.services.toast.show('Добавьте хотя бы один подход, чтобы сохранить тренировку', { variant: 'error' });
      return;
    }
    const history = this.#storage.list('workouts');
    const workout = { ...draft, exercises, endedAt: new Date().toISOString() };
    workout.prs = detectPRs(workout, history);
    workout.volume = workoutVolume(workout);
    this.#storage.add('workouts', workout);
    this.#storage.set('activeWorkout', null);

    if (workout.prs.length) {
      workout.prs.forEach((pr) => this.services.toast.show(`Новый PR — ${pr.name}: ${fmtKg(pr.weight)}`, { variant: 'pr', duration: 5000 }));
    } else {
      this.services.toast.show(`Тренировка сохранена · ${fmt(workout.volume)} кг объёма`);
    }
  }

  async #saveTemplate() {
    const draft = this.#storage.get('activeWorkout');
    if (!draft?.exercises.length) return;
    const nameInput = input('templateName', { value: draft.name === 'Тренировка' ? '' : `${draft.name} (моя)`, attrs: { maxlength: 40 } });
    const modal = new Modal({
      title: 'Сохранить как шаблон',
      className: 'modal--compact',
      content: field('Название шаблона', nameInput),
      actions: [
        { label: 'Отмена', value: 'cancel' },
        { label: 'Сохранить', value: 'save', variant: 'primary' },
      ],
    }).open();
    nameInput.focus();
    nameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        modal.close('save');
      }
    });
    if ((await modal.closed) !== 'save' || this.isDestroyed) return;
    const name = nameInput.value.trim() || `Шаблон ${this.#storage.list('templates').length + 1}`;
    this.#storage.add('templates', { id: createId('tpl'), name, exerciseIds: draft.exercises.map((e) => e.exerciseId) });
    this.services.toast.show(`Шаблон «${name}» сохранён`);
  }

  /** Используется ExerciseLibraryWidget: добавить упражнение в текущую тренировку. */
  static addToActiveWorkout(storage, exercise) {
    const draft = storage.get('activeWorkout');
    if (!draft) return false;
    draft.exercises.push({ exerciseId: exercise.id, name: exercise.name, muscle: exercise.muscle, sets: [] });
    storage.set('activeWorkout', draft);
    return true;
  }
}
