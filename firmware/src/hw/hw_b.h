/*
 * Набор B по п. 27 ТЗ: датчики + сервопривод + MicroSD.
 *
 * Полётный журнал на карте без радиоканала. Основной набор для запусков,
 * когда телеметрия разбирается после полёта, а не наблюдается в реальном
 * времени.
 *
 * ВНИМАНИЕ: библиотека SD забирает 10 920 байт флеша из 28 672. Отладочный
 * вывод в этом наборе выключен — с ним сборка в плату не помещается.
 */

#ifndef HW_B_H
#define HW_B_H

#define HW_PROFILE_NAME     "B: датчики + сервопривод + MicroSD"

#ifndef HAS_SD
#define HAS_SD              1
#endif
#ifndef HAS_RADIO
#define HAS_RADIO           0
#endif
#ifndef HAS_SERVO
#define HAS_SERVO           1
#endif

#define IMU_DRIVER          IMU_DRIVER_ICM20948
#define BARO_DRIVER         BARO_DRIVER_BMP280

/* Выключен вынужденно, ради места во флеше. Это не противоречит п. 39 ТЗ:
 * в релизной версии отладочный вывод и не нужен. */
#ifndef DEBUG_SERIAL_ENABLED
#define DEBUG_SERIAL_ENABLED 0
#endif

#endif /* HW_B_H */
