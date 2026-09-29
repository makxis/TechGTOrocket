// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — конечный автомат полёта
 *
 * Общий принцип всех переходов: ни одно состояние не меняется по
 * единичному измерению. Каждое условие должно продержаться заданное
 * время — так требуют пп. 10, 12, 15 и 34 ТЗ.
 */

#include <math.h>

#include "flight_state.h"
#include "config.h"
#include "recovery.h"
#include "diagnostics.h"

namespace FlightManager {

static uint8_t  g_state = STATE_INIT;

static uint32_t g_launchMs = 0;      /* момент обнаружения старта       */
static float    g_maxAltitude = 0.0f;

/* Таймеры подтверждения. Ноль означает, что условие сейчас не выполнено
 * и отсчёт не идёт. */
static uint32_t g_launchHoldSince = 0;
static uint32_t g_accelLaunchMs = 0;  /* ускорение подтвердило, ждём барометр */
static uint32_t g_boostEndHoldSince = 0;
static uint32_t g_apogeeHoldSince = 0;
static uint32_t g_landingHoldSince = 0;

/* Для оценки вертикальной скорости. */
static float    g_prevAltitude = 0.0f;
static uint32_t g_prevAltMs = 0;
static float    g_vspeed = 0.0f;

static void enter(uint8_t newState)
{
    if (g_state == newState)
        return;

    g_state = newState;

    switch (newState) {
    case STATE_READY:    Diagnostics::logEvent(EV_READY);            break;
    case STATE_BOOST:    Diagnostics::logEvent(EV_LAUNCH_DETECTED);  break;
    case STATE_COAST:    Diagnostics::logEvent(EV_BOOST_END);        break;
    case STATE_APOGEE:   Diagnostics::logEvent(EV_APOGEE_CONFIRMED); break;
    case STATE_DESCENT:  Diagnostics::logEvent(EV_DESCENT);          break;
    case STATE_LANDED:   Diagnostics::logEvent(EV_LANDED);           break;
    default: break;
    }
}

/* Общий помощник для условий «признак держится не меньше N мс».
 * holdSince хранит момент, когда условие впервые стало истинным. */
static bool heldFor(bool condition, uint32_t &holdSince,
                    uint32_t nowMs, uint16_t requiredMs)
{
    if (!condition) {
        holdSince = 0;
        return false;
    }

    if (holdSince == 0) {
        holdSince = nowMs;
        /* Нулевой порог означает «достаточно одного попадания». */
        return requiredMs == 0;
    }

    return (uint32_t)(nowMs - holdSince) >= requiredMs;
}

static void updateVerticalSpeed(const SensorData &d, uint32_t nowMs)
{
    if (!d.baro_valid)
        return;

    if (g_prevAltMs == 0) {
        g_prevAltitude = d.altitude_m;
        g_prevAltMs = nowMs;
        return;
    }

    uint32_t dt = nowMs - g_prevAltMs;
    if (dt < 50)
        return;                 /* слишком короткий интервал, шум задавит */

    g_vspeed = (d.altitude_m - g_prevAltitude) * 1000.0f / (float)dt;
    g_prevAltitude = d.altitude_m;
    g_prevAltMs = nowMs;
}

/* ------------------------------------------------------------------ */

void init(void)
{
    g_state = STATE_INIT;
    g_launchMs = 0;
    g_maxAltitude = 0.0f;
    g_launchHoldSince = 0;
    g_accelLaunchMs = 0;
    g_boostEndHoldSince = 0;
    g_apogeeHoldSince = 0;
    g_landingHoldSince = 0;
    g_prevAltitude = 0.0f;
    g_prevAltMs = 0;
    g_vspeed = 0.0f;
}

void setReady(void)
{
    if (Diagnostics::hasCritical()) {
        enter(STATE_ERROR);
        return;
    }

    Recovery::arm();
    enter(STATE_READY);
}

uint32_t flightTimeMs(uint32_t nowMs)
{
    if (g_launchMs == 0)
        return 0;

    return nowMs - g_launchMs;
}

uint32_t launchTimeMs(void)
{
    return g_launchMs;
}

uint8_t state(void)
{
    return g_state;
}

float maxAltitude(void)
{
    return g_maxAltitude;
}

bool isFinished(void)
{
    return g_state == STATE_LANDED;
}

/* ------------------------------------------------------------------ */
/*  Обработчики состояний                                              */
/* ------------------------------------------------------------------ */

/* Пункт 10 ТЗ: старт не определяется по одному измерению. Нужны
 * превышение порога ускорения, удержание признака и, по возможности,
 * подтверждение ростом высоты. */
static void handleReady(const SensorData &d, uint32_t nowMs)
{
    if (!d.imu_valid)
        return;

    bool accelHigh = d.accel_mag >= LAUNCH_ACCEL_THRESHOLD_G;

    /* Ускорение продержалось нужное время — запоминаем и даём барометру
     * LAUNCH_BARO_WINDOW_MS на подтверждение. Ждать, что он подтвердит,
     * пока ускорение ещё высокое, нельзя: разгон слишком короткий. */
    if (heldFor(accelHigh, g_launchHoldSince, nowMs, LAUNCH_CONFIRM_TIME_MS) &&
        g_accelLaunchMs == 0)
        g_accelLaunchMs = nowMs;

    if (g_accelLaunchMs == 0)
        return;

    /* Дополнительное подтверждение по барометру. Если барометр не отвечает,
     * решение принимается по одному акселерометру: терять старт из-за
     * неисправного барометра нельзя, система спасения важнее. */
    if (LAUNCH_ALTITUDE_CONFIRM_M > 0.0f &&
        d.baro_valid && d.altitude_m < LAUNCH_ALTITUDE_CONFIRM_M) {
        /* Высота так и не выросла — это был удар или падение ракеты
         * со штанги, а не старт. */
        if ((uint32_t)(nowMs - g_accelLaunchMs) > LAUNCH_BARO_WINDOW_MS)
            g_accelLaunchMs = 0;
        return;
    }

    g_launchMs = nowMs;
    g_maxAltitude = d.baro_valid ? d.altitude_m : 0.0f;
    g_prevAltMs = 0;            /* начать отсчёт скорости заново */
    enter(STATE_BOOST);
}

/* Активный участок разгона закончился, когда ускорение вернулось
 * к значениям свободного полёта. */
static void handleBoost(const SensorData &d, uint32_t nowMs)
{
    bool thrustGone = d.imu_valid &&
                      (d.accel_mag < (LAUNCH_ACCEL_THRESHOLD_G * 0.5f));

    if (heldFor(thrustGone, g_boostEndHoldSince, nowMs, 80))
        enter(STATE_COAST);
}

/* Пункт 12 ТЗ: апогей нельзя определять по одному значению барометра.
 * Отслеживаем максимум, ловим падение относительно него и подтверждаем
 * его несколькими последовательными измерениями. */
static void handleCoast(const SensorData &d, uint32_t nowMs)
{
    if (!d.baro_valid)
        return;

    if (d.altitude_m > g_maxAltitude)
        g_maxAltitude = d.altitude_m;

    /* Слишком рано после старта апогей не рассматриваем вообще:
     * на активном участке барометр врёт из-за скоростного напора. */
    if (flightTimeMs(nowMs) < APOGEE_MIN_TIME_MS)
        return;

    bool dropping = (g_maxAltitude - d.altitude_m) >= APOGEE_DROP_THRESHOLD_M;

    /* Сверка с IMU по п. 12.5 ТЗ: при свободном падении модуль ускорения
     * заметно меньше единицы. Признак вспомогательный — если гироскоп с
     * акселерометром отказали, апогей всё равно определится по барометру. */
    if (heldFor(dropping, g_apogeeHoldSince, nowMs, APOGEE_CONFIRM_TIME_MS)) {
        enter(STATE_APOGEE);
        Recovery::deploy(false);
        enter(STATE_DESCENT);
    }
}

/* Пункт 15 ТЗ: посадка определяется по совокупности признаков. */
static void handleDescent(const SensorData &d, uint32_t nowMs)
{
    bool slowVertical = d.baro_valid &&
                        (fabs(g_vspeed) < LANDING_VSPEED_THRESHOLD);

    bool stillAccel = d.imu_valid &&
                      (fabs(d.accel_mag - 1.0f) < LANDING_ACCEL_TOLERANCE_G);

    /* Требуем оба признака сразу. Только по барометру посадку определять
     * нельзя: ракета, зависшая на дереве, тоже даст стабильное давление,
     * но это не посадка — а вот покой по акселерометру там тоже будет,
     * поэтому окончательную защиту даёт предел MAX_FLIGHT_TIME_MS. */
    bool landed = slowVertical && stillAccel;

    if (heldFor(landed, g_landingHoldSince, nowMs, LANDING_CONFIRM_TIME_MS))
        enter(STATE_LANDED);
}

/* ------------------------------------------------------------------ */

void update(const SensorData &d, uint32_t nowMs)
{
    updateVerticalSpeed(d, nowMs);

    if (d.baro_valid && d.altitude_m > g_maxAltitude &&
        g_state >= STATE_BOOST && g_state <= STATE_COAST) {
        g_maxAltitude = d.altitude_m;
    }

    switch (g_state) {
    case STATE_READY:   handleReady(d, nowMs);   break;
    case STATE_BOOST:   handleBoost(d, nowMs);   break;
    case STATE_COAST:   handleCoast(d, nowMs);   break;
    case STATE_APOGEE:
    case STATE_DESCENT:
    case STATE_RECOVERY: handleDescent(d, nowMs); break;
    default: break;
    }

    /* --- страховки, работающие независимо от того, что решил автомат --- */

    if (g_launchMs == 0)
        return;

    uint32_t ft = flightTimeMs(nowMs);

    /* Пункт 14 ТЗ: резервное раскрытие на случай, если основной алгоритм
     * апогей не определил. Не зависит ни от MicroSD, ни от радиоканала. */
    if (!Recovery::isDeployed() && ft >= BACKUP_DEPLOY_TIME_MS) {
        Recovery::deploy(true);
        enter(STATE_DESCENT);
    }

    /* Аварийный предел времени полёта: не зависать в DESCENT навсегда,
     * если ракета села так, что признаки посадки не сошлись. */
    if (g_state != STATE_LANDED && ft >= MAX_FLIGHT_TIME_MS) {
        Diagnostics::raise(ERROR_TIMING);
        enter(STATE_LANDED);
    }
}

} /* namespace FlightManager */
