/*
 * ВРО-1 / RocketBoard — текстовые имена состояний и событий
 *
 * Все строки размещены во флеше. Держать их в ОЗУ при 2560 байтах
 * недопустимо — п. 30 ТЗ.
 */

#include "types.h"

const __FlashStringHelper *flightStateName(uint8_t state)
{
    switch (state) {
    case STATE_INIT:     return F("INIT");
    case STATE_READY:    return F("READY");
    case STATE_BOOST:    return F("BOOST");
    case STATE_COAST:    return F("COAST");
    case STATE_APOGEE:   return F("APOGEE");
    case STATE_DESCENT:  return F("DESCENT");
    case STATE_RECOVERY: return F("RECOVERY");
    case STATE_LANDED:   return F("LANDED");
    case STATE_ERROR:    return F("ERROR");
    default:             return F("?");
    }
}

const __FlashStringHelper *recoveryStateName(uint8_t state)
{
    switch (state) {
    case RECOVERY_SAFE:     return F("SAFE");
    case RECOVERY_ARMED:    return F("ARMED");
    case RECOVERY_DEPLOYED: return F("DEPLOYED");
    case RECOVERY_ERROR:    return F("ERROR");
    default:                return F("?");
    }
}

const __FlashStringHelper *eventName(uint8_t ev)
{
    switch (ev) {
    case EV_POWER_ON:         return F("POWER_ON");
    case EV_SENSORS_OK:       return F("SENSORS_OK");
    case EV_SD_OK:            return F("SD_OK");
    case EV_SD_FAIL:          return F("SD_FAIL");
    case EV_RADIO_OK:         return F("RADIO_OK");
    case EV_RADIO_FAIL:       return F("RADIO_FAIL");
    case EV_READY:            return F("READY");
    case EV_LAUNCH_DETECTED:  return F("LAUNCH_DETECTED");
    case EV_BOOST_END:        return F("BOOST_END");
    case EV_APOGEE_CONFIRMED: return F("APOGEE_CONFIRMED");
    case EV_RECOVERY_DEPLOY:  return F("RECOVERY_DEPLOY");
    case EV_DESCENT:          return F("DESCENT");
    case EV_LANDED:           return F("LANDED");
    case EV_CRITICAL_ERROR:   return F("CRITICAL_ERROR");
    case EV_BACKUP_DEPLOY:    return F("BACKUP_DEPLOY");
    default:                  return F("?");
    }
}
