/**
 * Минимальный HTTP-клиент: fetch + таймаут + внешний AbortSignal.
 * Ошибки приводятся к ApiError с понятным русским текстом,
 * отмена (AbortError) пробрасывается как есть — её не показывают пользователю.
 */

export class ApiError extends Error {
  constructor(kind, { status } = {}) {
    super(`ApiError: ${kind}${status ? ` ${status}` : ''}`);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
  }

  get userMessage() {
    switch (this.kind) {
      case 'timeout':
        return 'Сервис отвечает слишком долго. Попробуйте ещё раз чуть позже.';
      case 'http':
        if (this.status === 429) return 'Слишком много запросов к сервису. Подождите минуту и повторите.';
        if (this.status >= 500) return 'Сервис временно недоступен. Остальные функции приложения работают.';
        return `Сервис ответил ошибкой (${this.status}).`;
      case 'parse':
        return 'Сервис вернул данные в неожиданном формате.';
      default:
        return 'Не удалось подключиться к сервису. Проверьте интернет-соединение.';
    }
  }
}

export const isAbortError = (error) => error?.name === 'AbortError';

export async function fetchJSON(url, { signal, timeout = 15000 } = {}) {
  if (signal?.aborted) throw new DOMException('Запрос отменён', 'AbortError');
  const controller = new AbortController();
  let timedOut = false;
  const forwardAbort = () => controller.abort();
  signal?.addEventListener('abort', forwardAbort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeout);

  try {
    // Только «простые» заголовки — без CORS preflight.
    const response = await fetch(url, { signal: controller.signal, headers: { Accept: 'application/json' } });
    if (!response.ok) throw new ApiError('http', { status: response.status });
    try {
      return await response.json();
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError('parse');
    }
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Запрос отменён', 'AbortError');
    if (timedOut) throw new ApiError('timeout');
    if (error instanceof ApiError) throw error;
    throw new ApiError('network');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

/* --------- валидация недоверенных данных --------- */

export const safeString = (value, max = 300) =>
  typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().slice(0, max) : '';

export const safeNumber = (value, { min = 0, max = Infinity } = {}) => {
  const n = typeof value === 'string' ? Number(value) : value;
  return typeof n === 'number' && Number.isFinite(n) && n >= min && n <= max ? n : null;
};
