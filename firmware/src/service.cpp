/*
 * ВРО-1 / RocketBoard — наземный сервисный режим
 */

#include "service.h"
#include "recovery.h"
#include "sensors.h"
#include "power.h"
#include "diagnostics.h"
#include "drivers/imu.h"
#include "drivers/baro.h"
#include "sim.h"
#include "flight_state.h"
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
    helpLine(F("  z  обнулить высоту (ракета неподвижна ~8 с)"));
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
    Serial.print(F("  батарея, В: "));
    Serial.println(Power::volts(), 2);
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

static void returnToReady(uint32_t nowMs);

/* Выполнить одну команду сервисного режима. Общая для USB и радио. */
static void execute(char c, uint32_t nowMs)
{
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
         * поэтому в воздухе недоступен.
         *
         * Если система спасения уже раскрыта (ручное раскрытие или прежний
         * прогон), раскрытие в прогоне не сработает: оно выдаётся один раз
         * (п. 13 ТЗ). Запускать такой прогон бессмысленно, поэтому отказ. */
        if (Recovery::isDeployed()) {
            if (dbgHasRoom(70))
                Serial.println(F("прогон не запущен: спасение уже раскрыто, нужен возврат в READY (R)"));
            break;
        }
        if (dbgHasRoom(40))
            Serial.println(F("--- прогон тестового профиля ---"));
        Sim::start(nowMs);
        break;

    case 'R':
        /* В READY возврат нужен, только если спасение уже раскрыто вручную
         * (проверка на земле): человек сложил парашют и взводит систему
         * заново. Без раскрытия команда ничего не делает. */
        if (Recovery::isDeployed())
            returnToReady(nowMs);
        break;

    case 'z':
        /* Предполётное обнуление высоты. Действует только в READY, куда
         * и так не пускает update(). */
        Sensors::rezero(nowMs);
        if (dbgHasRoom(60))
            Serial.println(F("обнуление высоты: не трогайте ракету 8 секунд"));
        break;

    case 'D':
        /* Открыть парашют: автомат не отработал, оператор открывает парашют
         * сам. Тот же вызов, что и резервное раскрытие по п. 14 ТЗ: выдаётся
         * один раз, повторные вызовы игнорируются (п. 13 ТЗ). */
        Recovery::deploy(true);
        if (dbgHasRoom(50))
            Serial.println(F("открытие парашюта по команде оператора"));
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

#if HAS_RADIO_SERVICE
/* Повтор команды по радио: станция шлёт кадр несколько раз, пока не придёт
 * подтверждение. Тот же номер в течение 4 с второй раз не выполняется
 * (иначе «цикл» и «прогон» запускались бы заново), а только подтверждается. */
static uint8_t  g_lastSeq = 0;
static uint32_t g_lastSeqMs = 0;
static bool     g_haveSeq = false;
#endif

/* Явный возврат оператора в READY после посадки (в том числе после прогона
 * профиля). Ничего не делается само: только по команде 'R' и только в
 * состоянии «посадка». Человек на земле сложил парашют и взводит систему
 * спасения заново.
 *
 * Привод возвращается в SAFE, автомат полёта начинается заново, система
 * спасения взводится. Ноль высоты ставится заново: место посадки могло
 * оказаться не там, откуда стартовали (ракета неподвижна около 8 с). */
static void returnToReady(uint32_t nowMs)
{
    Sim::stop();
    Recovery::init();
    FlightManager::init();
    FlightManager::setReady();
    Sensors::rezero(nowMs);
    if (dbgHasRoom(70))
        Serial.println(F("возврат в READY: привод SAFE, спасение взведено, обнуление 8 с"));
}

void update(uint8_t flightState, uint32_t nowMs)
{
    /* Защита от входа в сервисный режим в полёте: порт читается только в
     * READY (все команды) и в «посадке» (одна команда 'R', возврат в READY).
     * В остальных состояниях порт не читается совсем, а команда, принятая
     * по радио раньше и не выполненная, устаревает за 2 с. */
    if (flightState != STATE_READY) {
        g_cycleStep = 0;
#if HAS_RADIO_SERVICE
        /* Вне READY по радио принимаются ровно две команды, обе с CRC и
         * номером кадра:
         *   D  открыть парашют, в любом состоянии, кроме «посадки»
         *      (если автомат «завис» в полёте, оператор открывает парашют);
         *   R  возврат в READY, только в «посадке».
         * Всё остальное вне READY молча отбрасывается. */
        uint8_t seq;
        char rc;
        if (Radio::takeCommand(seq, rc, nowMs)) {
            bool ok = (rc == 'D' && flightState != STATE_LANDED) ||
                      (rc == 'R' && flightState == STATE_LANDED);
            if (ok) {
                bool repeat = g_haveSeq && seq == g_lastSeq &&
                              (uint32_t)(nowMs - g_lastSeqMs) < 4000UL;
                if (!repeat) {
                    if (rc == 'D')
                        Recovery::deploy(true);
                    else
                        returnToReady(nowMs);
                    g_lastSeq = seq;
                    g_lastSeqMs = nowMs;
                    g_haveSeq = true;
                }
                Radio::sendAck(seq, rc);
            }
        }
#endif
        /* По USB вне READY читается только 'R' в «посадке»: открытие
         * раскрытие по проводу в полёте невозможно физически. */
        if (flightState == STATE_LANDED && Serial.available() && Serial.read() == 'R')
            returnToReady(nowMs);
        return;
    }

    updateCycle(nowMs);

#if HAS_RADIO_SERVICE
    uint8_t seq;
    char rc;
    if (Radio::takeCommand(seq, rc, nowMs)) {
        bool repeat = g_haveSeq && seq == g_lastSeq &&
                      (uint32_t)(nowMs - g_lastSeqMs) < 4000UL;
        if (!repeat) {
            execute(rc, nowMs);
            g_lastSeq = seq;
            g_lastSeqMs = nowMs;
            g_haveSeq = true;
        }
        Radio::sendAck(seq, rc);
    }
#endif

    if (!Serial.available())
        return;

    execute((char)Serial.read(), nowMs);
}

#else  /* HAS_SERVICE_MODE == 0 */

/* Полётная сборка. Сервисного режима нет, порт не читается. */

void init(void)                    { }
void update(uint8_t, uint32_t)     { }

#endif

} /* namespace Service */
