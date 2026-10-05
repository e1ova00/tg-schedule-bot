/**
 * Router — переключение разделов SPA по hash (#/today, #/training …) без перезагрузки.
 * Текущий раздел уничтожается (destroy) перед показом нового: все его виджеты
 * снимают слушатели, графики и запросы.
 */
export class Router {
  #routes;
  #outlet;
  #fallback;
  #current = null;
  #currentPath = null;
  #onChange;
  #handler = () => this.#resolve();

  constructor({ routes, outlet, fallback, onChange = () => {} }) {
    this.#routes = routes;
    this.#outlet = outlet;
    this.#fallback = fallback;
    this.#onChange = onChange;
  }

  get currentPath() {
    return this.#currentPath;
  }

  start() {
    window.addEventListener('hashchange', this.#handler);
    this.#resolve();
  }

  navigate(path) {
    if (location.hash === `#/${path}`) this.reload();
    else location.hash = `/${path}`;
  }

  /** Пересоздать текущий раздел (например, после загрузки демо-данных). */
  reload({ focus = false } = {}) {
    this.#resolve({ force: true, focus });
  }

  #parse() {
    const path = location.hash.replace(/^#\/?/, '').split('?')[0];
    return path in this.#routes ? path : this.#fallback;
  }

  #resolve({ force = false, focus = true } = {}) {
    const path = this.#parse();
    if (!force && path === this.#currentPath && this.#current) return;
    const isFirst = this.#currentPath === null;
    this.#current?.destroy();
    this.#outlet.replaceChildren();
    this.#currentPath = path;
    this.#current = this.#routes[path]();
    this.#current.mount(this.#outlet);
    this.#onChange(path, this.#current);
    // Для скринридеров и клавиатуры — фокус на заголовок нового раздела.
    if (!isFirst && focus) this.#outlet.querySelector('h1')?.focus({ preventScroll: true });
    if (!isFirst && !force) window.scrollTo({ top: 0 });
  }

  stop() {
    window.removeEventListener('hashchange', this.#handler);
    this.#current?.destroy();
    this.#current = null;
    this.#currentPath = null;
  }
}
