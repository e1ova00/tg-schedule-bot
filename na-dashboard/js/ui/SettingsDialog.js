/**
 * Диалог «Профиль и настройки»: сводка профиля, тема, демо-данные, удаление данных.
 */
import { el } from '../utils/dom.js';
import { icon } from '../utils/icons.js';
import { THEMES } from '../core/ThemeManager.js';
import { ACTIVITY_LEVELS, EXPERIENCE_LEVELS, GOALS } from '../data/catalog.js';
import { Modal } from './Modal.js';
import { segmented } from './kit.js';

export function openSettings({ storage, theme, onEditProfile, onDemo, onReset }) {
  const profile = storage.get('profile') ?? {};
  const rows = [
    ['Имя', profile.name || '—'],
    ['Пол', profile.sex === 'female' ? 'Женщина' : profile.sex === 'male' ? 'Мужчина' : '—'],
    ['Возраст, рост, вес', profile.age ? `${profile.age} лет · ${profile.height} см · ${profile.weight} кг` : '—'],
    ['Цель', GOALS[profile.goal] ?? '—'],
    ['Активность', ACTIVITY_LEVELS[profile.activity] ?? '—'],
    ['Опыт', EXPERIENCE_LEVELS[profile.experience] ?? '—'],
    ['Тренировок в неделю', profile.workoutsPerWeek ?? '—'],
    ['Сон', profile.sleepTime ? `${profile.sleepTime} → ${profile.wakeTime}` : '—'],
  ];

  const themeControl = segmented({
    action: 'theme',
    label: 'Тема оформления',
    value: theme.mode,
    options: Object.entries(THEMES).map(([value, label]) => ({ value, label })),
  });

  const content = el(
    'div',
    { class: 'settings' },
    el(
      'section',
      { class: 'settings__block', attrs: { 'aria-labelledby': 'settings-profile' } },
      el('h3', { class: 'settings__title', id: 'settings-profile', text: 'Профиль' }),
      el('dl', { class: 'profile-list' }, rows.map(([k, v]) => [el('dt', { text: k }), el('dd', { text: String(v) })])),
      el('button', { type: 'button', class: 'btn btn--secondary', dataset: { settings: 'profile' } }, icon('edit', { size: 16 }), 'Изменить профиль'),
    ),
    el(
      'section',
      { class: 'settings__block', attrs: { 'aria-labelledby': 'settings-theme' } },
      el('h3', { class: 'settings__title', id: 'settings-theme', text: 'Оформление' }),
      themeControl,
    ),
    el(
      'section',
      { class: 'settings__block', attrs: { 'aria-labelledby': 'settings-data' } },
      el('h3', { class: 'settings__title', id: 'settings-data', text: 'Данные' }),
      el('p', { class: 'muted small', text: storage.isPersistent ? 'Все записи хранятся только в localStorage этого браузера.' : 'localStorage недоступен: данные сохранятся только до закрытия вкладки.' }),
      el(
        'div',
        { class: 'actions' },
        el('button', { type: 'button', class: 'btn btn--secondary', dataset: { settings: 'demo' } }, icon('database', { size: 16 }), 'Загрузить демо-данные'),
        el('button', { type: 'button', class: 'btn btn--danger-ghost', dataset: { settings: 'reset' } }, icon('trash', { size: 16 }), 'Удалить все данные'),
      ),
    ),
    el('p', { class: 'note-inline', text: 'N.A. — учебный проект и не является медицинским сервисом. Наблюдения не заменяют консультацию специалиста.' }),
  );

  const modal = new Modal({ title: 'Профиль и настройки', content, className: 'modal--settings' });
  modal.element.addEventListener('click', (event) => {
    const themeBtn = event.target.closest('[data-action="theme"]');
    if (themeBtn) {
      theme.set(themeBtn.dataset.value);
      themeControl.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === themeBtn)));
      return;
    }
    const action = event.target.closest('[data-settings]')?.dataset.settings;
    if (!action) return;
    modal.close(action);
  });
  modal.open();
  modal.closed.then((value) => {
    if (value === 'profile') onEditProfile();
    if (value === 'demo') onDemo();
    if (value === 'reset') onReset();
  });
  return modal;
}
