// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — формирование телеметрии
 *
 * Пункт 16 ТЗ: все подсистемы получают данные из единой структуры.
 * Пункт 17 ТЗ: временные метки и сквозные номера пакетов.
 */

#ifndef TELEMETRY_H
#define TELEMETRY_H

#include "types.h"

namespace Telemetry {

void init(void);

/* Собрать очередную запись. Номер пакета увеличивается на единицу
 * при каждом вызове — по нему наземная программа находит пропуски
 * радиопакетов (п. 17 ТЗ). */
void build(TelemetryRecord &rec, const SensorData &d, uint32_t nowMs);

/* Текущий счётчик пакетов. */
uint16_t packetId(void);

} /* namespace Telemetry */

#endif /* TELEMETRY_H */
