/*
 * ВРО-1 / RocketBoard — наземный сервисный режим
 */

#include "service.h"
#include "recovery.h"
#include "sensors.h"
#include "diagnostics.h"
#include "drivers/imu.h"
#include "drivers/baro.h"
#include "sim.h"
#include "radio.h"
#include "dbg.h"

namespace Service {

#if HAS_SERVICE_MODE

/* Печать строки справки с проверкой места. Справка целиком в буфер USB
 * не помещается, поэтому строки выдаются по одной и каждая проверяется
 * отдельно. Недопечатанная справка — мелочь; заблокированный полётный
 * цикл — нет. */
static void helpLine(const __FlashStringHelper *s)
{
    if (dbgHasRoom(56))
        Serial.println(s);
}

static void printHelp(void)
{
    helpLine(F("--- сервисный режим (п. 40 ТЗ) ---"));
    helpLine(F("  s  привод в SAFE"));
    helpLine(F("  d  привод в DEPLOYED"));
    helpLine(F("  t  цикл SAFE -> DEPLOYED -> SAFE"));
    helpLine(F("  0..9  угол 0..180 с шагом 20"));
    helpLine(F("  r  прогон профиля полёта (п. 43 ТЗ)"));
    helpLine(F("  i  сведения о железе"));
    helpLine(F("  ?  эта справка"));
}

static void printInfo(void)
{
    if (!dbgHasRoom(60))
        return;

    Serial.println(F("--- железо ---"));
    Serial.print(F("  IMU:      "));  Serial.println(ImuDriver::name());
    Serial.print(F("  барометр: "));  Serial.println(BaroDriver::name());
    Serial.print(F("  давление площадки, Па: "));
    Serial.println(Sensors::groundPressure());
    Serial.print(F("  углы привода SAFE/DEPLOY: "));
    Serial.print(RECOVERY_SERVO_SAFE_ANGLE);
    Serial.print('/');
    Serial.println(RECOVERY_SERVO_DEPLOY_ANGLE);
    Serial.print(F("  флаги ошибок: 0x"));
    Serial.println(Diagnostics::flags(), HEX);
    Serial.print(F("  радио: пропущено из-за буфера: "));
    Serial.println(Radio::droppedCount());
    Serial.print(F("  набор: "));
    Serial.println(F(HW_PROFILE_NAME));
}

/* Цикл проверки по п. 40 ТЗ выполняется по шагам, без delay():
 * полётный цикл во время проверки продолжает работать, и если по
 * акселерометру вдруг обнаружится старт, автомат не будет заторможен. */
static uint8_t  g_cycleStep = 0;
static uint32_t g_cycleMs = 0;

static void startCycle(uint32_t nowMs)
{
    g_cycleStep = 1;
    g_cycleMs = nowMs;
    Recovery::serviceSetAngle(RECOVERY_SERVO_SAFE_ANGLE);
    if (dbgHasRoom(24)) Serial.println(F("цикл: SAFE"));
}

static void updateCycle(uint32_t nowMs)
{
    if (g_cycleStep == 0)
        return;

    if ((uint32_t)(nowMs - g_cycleMs) < 1500)
        return;

    g_cycleMs = nowMs;

    switch (g_cycleStep) {
    case 1:
        Recovery::serviceSetAngle(RECOVERY_SERVO_DEPLOY_ANGLE);
        if (dbgHasRoom(28)) Serial.println(F("цикл: DEPLOYED"));
        g_cycleStep = 2;
        break;
    case 2:
        Recovery::serviceSetAngle(RECOVERY_SERVO_SAFE_ANGLE);
        if (dbgHasRoom(40)) Serial.println(F("цикл: возврат в SAFE"));
        g_cycleStep = 3;
        break;
    default:
        if (dbgHasRoom(28)) Serial.println(F("цикл завершён"));
        g_cycleStep = 0;
        break;
    }
}

void init(void)
{
    g_cycleStep = 0;
    g_cycleMs = 0;

    /* Справку здесь НЕ печатаем. init() вызывается из setup(), а там хост
     * ещё только перечисляет устройство после сброса, и запись в USB CDC
     * способна заблокировать прошивку насмерть. Справка выдаётся по
     * команде '?' из главного цикла, когда порт заведомо готов. */
}

void update(uint8_t flightState, uint32_t nowMs)
{
    /* Единственная защита от входа в сервисный режим в полёте, и она же
     * достаточная: вне READY порт не читается совсем. */
    if (flightState != STATE_READY) {
        g_cycleStep = 0;
        return;
    }

    updateCycle(nowMs);

    if (!Serial.available())
        return;

    char c = Serial.read();

    switch (c) {
    case 's':
        Recovery::serviceSetAngle(RECOVERY_SERVO_SAFE_ANGLE);
        if (dbgHasRoom(40)) {
        Serial.print(F("привод -> SAFE, угол "));
        Serial.println(RECOVERY_SERVO_SAFE_ANGLE); }
        break;

    case 'd':
        Recovery::serviceSetAngle(RECOVERY_SERVO_DEPLOY_ANGLE);
        if (dbgHasRoom(44)) {
        Serial.print(F("привод -> DEPLOYED, угол "));
        Serial.println(RECOVERY_SERVO_DEPLOY_ANGLE); }
        break;

    case 't':
        startCycle(nowMs);
        break;

    case 'r':
        /* Прогон подменяет датчики моделью. Запускается только из READY,
         * поэтому в воздухе недоступен. */
        if (dbgHasRoom(40))
            Serial.println(F("--- прогон тестового профиля ---"));
        Sim::start(nowMs);
        break;

    case 'i':
        printInfo();
        break;

    case '?':
    case 'h':
        printHelp();
        break;

    default:
        /* Цифра задаёт угол с шагом 20 градусов. Нужно, чтобы подобрать
         * рабочие углы SAFE и DEPLOYED по п. 47.8 ТЗ, не пересобирая
         * прошивку на каждую пробу. */
        if (c >= '0' && c <= '9') {
            uint8_t angle = (uint8_t)((c - '0') * 20);
            Recovery::serviceSetAngle(angle);
            if (dbgHasRoom(30)) {
                Serial.print(F("привод -> угол "));
                Serial.println(angle);
            }
        }
        break;
    }
}

#else  /* HAS_SERVICE_MODE == 0 */

/* Полётная сборка. Сервисного режима нет, порт не читается. */

void init(void)                    { }
void update(uint8_t, uint32_t)     { }

#endif

} /* namespace Service */
