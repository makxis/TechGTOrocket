// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * Набор C по п. 27 ТЗ: датчики + сервопривод + HC-12.
 *
 * Телеметрия в реальном времени на наземную станцию, локальной записи нет.
 * Набор для отработки радиотракта и для запусков, где важно видеть полёт
 * с земли.
 *
 * Радиомодуль по п. 4.3 ТЗ один, различаются только форм-факторы, поэтому
 * отдельного выбора драйвера радио не предусмотрено.
 */

#ifndef HW_C_H
#define HW_C_H

#define HW_PROFILE_NAME     "C: датчики + сервопривод + HC-12"

#ifndef HAS_SD
#define HAS_SD              0
#endif
#ifndef HAS_RADIO
#define HAS_RADIO           1
#endif
#ifndef HAS_SERVO
#define HAS_SERVO           1
#endif

#define IMU_DRIVER          IMU_DRIVER_ICM20948
#define BARO_DRIVER         BARO_DRIVER_BMP280

#ifndef DEBUG_SERIAL_ENABLED
#define DEBUG_SERIAL_ENABLED 1
#endif

#endif /* HW_C_H */
