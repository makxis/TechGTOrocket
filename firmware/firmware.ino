// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — бортовая программа учебной водяной ракеты
 *
 * Плата: Arduino Pro Micro / Leonardo, ATmega32U4.
 * Датчики: ICM-20948 (0x68) и BMP280 (0x77) — подтверждены на стенде,
 * см. docs/HARDWARE.md.
 *
 * Структура по п. 37 ТЗ: здесь только главный цикл и планирование задач,
 * вся логика вынесена в модули из src/.
 *
 * Приоритеты по п. 6 ТЗ: логика полёта и система спасения выполняются
 * каждый проход цикла, всё остальное — по расписанию и только если
 * пришло время.
 */

#include "src/config.h"
#include "src/pins.h"
#include "src/types.h"
#include "src/diagnostics.h"
#include "src/sensors.h"
#include "src/power.h"
#include "src/flight_state.h"
#include "src/recovery.h"
#include "src/telemetry.h"
#include "src/sd_logger.h"
#include "src/radio.h"
#include "src/service.h"
#include "src/sim.h"
#include "src/dbg.h"

/* Единственные глобальные структуры. Динамическое выделение памяти
 * в критическом цикле запрещено п. 30 ТЗ. */
static SensorData      g_sensors;
static TelemetryRecord g_record;

/* Момент последнего выполнения каждой задачи. */
static uint32_t g_lastSensorMs = 0;
static uint32_t g_lastBaroMs   = 0;
static uint32_t g_lastFlightMs = 0;
static uint32_t g_lastSdMs     = 0;
static uint32_t g_lastRadioMs  = 0;
static uint32_t g_lastDebugMs  = 0;

/* Журнал закрывается один раз после посадки. */
static bool g_logClosed = false;

/* Периоды в миллисекундах, посчитанные из частот в config.h. */
static const uint16_t SENSOR_PERIOD_MS = 1000 / SENSOR_RATE_HZ;
static const uint16_t BARO_PERIOD_MS   = 1000 / BARO_RATE_HZ;
static const uint16_t FLIGHT_PERIOD_MS = 1000 / FLIGHT_RATE_HZ;
static const uint16_t SD_PERIOD_MS     = 1000 / SD_LOG_RATE_HZ;
static const uint16_t RADIO_PERIOD_MS  = 1000 / RADIO_RATE_HZ;
static const uint16_t DEBUG_PERIOD_MS  = 1000 / DEBUG_PRINT_RATE_HZ;

/* Вычитание беззнаковых чисел корректно переживает переполнение millis(),
 * которое наступает через 49 суток. Для ракеты это теория, но писать
 * сравнение иначе — значит закладывать ошибку без нужды. */
static inline bool due(uint32_t now, uint32_t last, uint16_t period)
{
    return (uint32_t)(now - last) >= period;
}

/* ------------------------------------------------------------------ */

static void printDebug(uint32_t now)
{
#if DEBUG_SERIAL_ENABLED
    /* Строка занимает около 80 байт. Если столько в буфере USB не
     * освободилось, пропускаем её целиком: начать печать и упереться
     * в середине означало бы заблокировать полётный цикл. */
    DBG_NEED(128);

    Serial.print(now);
    Serial.print(F(" "));
    Serial.print(flightStateName(FlightManager::state()));
    Serial.print(F(" h="));
    Serial.print(g_sensors.altitude_m, 2);
    Serial.print(F(" max="));
    Serial.print(FlightManager::maxAltitude(), 2);
    Serial.print(F(" |a|="));
    Serial.print(g_sensors.accel_mag, 2);
    Serial.print(F(" p="));
    Serial.print(g_sensors.pressure_pa);
    Serial.print(F(" rec="));
    Serial.print(recoveryStateName(Recovery::state()));
    Serial.print(F(" z="));
    Serial.print(Sensors::groundZeroSet() ? 1 : 0);
    Serial.print(F(" vb="));
    Serial.print(Power::volts(), 2);
    Serial.print(F(" err=0x"));
    Serial.print(Diagnostics::flags(), HEX);
    Serial.print(F(" rdrop="));
    Serial.print(Radio::droppedCount());
    Serial.print(F(" sv="));
    Serial.print(Recovery::servoCommand());
    Serial.print(F(" rx="));
    Serial.print(Radio::rxBytes());
    Serial.print('/');
    Serial.print(Radio::rxFrames());
    Serial.print('/');
    Serial.println(Radio::rxBad());
#else
    (void)now;
#endif
}

