/*
 * ВРО-1 / RocketBoard — работа с датчиками
 *
 * Этот файл не знает, какие микросхемы стоят на плате. Всё чип-зависимое
 * вынесено в src/drivers/, а выбор драйвера делается набором железа из
 * src/hw/. Здесь остаётся то, что одинаково для любого железа: калибровка,
 * проверка диапазонов, фильтрация и пересчёт в физические величины.
 *
 * Пункт 9 ТЗ: калибровка базового давления площадки.
 * Пункт 34 ТЗ: проверка диапазонов и фильтрация выбросов.
 */

#include <Wire.h>
#include <math.h>

#include "sensors.h"
#include "config.h"
#include "pins.h"
#include "diagnostics.h"
#include "drivers/imu.h"
#include "drivers/baro.h"
#include "dbg.h"

namespace Sensors {

static float   g_gyroBiasX;
static float   g_gyroBiasY;
static float   g_gyroBiasZ;

static int32_t g_groundPressure;  /* Па, п. 9 ТЗ */
static float   g_filteredAlt;
static bool    g_altInitialised;

/* Относительная высота над площадкой по барометрической формуле.
 * Основная величина в полёте — именно относительная высота, п. 9 ТЗ. */
static float altitudeFromPressure(int32_t p)
{
    if (g_groundPressure <= 0 || p <= 0)
        return 0.0f;

    float ratio = (float)p / (float)g_groundPressure;
    return 44330.0f * (1.0f - pow(ratio, 0.1902949f));
}

bool init(void)
{
    Wire.begin();
    /* 400 кГц: на 100 кГц чтение 12 байт с IMU заняло бы около
     * миллисекунды, а при 50 Гц полётного цикла такое расточительство
     * недопустимо (п. 31 ТЗ). */
    Wire.setClock(400000UL);

    /* ОБЯЗАТЕЛЬНО. В библиотеке Wire таймаут по умолчанию выключен
     * (twi_timeout_us = 0), и при залипании шины она ждёт вечно. Для
     * ракеты это означает: одна помеха по I2C — и бортовой компьютер
     * встал, а парашют не вышел. Пункт 31 ТЗ требует неблокирующего
     * цикла, пункт 20 прямо запрещает бесконечное ожидание.
     *
     * Так и поймали зависание на стенде 21.09.2026: после подключения
     * сервопривода плата переставала отвечать, при этом USB был исправен
     * и сбросов питания не было.
     *
     * 3 мс с запасом перекрывают самый длинный обмен на 400 кГц (чтение
     * 12 байт занимает около 0,3 мс). Второй параметр включает сброс
     * шины по таймауту, иначе она так и останется залипшей. */
    Wire.setWireTimeout(3000, true);

    g_gyroBiasX = g_gyroBiasY = g_gyroBiasZ = 0.0f;
    g_groundPressure = 0;
    g_filteredAlt = 0.0f;
    g_altInitialised = false;

    bool imuOk  = ImuDriver::init();
    bool baroOk = BaroDriver::init();

#if DEBUG_SERIAL_ENABLED
    if (dbgHasRoom(60)) {
    Serial.print(F("IMU: "));
    Serial.print(ImuDriver::name());
    Serial.println(imuOk ? F(" ok") : F(" ОТКАЗ"));
    Serial.print(F("Барометр: "));
    Serial.print(BaroDriver::name());
    Serial.println(baroOk ? F(" ok") : F(" ОТКАЗ"));
    }
#endif

    if (!imuOk)
        Diagnostics::raise(ERROR_IMU);

    if (!baroOk)
        Diagnostics::raise(ERROR_BARO);

    if (!imuOk || !baroOk) {
        Diagnostics::raise(ERROR_SENSOR_INIT);
        return false;
    }

    Diagnostics::logEvent(EV_SENSORS_OK);
    return true;
}

void calibrateGround(void)
{
    /* --- ноль гироскопа ---
     * На стенде замерено смещение до -0,65 °/с. Без его снятия оценка
     * вращения уедет, поэтому калибровка обязательна. */
    if (!Diagnostics::isSet(ERROR_IMU)) {
        float sx = 0.0f, sy = 0.0f, sz = 0.0f;
        uint8_t taken = 0;

        for (uint8_t i = 0; i < GYRO_CALIB_SAMPLES; i++) {
            int16_t a[3], g[3];

            if (ImuDriver::readRaw(a, g)) {
                sx += g[0];
                sy += g[1];
                sz += g[2];
                taken++;
            }
            delay(5);
        }

        if (taken > 0) {
            float scale = ImuDriver::gyroScale();
            g_gyroBiasX = (sx / taken) / scale;
            g_gyroBiasY = (sy / taken) / scale;
            g_gyroBiasZ = (sz / taken) / scale;
        }
    }

    /* --- базовое давление площадки (п. 9 ТЗ) --- */
    if (Diagnostics::isSet(ERROR_BARO))
        return;

    int32_t sum = 0;
    int32_t minP = PRESSURE_VALID_MAX_PA;
    int32_t maxP = PRESSURE_VALID_MIN_PA;
    uint8_t taken = 0;

    for (uint8_t i = 0; i < BARO_CALIB_SAMPLES; i++) {
        int32_t p;
        float t;

        if (BaroDriver::read(&p, &t)) {
            /* Явно ошибочные отсчёты калибровка игнорирует — п. 9 ТЗ. */
            if (p >= PRESSURE_VALID_MIN_PA && p <= PRESSURE_VALID_MAX_PA) {
                sum += p;
                if (p < minP) minP = p;
                if (p > maxP) maxP = p;
                taken++;
            }
        }
        delay(20);
    }

    if (taken < (BARO_CALIB_SAMPLES / 2)) {
        /* Годных измерений слишком мало — барометру верить нельзя. */
        Diagnostics::raise(ERROR_BARO);
        return;
    }

    g_groundPressure = sum / taken;

    /* При чрезмерном разбросе выставляем диагностический флаг, но работу
     * не останавливаем — так требует п. 9 ТЗ. */
    if ((maxP - minP) > BARO_CALIB_MAX_SPREAD_PA)
        Diagnostics::raise(ERROR_BARO_CALIB);

    g_filteredAlt = 0.0f;
    g_altInitialised = true;
}

void readImu(SensorData &d)
{
    int16_t a[3], g[3];

    if (!ImuDriver::readRaw(a, g)) {
        d.imu_valid = false;
        Diagnostics::raise(ERROR_IMU);
        return;
    }

    float as = ImuDriver::accelScale();
    float gs = ImuDriver::gyroScale();

    d.accel_x = a[0] / as;
    d.accel_y = a[1] / as;
    d.accel_z = a[2] / as;

    d.gyro_x = g[0] / gs - g_gyroBiasX;
    d.gyro_y = g[1] / gs - g_gyroBiasY;
    d.gyro_z = g[2] / gs - g_gyroBiasZ;

    d.accel_mag = sqrt(d.accel_x * d.accel_x +
                       d.accel_y * d.accel_y +
                       d.accel_z * d.accel_z);

    d.imu_valid = true;
}

void readBaro(SensorData &d, uint32_t nowMs)
{
    int32_t p;
    float t;

    if (!BaroDriver::read(&p, &t)) {
        d.baro_valid = false;
        Diagnostics::raise(ERROR_BARO);
        return;
    }

    /* Проверка диапазона по п. 34 ТЗ: одиночное плохое измерение
     * отбрасывается целиком, предыдущее значение высоты сохраняется. */
    if (p < PRESSURE_VALID_MIN_PA || p > PRESSURE_VALID_MAX_PA) {
        d.baro_valid = false;
        return;
    }

    d.pressure_pa = p;
    d.temperature_c = t;

    float raw = altitudeFromPressure(p);

    if (fabs(raw) > ALTITUDE_VALID_MAX_M) {
        d.baro_valid = false;
        return;
    }

    /* Экспоненциальное сглаживание. Коэффициент вынесен в config.h:
     * слишком сильный фильтр задержит обнаружение апогея, слишком
     * слабый пропустит шум в автомат полёта (п. 34 ТЗ). */
    if (!g_altInitialised) {
        g_filteredAlt = raw;
        g_altInitialised = true;
    } else {
        g_filteredAlt += ALTITUDE_FILTER_ALPHA * (raw - g_filteredAlt);
    }

    d.altitude_m = g_filteredAlt;
    d.baro_updated_ms = nowMs;
    d.baro_valid = true;
}

int32_t groundPressure(void)
{
    return g_groundPressure;
}

} /* namespace Sensors */
