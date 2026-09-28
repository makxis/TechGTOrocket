/*
 * ВРО-1 / RocketBoard — диагностика, события и индикация
 */

#include "diagnostics.h"
#include "config.h"
#include "pins.h"
#include "sd_logger.h"
#include "radio.h"
#include "dbg.h"

namespace Diagnostics {

static uint16_t g_flags = 0;

/* Состояние мигалки. Индикация неблокирующая: ни одного delay(),
 * иначе поехали бы тайминги полётного цикла (п. 31 ТЗ). */
static uint32_t g_lastBlinkMs = 0;
static bool     g_ledOn = false;

void init(void)
{
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, LOW);

#if HAS_BUZZER
    pinMode(PIN_BUZZER, OUTPUT);
    digitalWrite(PIN_BUZZER, LOW);
#endif

    g_flags = 0;
    g_lastBlinkMs = 0;
    g_ledOn = false;
}

void raise(uint16_t flag)
{
    if (g_flags & flag)
        return;                 /* уже взведён, не шумим повторно */

    g_flags |= flag;

    if (flag & ERROR_CRITICAL_MASK)
        logEvent(EV_CRITICAL_ERROR);
}

void clear(uint16_t flag)
{
    g_flags &= (uint16_t)~flag;
}

bool isSet(uint16_t flag)
{
    return (g_flags & flag) != 0;
}

uint16_t flags(void)
{
    return g_flags;
}

bool hasCritical(void)
{
    return (g_flags & ERROR_CRITICAL_MASK) != 0;
}

void logEvent(uint8_t ev)
{
    uint32_t t = millis();

    SdLogger::writeEvent(t, ev);
    Radio::sendEvent(t, ev);

#if DEBUG_SERIAL_ENABLED
    /* Событие в журнал на карте и в радиоканал уже ушло выше. Печать
     * по USB — дело десятое, и ради неё останавливаться нельзя. */
    if (dbgHasRoom(40)) {
    Serial.print(F("[EV "));
    Serial.print(t);
    Serial.print(F("] "));
    Serial.println(eventName(ev));
    }
#endif
}

/* Период мигания подобран так, чтобы состояние читалось с земли
 * без секундомера. */
static uint16_t blinkPeriodMs(uint8_t flightState)
{
    if (g_flags & ERROR_CRITICAL_MASK)
        return 100;             /* частое мигание — критическая ошибка */

    switch (flightState) {
    case STATE_INIT:   return 500;
    case STATE_READY:  return 1000;   /* редкие вспышки — ждём старта */
    case STATE_LANDED: return 200;    /* маячок для поиска после посадки */
    default:           return 250;    /* полёт */
    }
}

void updateIndicators(uint8_t flightState, uint32_t nowMs)
{
    uint16_t period = blinkPeriodMs(flightState);

    if ((uint32_t)(nowMs - g_lastBlinkMs) < period)
        return;

    g_lastBlinkMs = nowMs;
    g_ledOn = !g_ledOn;
    digitalWrite(PIN_LED, g_ledOn ? HIGH : LOW);

#if HAS_BUZZER
    /* Пищим только после посадки — чтобы ракету было легче найти
     * в траве (п. 29 ТЗ). В полёте звук бесполезен. */
    if (flightState == STATE_LANDED)
        digitalWrite(PIN_BUZZER, g_ledOn ? HIGH : LOW);
    else
        digitalWrite(PIN_BUZZER, LOW);
#endif
}

} /* namespace Diagnostics */
