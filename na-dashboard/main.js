/**
 * main.js — точка входа приложения N.A.
 * Создаёт сервисы, регистрирует виджеты, строит навигацию,
 * запускает onboarding или роутер разделов.
 */
import { StorageService } from './js/core/StorageService.js';
import { ThemeManager, THEMES } from './js/core/ThemeManager.js';
import { InsightEngine } from './js/core/InsightEngine.js';
import { WidgetRegistry } from './js/core/WidgetRegistry.js';
import { Router } from './js/core/Router.js';
import { WgerService } from './js/services/WgerService.js';
import { FoodService } from './js/services/FoodService.js';
import { Modal } from './js/ui/Modal.js';
import { Toast } from './js/ui/Toast.js';
import { Onboarding } from './js/ui/Onboarding.js';
import { openSettings } from './js/ui/SettingsDialog.js';
import { SectionView } from './js/views/SectionView.js';
import { SECTIONS } from './js/views/sections.js';
import { createDemoData } from './js/utils/demoData.js';
import { el } from './js/utils/dom.js';
import { icon } from './js/utils/icons.js';

import { TrainingWidget } from './js/widgets/TrainingWidget.js';
import { ProgressWidget } from './js/widgets/ProgressWidget.js';
import { MoodWidget } from './js/widgets/MoodWidget.js';
import { RecoveryWidget } from './js/widgets/RecoveryWidget.js';
import { BiologicalRhythmWidget } from './js/widgets/BiologicalRhythmWidget.js';
import { NutritionWidget } from './js/widgets/NutritionWidget.js';
import { SupplementsWidget } from './js/widgets/SupplementsWidget.js';
import { ExerciseLibraryWidget } from './js/widgets/ExerciseLibraryWidget.js';

/* ------------------------------------------------------------------ */
/* Сервисы                                                             */
/* ------------------------------------------------------------------ */

const storage = new StorageService();
const theme = new ThemeManager(storage);
const toast = new Toast();
const liveRegion = document.getElementById('live-region');
const announce = (message) => {
  liveRegion.textContent = '';
  requestAnimationFrame(() => {
    liveRegion.textContent = message;
  });
};

// Новый тип виджета = новый класс + одна строка регистрации. Dashboard менять не нужно.
const registry = new WidgetRegistry()
  .register(TrainingWidget)
  .register(ProgressWidget)
  .register(MoodWidget)
  .register(RecoveryWidget)
  .register(BiologicalRhythmWidget)
  .register(NutritionWidget)
  .register(SupplementsWidget)
  .register(ExerciseLibraryWidget);

const services = {
  storage,
  theme,
  toast,
  announce,
  modal: Modal,
  insights: new InsightEngine(storage),
  wger: new WgerService(),
  food: new FoodService(),
  navigate: (path) => router.navigate(path),
  loadDemo: () => loadDemo(),
};

const ctx = { storage, registry, services };

const router = new Router({
  outlet: document.getElementById('main'),
  fallback: 'today',
  routes: Object.fromEntries(SECTIONS.map((section) => [section.key, () => new SectionView(section, ctx)])),
  onChange: (path) => updateNav(path),
});
let routerStarted = false;

/* ------------------------------------------------------------------ */
/* Навигация и оболочка                                                */
/* ------------------------------------------------------------------ */

function buildNav() {
  const list = document.getElementById('nav-list');
  list.replaceChildren(
    ...SECTIONS.map((s) =>
      el(
        'li',
        {},
        el('a', { class: ['nav-link', `accent-${s.key}`], href: `#/${s.key}`, dataset: { route: s.key } }, el('span', { class: 'nav-link__icon', attrs: { 'aria-hidden': 'true' } }, icon(s.icon, { size: 20 })), el('span', { class: 'nav-link__text', text: s.nav }), s.short ? el('span', { class: 'nav-link__short', text: s.short, attrs: { 'aria-hidden': 'true' } }) : null),
      ),
    ),
  );

  const themeSwitch = document.getElementById('theme-switch');
  const themeIcons = { light: 'sun', dark: 'moon', system: 'monitor' };
  themeSwitch.replaceChildren(
    ...Object.entries(THEMES).map(([mode, label]) =>
      el('button', { type: 'button', class: 'theme-btn', dataset: { theme: mode }, attrs: { 'aria-label': `Тема: ${label}`, 'aria-pressed': String(theme.mode === mode), title: label } }, icon(themeIcons[mode], { size: 16 })),
    ),
  );
  themeSwitch.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-theme]');
    if (btn) theme.set(btn.dataset.theme);
  });
  theme.addEventListener('change', () => {
    themeSwitch.querySelectorAll('[data-theme]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.theme === theme.mode)));
  });

  document.querySelectorAll('[data-open-settings]').forEach((btn) =>
    btn.addEventListener('click', () =>
      openSettings({
        storage,
        theme,
        onEditProfile: () => editProfile(),
        onDemo: () => loadDemo(),
        onReset: () => resetAll(),
      }),
    ),
  );
}

function updateNav(path) {
  document.querySelectorAll('.nav-link').forEach((link) => {
    if (link.dataset.route === path) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const section = SECTIONS.find((s) => s.key === path);
  document.title = section ? `${section.title} · N.A.` : 'N.A.';
}

function startApp() {
  document.body.classList.remove('is-onboarding');
  if (routerStarted) {
    router.reload();
  } else {
    routerStarted = true;
    router.start();
  }
}

/* ------------------------------------------------------------------ */
/* Сценарии                                                            */
/* ------------------------------------------------------------------ */

async function runOnboarding() {
  document.body.classList.add('is-onboarding');
  const result = await new Onboarding({ storage }).open();
  if (result === 'demo') {
    await loadDemo({ skipConfirm: true });
    return;
  }
  if (result === 'saved') {
    toast.show('Профиль сохранён. Добро пожаловать в N.A.!');
    startApp();
  }
}

async function editProfile() {
  const result = await new Onboarding({ storage, editing: true }).open();
  if (result === 'saved') {
    toast.show('Профиль обновлён');
    router.reload();
  }
}

async function loadDemo({ skipConfirm = false } = {}) {
  if (!skipConfirm && storage.hasUserData()) {
    const ok = await Modal.confirm({
      title: 'Загрузить демо-данные?',
      message: 'Текущие записи и профиль будут заменены демонстрационной историей Анны за 7 недель. Раскладка виджетов и тема сохранятся.',
      confirmLabel: 'Заменить данные',
      danger: true,
    });
    if (!ok) return;
  }
  storage.importSnapshot(createDemoData());
  toast.show('Демо-данные загружены: 7 недель истории');
  startApp();
}

async function resetAll() {
  const ok = await Modal.confirm({
    title: 'Удалить все данные?',
    message: 'Профиль, тренировки, записи самочувствия, питания и настройки будут удалены из этого браузера. Действие нельзя отменить.',
    confirmLabel: 'Удалить всё',
    danger: true,
  });
  if (!ok) return;
  router.stop();
  routerStarted = false;
  storage.clearAll();
  theme.sync();
  toast.show('Все данные удалены', { variant: 'info' });
  runOnboarding();
}

/* ------------------------------------------------------------------ */
/* Запуск                                                              */
/* ------------------------------------------------------------------ */

storage.addEventListener('error', () => toast.show('Не удалось сохранить данные: хранилище браузера переполнено или недоступно', { variant: 'error' }));
buildNav();

if (storage.get('profile')) startApp();
else runOnboarding();

// Хук для отладки и автоматических проверок в консоли браузера.
window.NA = Object.freeze({ storage, router, registry, services });
