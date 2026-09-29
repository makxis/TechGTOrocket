#include <Arduino.h>
#include "power.h"
#include "config.h"
#include "pins.h"
#include "types.h"
#include "diagnostics.h"

namespace Power {

static uint16_t g_centi = 0;     /* сотые доли вольта */
static uint32_t g_lastMs = 0;
static uint8_t  g_lowCount = 0;

void init(void)
{
#if HAS_VBAT_SENSE
    pinMode(PIN_VBAT, INPUT);
#endif
}

float volts(void) { return g_centi / 100.0f; }
uint16_t centivolts(void) { return g_centi; }

/* Границы и множитель считаются компилятором, во время работы только целые числа. */
#define VBAT_CENTI_X4     ((uint32_t)(VBAT_ADC_REF_V * VBAT_DIVIDER * 25.0f + 0.5f))
#define VBAT_LOW_CENTI    ((uint16_t)(VBAT_LOW_V * 100.0f + 0.5f))
#define VBAT_OK_CENTI     ((uint16_t)((VBAT_LOW_V + VBAT_HYST_V) * 100.0f + 0.5f))

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
    /* Среднее из четырёх, делитель и опора АЦП: сумма * 375 / 1023 сотых вольта. */
    g_centi = (uint16_t)(((uint32_t)sum * VBAT_CENTI_X4) / 1023UL);

    /* Флаг ведётся только до старта. В полёте бросок тока привода
     * просаживает батарею, и ложная тревога никому не поможет. */
    if (flightState != STATE_READY && flightState != STATE_INIT)
        return;

    if (g_centi < VBAT_LOW_CENTI) {
        if (g_lowCount < VBAT_LOW_SAMPLES)
            g_lowCount++;
        if (g_lowCount >= VBAT_LOW_SAMPLES)
            Diagnostics::raise(ERROR_POWER);
    } else if (g_centi > VBAT_OK_CENTI) {
        g_lowCount = 0;
        Diagnostics::clear(ERROR_POWER);
    }
#else
    (void)flightState; (void)nowMs;
#endif
}

} /* namespace Power */
