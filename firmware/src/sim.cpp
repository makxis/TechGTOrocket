/*
 * ВРО-1 / RocketBoard — воспроизведение тестового профиля полёта
 */

#include "sim.h"
#include "recovery.h"
#include "sensors.h"

namespace Sim {

#if HAS_SERVICE_MODE

/* --- параметры модели ---------------------------------------------- */

/* Активный участок. Водяная ракета отрабатывает коротко и резко:
 * несколько десятых секунды при ускорении в несколько g. */
static const uint16_t BOOST_MS         = 250;
static const float    BOOST_ACCEL_MS2  = 70.0f;   /* около 7 g          */
static const float    BOOST_ACCEL_G    = 8.0f;    /* что покажет датчик */

static const float    GRAVITY_MS2      = 9.81f;

/* Что показывает акселерометр в свободном полёте. Не строго ноль:
 * сопротивление воздуха и вращение дают остаток. */
static const float    FREEFALL_ACCEL_G = 0.15f;

/* Установившаяся скорость снижения под парашютом. */
static const float    CHUTE_SPEED_MS   = 5.0f;

/* Аварийный предел прогона, чтобы он не крутился вечно. */
static const uint32_t SIM_TIMEOUT_MS   = 20000UL;

/* Сколько паскалей приходится на метр высоты у земли. Для теста этой
 * точности достаточно: автомат полёта работает по высоте, а давление
 * нужно только чтобы телеметрия выглядела осмысленно. */
static const float    PA_PER_METER     = 11.8f;

/* --- состояние модели ---------------------------------------------- */

static bool     g_active = false;
static uint32_t g_startMs = 0;
static uint32_t g_lastMs = 0;

static float    g_alt = 0.0f;      /* высота, м      */
static float    g_vel = 0.0f;      /* скорость, м/с  */
static bool     g_landed = false;

void start(uint32_t nowMs)
{
    g_active = true;
    g_startMs = nowMs;
    g_lastMs = nowMs;
    g_alt = 0.0f;
    g_vel = 0.0f;
    g_landed = false;
}

void stop(void)
{
    g_active = false;
}

bool active(void)
{
    return g_active;
}

void fill(SensorData &d, uint32_t nowMs)
{
    if (!g_active)
        return;

    uint32_t elapsed = nowMs - g_startMs;
    float dt = (nowMs - g_lastMs) / 1000.0f;
    g_lastMs = nowMs;

    /* Первый вызов и слишком частые вызовы пропускаем: при dt около нуля
     * интегрирование ничего не даёт, а делений на малое число лучше
     * избегать. */
    if (dt <= 0.0f || dt > 0.5f)
        dt = 0.0f;

    float accelG;

    if (g_landed) {
        g_vel = 0.0f;
        g_alt = 0.0f;
        accelG = 1.0f;
    } else if (elapsed < BOOST_MS) {
        /* Активный участок. */
        g_vel += BOOST_ACCEL_MS2 * dt;
        g_alt += g_vel * dt;
        accelG = BOOST_ACCEL_G;
    } else if (!Recovery::isDeployed()) {
        /* Свободный полёт: подъём по инерции, апогей, затем падение.
         * Именно здесь автомат обязан поймать апогей. Если он этого не
         * сделает, модель продолжит разгоняться вниз, и сработает
         * резервное раскрытие по п. 14 ТЗ — что тоже надо проверить. */
        g_vel -= GRAVITY_MS2 * dt;
        g_alt += g_vel * dt;
        accelG = FREEFALL_ACCEL_G;
    } else {
        /* Парашют вышел: переходим на установившееся снижение. */
        g_vel = -CHUTE_SPEED_MS;
        g_alt += g_vel * dt;
        accelG = 1.0f;
    }

    if (g_alt <= 0.0f) {
        g_alt = 0.0f;
        g_vel = 0.0f;
        g_landed = true;
        accelG = 1.0f;
    }

    d.altitude_m   = g_alt;
#ifdef SIM_ALT_OFFSET_M
    /* Только для испытаний: сдвиг нуля высоты, как если бы давление
     * площадки уехало за время стоянки. В обычные сборки не попадает. */
    d.altitude_m  += SIM_ALT_OFFSET_M;
#endif
    d.accel_mag    = accelG;

    /* Раскладываем модуль по осям так, будто продольная ось — Z.
     * Настоящая ориентация осей по п. 47.4 ТЗ пока не подтверждена,
     * но для проверки автомата это несущественно: он работает с модулем. */
    d.accel_x      = 0.0f;
    d.accel_y      = 0.0f;
    d.accel_z      = accelG;

    d.gyro_x       = 0.0f;
    d.gyro_y       = 0.0f;
    d.gyro_z       = 0.0f;

    d.pressure_pa  = Sensors::groundPressure() - (int32_t)(g_alt * PA_PER_METER);
    d.temperature_c = 20.0f;

    d.baro_valid   = true;
    d.imu_valid    = true;
    d.baro_updated_ms = nowMs;

    if (elapsed > SIM_TIMEOUT_MS)
        g_active = false;
}

#else  /* HAS_SERVICE_MODE == 0 */

/* В полётной сборке модели нет вовсе: подменять датчики в воздухе
 * нечему и незачем. */

void start(uint32_t)            { }
void stop(void)                 { }
bool active(void)               { return false; }
void fill(SensorData &, uint32_t) { }

#endif

} /* namespace Sim */
