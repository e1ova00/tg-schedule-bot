/**
 * WidgetRegistry — фабрика виджетов по строковому типу.
 * Dashboard ничего не знает о конкретных классах: чтобы добавить новый вид
 * виджета, достаточно зарегистрировать класс (принцип открытости/закрытости).
 */
import { UIComponent } from './UIComponent.js';

export class WidgetRegistry {
  #types = new Map();

  register(WidgetClass) {
    if (!(WidgetClass.prototype instanceof UIComponent)) {
      throw new TypeError(`${WidgetClass.name} должен наследоваться от UIComponent`);
    }
    this.#types.set(WidgetClass.type, WidgetClass);
    return this;
  }

  has(type) {
    return this.#types.has(type);
  }

  create(type, config) {
    const WidgetClass = this.#types.get(type);
    if (!WidgetClass) throw new Error(`Неизвестный тип виджета: ${type}`);
    return new WidgetClass(config);
  }

  /** Каталог для диалога «Добавить виджет». */
  list() {
    return [...this.#types.values()].map((C) => ({ type: C.type, ...C.meta }));
  }
}
