/**
 * ThemeManager — светлая / тёмная / системная тема.
 * Выбор хранится в preferences; при «системной» следим за prefers-color-scheme.
 * Событие 'change' позволяет графикам перекрасить себя.
 */
export const THEMES = Object.freeze({ light: 'Светлая', dark: 'Тёмная', system: 'Системная' });

export class ThemeManager extends EventTarget {
  #storage;
  #media = window.matchMedia('(prefers-color-scheme: dark)');
  #onMedia = () => {
    if (this.mode === 'system') this.#apply();
  };

  constructor(storage) {
    super();
    this.#storage = storage;
    this.#media.addEventListener('change', this.#onMedia);
    this.#apply();
  }

  get mode() {
    const mode = this.#storage.get('preferences')?.theme;
    return mode in THEMES ? mode : 'light';
  }

  get resolved() {
    if (this.mode === 'system') return this.#media.matches ? 'dark' : 'light';
    return this.mode;
  }

  set(mode) {
    if (!(mode in THEMES)) return;
    this.#storage.update('preferences', (p) => ({ ...(p ?? {}), theme: mode }));
    this.#apply();
  }

  /** Повторно применить (например, после сброса данных). */
  sync() {
    this.#apply();
  }

  #apply() {
    const root = document.documentElement;
    root.dataset.theme = this.resolved;
    root.dataset.themeMode = this.mode;
    root.style.colorScheme = this.resolved;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', this.resolved === 'dark' ? '#000000' : '#f2f2f7');
    this.dispatchEvent(new CustomEvent('change', { detail: { mode: this.mode, resolved: this.resolved } }));
  }
}
