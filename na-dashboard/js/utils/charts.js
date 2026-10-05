/**
 * Обёртка над Chart.js (подключён как UMD-скрипт в index.html → window.Chart).
 * Единый стиль графиков в духе Apple Health: без вертикальной сетки,
 * мягкие линии, цвета берутся из CSS-переменных текущей темы.
 */
import { el, prefersReducedMotion } from './dom.js';

export const isChartAvailable = () => typeof window.Chart === 'function';

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function accentColor(accent) {
  return cssVar(`--c-${accent}`) || '#0a84ff';
}

function withAlpha(color, alpha) {
  const hex = color.replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(hex)) return color;
  const n = parseInt(hex, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function baseOptions({ yLabel, suggestedMin, suggestedMax, stepSize } = {}) {
  const text = cssVar('--text-secondary');
  const grid = cssVar('--separator');
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: prefersReducedMotion() ? false : { duration: 500, easing: 'easeOutQuart' },
    interaction: { mode: 'index', intersect: false },
    layout: { padding: { top: 4, right: 4 } },
    plugins: {
      legend: { display: false, labels: { color: text, usePointStyle: true, boxWidth: 8, boxHeight: 8 } },
      tooltip: {
        backgroundColor: cssVar('--surface-elevated') || '#fff',
        titleColor: cssVar('--text-primary'),
        bodyColor: cssVar('--text-primary'),
        borderColor: grid,
        borderWidth: 1,
        padding: 10,
        cornerRadius: 12,
        displayColors: false,
      },
    },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { color: text, maxRotation: 0, autoSkip: true, maxTicksLimit: 6, font: { size: 11 } } },
      y: {
        suggestedMin,
        suggestedMax,
        grid: { color: grid },
        border: { display: false },
        ticks: { color: text, font: { size: 11 }, maxTicksLimit: 5, stepSize },
        title: yLabel ? { display: true, text: yLabel, color: text, font: { size: 11 } } : undefined,
      },
    },
  };
}

/**
 * Создать контейнер графика с canvas. Возвращает { wrapper, canvas }.
 * Canvas получает role="img" и текстовое описание для скринридеров.
 */
export function chartContainer(summary, { height = 200 } = {}) {
  const canvas = el('canvas', { attrs: { role: 'img', 'aria-label': summary } });
  const wrapper = el('div', { class: 'chart', style: { height: `${height}px` } }, canvas);
  return { wrapper, canvas };
}

/** Заглушка, если Chart.js не загрузился. */
export function chartFallback(summary) {
  return el('p', { class: 'chart-fallback muted', text: `График недоступен. ${summary}` });
}

/**
 * Линейный график.
 * datasets: [{ label, data, accent }]
 */
export function createLineChart(canvas, { labels, datasets, yLabel, suggestedMin, suggestedMax, stepSize, legend = false }) {
  if (!isChartAvailable()) return null;
  const options = baseOptions({ yLabel, suggestedMin, suggestedMax, stepSize });
  options.plugins.legend.display = legend;
  const ctx = canvas.getContext('2d');
  return new window.Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: datasets.map(({ label, data, accent, dashed }) => {
        const color = accentColor(accent);
        const height = canvas.parentElement?.clientHeight || 200;
        const gradient = ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, withAlpha(color, datasets.length > 1 ? 0.08 : 0.22));
        gradient.addColorStop(1, withAlpha(color, 0));
        return {
          label,
          data,
          borderColor: color,
          backgroundColor: gradient,
          fill: true,
          tension: 0.35,
          borderWidth: 2.5,
          borderDash: dashed ? [6, 4] : undefined,
          pointRadius: data.length > 30 ? 0 : 3,
          pointHoverRadius: 5,
          pointBackgroundColor: color,
          pointBorderWidth: 0,
          spanGaps: true,
        };
      }),
    },
    options,
  });
}

/** Столбчатый график. */
export function createBarChart(canvas, { labels, data, accent, label, yLabel, suggestedMax, stepSize }) {
  if (!isChartAvailable()) return null;
  const color = accentColor(accent);
  return new window.Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label, data, backgroundColor: withAlpha(color, 0.85), hoverBackgroundColor: color, borderRadius: 6, borderSkipped: false, maxBarThickness: 22 }],
    },
    options: baseOptions({ yLabel, suggestedMin: 0, suggestedMax, stepSize }),
  });
}
