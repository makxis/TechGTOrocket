/*
 * ВРО-1 / RocketBoard — диагностика, события и индикация
 *
 * Пункт 28 ТЗ: диагностические флаги; некритическая ошибка регистрируется,
 * но систему не останавливает.
 * Пункт 33 ТЗ: события с временными метками.
 * Пункт 29 ТЗ: светодиод и пьезодинамик.
 */

#ifndef DIAGNOSTICS_H
#define DIAGNOSTICS_H

#include "types.h"

namespace Diagnostics {

void     init(void);

/* Взвести диагностический флаг. Повторный вызов с тем же флагом
 * ничего не меняет и не засоряет журнал. */
void     raise(uint16_t flag);
void     clear(uint16_t flag);
bool     isSet(uint16_t flag);
uint16_t flags(void);

/* Есть ли ошибка, при которой полёт невозможен. */
bool     hasCritical(void);

/* Зарегистрировать событие (п. 33 ТЗ). Время метки проставляется само.
 * Событие уходит в журнал на карте, в радиоканал и в отладочный вывод. */
void     logEvent(uint8_t ev);

/* Индикация. Вызывается из главного цикла, внутри ничего не блокирует. */
void     updateIndicators(uint8_t flightState, uint32_t nowMs);

} /* namespace Diagnostics */

#endif /* DIAGNOSTICS_H */
