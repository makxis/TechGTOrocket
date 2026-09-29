// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — интерфейс драйвера барометра
 *
 * Микросхемы взаимоисключающие: BMP280 либо BME280. Выбор задаётся
 * макросом BARO_DRIVER в файле набора из src/hw/.
 *
 * Влажность, которую умеет BME280, в интерфейс не вынесена: по ТЗ она
 * не нужна, а тянуть её ради одного варианта железа — значит городить
 * разный интерфейс под разные сборки.
 */

#ifndef BARO_H
#define BARO_H

#include <Arduino.h>

namespace BaroDriver {

/* Проверка идентификатора микросхемы, чтение калибровочных коэффициентов
 * и настройка режима измерений. */
bool init(void);

/* Давление в паскалях и температура в градусах Цельсия. */
bool read(int32_t *pressure_pa, float *temp_c);

const __FlashStringHelper *name(void);

} /* namespace BaroDriver */

#endif /* BARO_H */
