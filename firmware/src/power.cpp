#include <Arduino.h>
#include "power.h"
#include "config.h"
#include "pins.h"
#include "types.h"
#include "diagnostics.h"

namespace Power {

static float    g_volts = 0.0f;
static uint32_t g_lastMs = 0;
static uint8_t  g_lowCount = 0;

void init(void)
{
#if HAS_VBAT_SENSE
    pinMode(PIN_VBAT, INPUT);
#endif
}

float volts(void) { return g_volts; }

void update(uint8_t flightState, uint32_t nowMs)
{
#if HAS_VBAT_SENSE
    if (g_lastMs != 0 && (uint32_t)(nowMs - g_lastMs) < VBAT_PERIOD_MS)
        return;
    g_lastMs = nowMs | 1;   /* ноль означает «ещё не мерили» */

    /* Четыре отсчёта гасят шум, один отсчёт занимает около 110 мкс. */
    uint16_t sum = 0;
    for (uint8_t i = 0; i < 4; i++)
        sum += analogRead(PIN_VBAT);
    g_volts = (sum / 4.0f) / 1023.0f * VBAT_ADC_REF_V * VBAT_DIVIDER;

    /* Флаг ведётся только до старта. В полёте бросок тока привода
     * просаживает батарею, и ложная тревога никому не поможет. */
    if (flightState != STATE_READY && flightState != STATE_INIT)
        return;

    if (g_volts < VBAT_LOW_V) {
        if (g_lowCount < VBAT_LOW_SAMPLES)
            g_lowCount++;
        if (g_lowCount >= VBAT_LOW_SAMPLES)
            Diagnostics::raise(ERROR_POWER);
    } else if (g_volts > VBAT_LOW_V + VBAT_HYST_V) {
        g_lowCount = 0;
        Diagnostics::clear(ERROR_POWER);
    }
#else
    (void)flightState; (void)nowMs;
#endif
}

} /* namespace Power */
