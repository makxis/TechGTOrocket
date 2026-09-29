// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * Набор A по п. 27 ТЗ: датчики + сервопривод.
 *
 * Соответствует тому, что физически собрано на стенде на 21.09.2026:
 * ICM-20948 и BMP280 на шине I2C, больше ничего.
 *
 * Это набор для первого испытательного запуска по п. 26 ТЗ: проверить
 * питание, устойчивость электроники, определение полётных состояний и
 * работу системы спасения, не отвлекаясь на карту и радио.
 */

#ifndef HW_A_H
#define HW_A_H

#define HW_PROFILE_NAME     "A: датчики + сервопривод"

#ifndef HAS_SD
#define HAS_SD              0
#endif
#ifndef HAS_RADIO
#define HAS_RADIO           0
#endif
#ifndef HAS_SERVO
#define HAS_SERVO           1
#endif

#define IMU_DRIVER          IMU_DRIVER_ICM20948
#define BARO_DRIVER         BARO_DRIVER_BMP280

/* Места во флеше в этом наборе вдоволь, отладочный вывод оставляем. */
#ifndef DEBUG_SERIAL_ENABLED
#define DEBUG_SERIAL_ENABLED 1
#endif

#endif /* HW_A_H */
