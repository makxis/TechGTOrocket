/*
 * Набор D по п. 27 ТЗ: датчики + сервопривод + MicroSD + HC-12.
 *
 * Полный комплект. Самый тесный по памяти набор: SD и радио вместе
 * оставляют мало запаса, поэтому отладочный вывод выключен, а любое
 * расширение кода надо проверять на вместимость.
 *
 * Если сборка перестанет помещаться, смотреть docs/MEMORY.md — там
 * разобрано, кто сколько занимает и что можно ужать.
 */

#ifndef HW_D_H
#define HW_D_H

#define HW_PROFILE_NAME     "D: полный комплект"

#ifndef HAS_SD
#define HAS_SD              1
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
#define DEBUG_SERIAL_ENABLED 0
#endif

#endif /* HW_D_H */