/* ------------------------------------------------------------------ */
/*  Последовательность запуска по п. 7 ТЗ                              */
/* ------------------------------------------------------------------ */

void setup()
{
#if DEBUG_SERIAL_ENABLED
    Serial.begin(DEBUG_SERIAL_BAUD);
    /* Ждать открытия порта нельзя: конструкция while (!Serial) повесила бы
     * прошивку намертво, когда компьютер не подключён. Пункт 2 ТЗ требует
     * автономной работы, поэтому просто даём небольшую паузу. */
    /* В setup() ничего не печатаем. На этом месте хост ещё только
     * перечисляет устройство после сброса, и запись в USB CDC может
     * заблокироваться до бесконечности. Всё, что нужно знать о запуске,
     * выдаётся из главного цикла, когда порт заведомо готов. */
#endif

    Diagnostics::init();
    Diagnostics::logEvent(EV_POWER_ON);

    /* Порядок ниже задан п. 7 ТЗ и менять его не следует: привод уходит
     * в безопасное положение сразу после датчиков, до того как начнётся
     * возня с картой и радио. */
    Power::init();
    bool sensorsOk = Sensors::init();

    /* Пауза перед первым движением привода.
     *
     * Сервопривод питается от той же шины +5 В, что и контроллер, и
     * бросок тока при трогании с места просаживает питание всей платы.
     * Если это совпадает с перечислением USB, оно срывается: плата
     * остаётся видимой в системе и принимает новую прошивку, но
     * телеметрию в порт больше не выдаёт до снятия питания.
     *
     * Так и ловили на стенде 21.09.2026 вспышку отказов: они шли
     * подряд, пока одна из загрузок не довела привод до безопасного
     * положения, после чего ход стал нулевым и отказы прекратились.
     *
     * Полсекунды дают USB перечислиться до броска. На автономную
     * работу это не влияет: по п. 7 ТЗ привод всё равно уходит в SAFE
     * до перехода в READY, а полсекунды на старте ничего не решают. */
    delay(500);

    Recovery::init();

    SdLogger::init();
    Radio::init();

    if (Radio::status() == SUBSYS_OK)
        Diagnostics::logEvent(EV_RADIO_OK);

    /* Калибровка давления площадки и нуля гироскопа (п. 9 ТЗ).
     * Занимает около секунды и выполняется до перехода в READY. */
    if (sensorsOk)
        Sensors::calibrateGround();

    FlightManager::init();

    /* Самодиагностика. Отсутствие MicroSD или HC-12 переходу в READY
     * не мешает — это прямо оговорено п. 7 ТЗ. Блокирует только отказ
     * обязательного датчика. */
    if (Diagnostics::hasCritical()) {
#if DEBUG_SERIAL_ENABLED
        Serial.println(F("КРИТИЧЕСКАЯ ОШИБКА: полёт невозможен"));
#endif
        /* В STATE_ERROR автомат не пойдёт дальше, но цикл продолжает
         * крутиться: индикация и телеметрия должны работать, чтобы было
         * видно, что именно сломалось. */
    }

    Telemetry::init();
    FlightManager::setReady();
    Service::init();

    uint32_t now = millis();
    g_lastSensorMs = g_lastBaroMs = g_lastFlightMs = now;
    g_lastSdMs = g_lastRadioMs = g_lastDebugMs = now;
}

/* ------------------------------------------------------------------ */
/*  Главный цикл по п. 48 ТЗ                                           */
/* ------------------------------------------------------------------ */

