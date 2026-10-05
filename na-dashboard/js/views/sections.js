/**
 * Конфигурация разделов SPA: заголовки, стандартная раскладка виджетов
 * и компоненты над сеткой. Раскладку пользователь может менять — она сохраняется.
 */
import { ActivityRings, DailyInsightCard, InsightsPanel } from '../ui/TodayComponents.js';

export const SECTIONS = Object.freeze([
  {
    key: 'today',
    title: 'Сегодня',
    nav: 'Сегодня',
    icon: 'home',
    subtitle: 'Главное за день: восстановление, активность и питание.',
    header: (ctx) => [new DailyInsightCard({ services: ctx.services }), new ActivityRings({ services: ctx.services })],
    defaultLayout: [
      { type: 'recovery', size: 'medium' },
      { type: 'training', size: 'medium' },
      { type: 'mood', size: 'medium' },
      { type: 'nutrition', size: 'medium' },
    ],
  },
  {
    key: 'training',
    title: 'Тренировки',
    nav: 'Тренировки',
    icon: 'dumbbell',
    subtitle: 'Записывайте подходы, следите за рекордами, находите новые упражнения.',
    defaultLayout: [
      { type: 'training', size: 'medium' },
      { type: 'library', size: 'medium' },
    ],
  },
  {
    key: 'progress',
    title: 'Прогресс',
    nav: 'Прогресс',
    icon: 'chart',
    subtitle: 'Графики силовых, замеров тела и кардио. Выбирайте один показатель за раз.',
    header: (ctx) => [new InsightsPanel({ services: ctx.services })],
    defaultLayout: [
      { type: 'progress', size: 'large', settings: { tab: 'strength', strengthMetric: 'max' } },
      { type: 'progress', size: 'medium', settings: { tab: 'body', bodyMetric: 'bodyWeight' } },
      { type: 'progress', size: 'medium', settings: { tab: 'cardio', cardioMetric: 'duration' } },
    ],
  },
  {
    key: 'wellbeing',
    title: 'Самочувствие',
    nav: 'Самочувствие',
    icon: 'smile',
    subtitle: 'Сон, восстановление, настроение и биоритмы.',
    defaultLayout: [
      { type: 'recovery', size: 'medium' },
      { type: 'mood', size: 'medium' },
      { type: 'rhythm', size: 'large' },
    ],
  },
  {
    key: 'nutrition',
    title: 'Питание',
    nav: 'Питание',
    icon: 'leaf',
    subtitle: 'Калории, БЖУ, вода и добавки по вашим собственным целям.',
    defaultLayout: [
      { type: 'nutrition', size: 'medium' },
      { type: 'supplements', size: 'medium' },
    ],
  },
]);
