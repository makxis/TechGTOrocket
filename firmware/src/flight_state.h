/*
 * ВРО-1 / RocketBoard — конечный автомат полёта
 *
 * Пункт 11 ТЗ: набор состояний.
 * Пункт 10 ТЗ: подтверждённое обнаружение старта.
 * Пункт 12 ТЗ: обнаружение апогея, устойчивое к шуму.
 * Пункт 14 ТЗ: резервное раскрытие.
 * Пункт 15 ТЗ: обнаружение посадки по совокупности признаков.
 *
 * Модуль не зависит ни от MicroSD, ни от радиоканала — ключевое
 * требование п. 5 ТЗ.
 */

#ifndef FLIGHT_STATE_H
#define FLIGHT_STATE_H

#include "types.h"

namespace FlightManager {

void init(void);

/* Перевод в READY после успешной калибровки. */
void setReady(void);

/* Шаг автомата. Вызывается на FLIGHT_RATE_HZ. */
void update(const SensorData &d, uint32_t nowMs);

uint8_t  state(void);
float    maxAltitude(void);

/* Время от старта, мс. Ноль, пока старт не обнаружен (п. 17 ТЗ). */
uint32_t flightTimeMs(uint32_t nowMs);

/* Момент обнаружения старта от включения, мс. Ноль — старта не было. */
uint32_t launchTimeMs(void);

/* Полёт завершён: можно закрывать журнал на карте. */
bool     isFinished(void);

} /* namespace FlightManager */

#endif /* FLIGHT_STATE_H */