void loop()
{
    uint32_t now = millis();

    /* --- датчики (п. 6 ТЗ, приоритет 3) --- */
    if (Sim::active()) {
        /* Идёт прогон тестового профиля по п. 43 ТЗ: показания берутся
         * из модели, настоящие датчики не опрашиваются. Всё остальное
         * ниже по циклу работает ровно так же, как в полёте. */
        if (due(now, g_lastSensorMs, SENSOR_PERIOD_MS)) {
            g_lastSensorMs = now;
            Sim::fill(g_sensors, now);
        }
    } else {
        if (due(now, g_lastSensorMs, SENSOR_PERIOD_MS)) {
            g_lastSensorMs = now;
            Sensors::readImu(g_sensors);
        }

        /* Барометр опрашивается реже: обмен с ним дольше, а высота
         * меняется не так быстро, как ускорение. */
        if (due(now, g_lastBaroMs, BARO_PERIOD_MS)) {
            g_lastBaroMs = now;
            Sensors::readBaro(g_sensors, now);
        }
    }

    /* --- ноль высоты на площадке ---
     * Только до старта и не во время прогона модели: в модели давление
     * ненастоящее, а после старта ноль трогать нельзя. */
    if (FlightManager::state() == STATE_READY && !Sim::active())
        Sensors::updateGround(g_sensors, now);

    /* --- автомат полёта (приоритет 1) --- */
    if (due(now, g_lastFlightMs, FLIGHT_PERIOD_MS)) {
        g_lastFlightMs = now;
        FlightManager::update(g_sensors, now);
    }

    /* --- система спасения (приоритет 2) ---
     * Каждый проход цикла, без расписания: это самая важная задача
     * на борту, и задерживать её нельзя. */
    Recovery::update(now);

    /* --- телеметрия (приоритет 4) ---
     * Собирается только тогда, когда её есть кому отдать. Раньше запись
     * строилась каждый проход цикла, то есть в сотни раз чаще, чем
     * использовалась: полсотни байт копирования впустую на каждом
     * проходе при 2560 байтах ОЗУ и 50 Гц полётного цикла. */
    bool sdDue    = due(now, g_lastSdMs, SD_PERIOD_MS);
    bool radioDue = due(now, g_lastRadioMs, RADIO_PERIOD_MS);

    if (sdDue || radioDue)
        Telemetry::build(g_record, g_sensors, now);

    /* --- запись на карту (приоритет 5) --- */
    if (sdDue) {
        g_lastSdMs = now;
        SdLogger::writeRecord(g_record);
    }

    /* --- радиопередача (приоритет 6) --- */
    if (radioDue) {
        g_lastRadioMs = now;
        Radio::sendRecord(g_record);
    }

    /* Досылка событий, не поместившихся в буфер радио. Дёшево: пока
     * очередь пуста, это одна проверка. */
    Radio::update();

    /* --- индикация (приоритет 7) --- */
    /* Пока ноль не поставлен, мигаем как при запуске: «подождите,
     * положите ракету». Автомат при этом уже в READY и старт ловит. */
    uint8_t ledState = FlightManager::state();
    if (ledState == STATE_READY && !Sensors::groundZeroSet())
        ledState = STATE_INIT;
    Power::update(FlightManager::state(), now);
    Diagnostics::updateIndicators(ledState, now);

    /* --- наземный сервисный режим (п. 40 ТЗ) ---
     * В полёте выходит сразу: вне READY порт не читается. */
    Service::update(FlightManager::state(), now);

    /* --- отладочный вывод (п. 39 ТЗ) --- */
    if (due(now, g_lastDebugMs, DEBUG_PERIOD_MS)) {
        g_lastDebugMs = now;
        printDebug(now);
    }

    /* --- завершение полёта (п. 18.7 ТЗ) --- */
    if (FlightManager::isFinished() && !g_logClosed) {
        SdLogger::close();
        g_logClosed = true;
    }
}
