/*
 * ВРО-1 / RocketBoard — общие типы
 *
 * Состав телеметрии по п. 16 ТЗ, состояния по п. 11, флаги по п. 28,
 * события по п. 33.
 *
 * Состав записи оптимизирован под ATmega32U4 (п. 16 и п. 30 ТЗ):
 * разрядность полей взята минимально достаточная, чтобы структура
 * умещалась в скромные 2560 байт ОЗУ вместе со всем остальным.
 */

#ifndef TYPES_H
#define TYPES_H

#include <Arduino.h>
#include <stdint.h>

/* ------------------------------------------------------------------ */
/*  Состояния автомата полёта (п. 11 ТЗ)                               */
/* ------------------------------------------------------------------ */

enum FlightState : uint8_t {
    STATE_INIT = 0,
    STATE_READY,
    STATE_BOOST,
    STATE_COAST,
    STATE_APOGEE,
    STATE_DESCENT,
    STATE_RECOVERY,
    STATE_LANDED,
    STATE_ERROR
};

/* ------------------------------------------------------------------ */
/*  Состояния системы спасения (п. 4.2 ТЗ)                             */
/* ------------------------------------------------------------------ */

enum RecoveryState : uint8_t {
    RECOVERY_SAFE = 0,
    RECOVERY_ARMED,
    RECOVERY_DEPLOYED,
    RECOVERY_ERROR
};

/* ------------------------------------------------------------------ */
/*  Диагностические флаги (п. 28 ТЗ)                                   */
/* ------------------------------------------------------------------ */

/* Битовая маска: несколько ошибок могут быть взведены одновременно. */
#define ERROR_SENSOR_INIT   0x0001
#define ERROR_BARO          0x0002
#define ERROR_IMU           0x0004
#define ERROR_SD_INIT       0x0008
#define ERROR_SD_WRITE      0x0010
#define ERROR_RADIO         0x0020
#define ERROR_SERVO         0x0040
#define ERROR_TIMING        0x0080

/* Сверх перечня ТЗ: разброс при калибровке давления, требуемый п. 9. */
#define ERROR_BARO_CALIB    0x0100

/* Ошибки, при которых полёт невозможен. Остальные регистрируются,
 * но работу не останавливают (п. 28 ТЗ). */
#define ERROR_CRITICAL_MASK (ERROR_SENSOR_INIT | ERROR_BARO | ERROR_IMU)

/* ------------------------------------------------------------------ */
/*  Состояние подсистем                                                */
/* ------------------------------------------------------------------ */

enum SubsystemStatus : uint8_t {
    SUBSYS_ABSENT = 0,   /* модуль не обнаружен — это штатно, п. 36 ТЗ */
    SUBSYS_OK,
    SUBSYS_FAILED
};

/* ------------------------------------------------------------------ */
/*  События полёта (п. 33 ТЗ)                                          */
/* ------------------------------------------------------------------ */

enum FlightEvent : uint8_t {
    EV_POWER_ON = 0,
    EV_SENSORS_OK,
    EV_SD_OK,
    EV_SD_FAIL,
    EV_RADIO_OK,
    EV_RADIO_FAIL,
    EV_READY,
    EV_LAUNCH_DETECTED,
    EV_BOOST_END,
    EV_APOGEE_CONFIRMED,
    EV_RECOVERY_DEPLOY,
    EV_DESCENT,
    EV_LANDED,
    EV_CRITICAL_ERROR,
    /* Сверх перечня ТЗ: раскрытие по резервному условию п. 14 надо
     * отличать от штатного, иначе разбор полёта будет неполным. */
    EV_BACKUP_DEPLOY,
    /* Ноль высоты поставлен после того, как ракету перестали трогать. */
    EV_GROUND_ZERO
};

/* ------------------------------------------------------------------ */
/*  Показания датчиков                                                 */
/* ------------------------------------------------------------------ */

struct SensorData {
    int32_t  pressure_pa;       /* давление, Па                        */
    float    temperature_c;     /* температура с барометра, °C         */
    float    altitude_m;        /* относительная высота, м             */
    float    accel_x;           /* ускорение, g                        */
    float    accel_y;
    float    accel_z;
    float    accel_mag;         /* модуль вектора ускорения, g         */
    float    gyro_x;            /* угловая скорость, °/с               */
    float    gyro_y;
    float    gyro_z;
    uint32_t baro_updated_ms;   /* когда барометр обновлялся в последний раз */
    bool     imu_valid;
    bool     baro_valid;
};

/* ------------------------------------------------------------------ */
/*  Запись телеметрии (п. 16 ТЗ)                                       */
/* ------------------------------------------------------------------ */

struct TelemetryRecord {
    uint32_t timestamp_ms;      /* время от включения (п. 17 ТЗ)       */
    uint32_t flight_time_ms;    /* время от старта   (п. 17 ТЗ)        */
    uint16_t packet_id;         /* сквозной номер    (п. 17 ТЗ)        */

    uint8_t  flight_state;      /* enum FlightState                    */
    uint8_t  recovery_state;    /* enum RecoveryState                  */

    int32_t  pressure_pa;
    float    temperature_c;
    float    altitude_m;
    float    max_altitude_m;

    float    accel_x;
    float    accel_y;
    float    accel_z;
    float    gyro_x;
    float    gyro_y;
    float    gyro_z;

    uint8_t  sd_status;         /* enum SubsystemStatus                */
    uint8_t  radio_status;      /* enum SubsystemStatus                */
    uint8_t  sensor_status;     /* enum SubsystemStatus                */
    uint16_t error_flags;
};

/* Текстовые имена состояний. Лежат во флеше, а не в ОЗУ (п. 30 ТЗ). */
const __FlashStringHelper *flightStateName(uint8_t state);
const __FlashStringHelper *recoveryStateName(uint8_t state);
const __FlashStringHelper *eventName(uint8_t ev);

#endif /* TYPES_H */
