// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — полётный журнал на MicroSD
 *
 * Используется SdFat, а не штатная библиотека SD. Причина чисто
 * арифметическая: SD занимает 10 920 байт флеша из 28 672 доступных,
 * и наборы B и D с ней в плату не помещаются (замерено 21.09.2026,
 * см. docs/MEMORY.md).
 *
 * SdFat в урезанной конфигурации заметно меньше, потому что из неё
 * выброшено всё, чего проекту не нужно: exFAT, длинные имена файлов,
 * подсчёт свободных кластеров. Карта форматируется в FAT32, имена
 * файлов вида FLT0001.CSV укладываются в формат 8.3 — ничего из
 * выброшенного не требуется.
 *
 * ВАЖНО: настройки SdFat задаются флагами сборки в sketch.yaml, а не
 * в этом файле. Библиотека компилируется отдельными единицами трансляции,
 * и определения, сделанные здесь, до её собственных файлов не дойдут —
 * получилось бы расхождение настроек между библиотекой и вызывающим
 * кодом, а это худший вид ошибки: сборка проходит, а работает неверно.
 *
 * Модуль MicroSD на стенде физически отсутствует (docs/HARDWARE.md,
 * раздел 6). Код написан так, чтобы это не мешало: при неудачной
 * инициализации подсистема переходит в SUBSYS_ABSENT, а все вызовы
 * становятся пустыми. Полётная логика об этом не знает — пп. 5 и 36 ТЗ.
 */

#include "sd_logger.h"
#include "config.h"
#include "pins.h"
#include "diagnostics.h"
#include "strbuf.h"

#if HAS_SD
#include <SPI.h>
#include <SdFat.h>
#endif

namespace SdLogger {

static uint8_t g_status = SUBSYS_ABSENT;

#if HAS_SD

static SdFat32 g_sd;
static File32  g_file;

/* Строка собирается в статическом буфере. Динамическое выделение и String
 * в полётном цикле запрещены п. 30 ТЗ.
 *
 * 96 байт хватает с запасом: самая длинная строка CSV по п. 19 ТЗ — это
 * около 80 символов. Держать 128 при 2560 байтах ОЗУ незачем. */
static char g_line[96];

/* Заголовок CSV по п. 19 ТЗ. Лежит во флеше. */
static const char CSV_HEADER[] PROGMEM =
    "time_ms,flight_time_ms,packet_id,state,pressure_pa,temp_c,"
    "altitude_m,max_altitude_m,ax,ay,az,gx,gy,gz,recovery_state,error_flags";

/* Подобрать свободное имя файла. Существующие не трогаем — п. 18 ТЗ. */
static bool openNewFile(void)
{
    char name[13];

    for (uint16_t n = 1; n <= 9999; n++) {
        /* Имя вида FLT0007.CSV собирается вручную, без snprintf_P:
         * см. пояснение в strbuf.h о цене printf на этой плате. */
        char *p = name;
        char *e = name + sizeof(name);

        p = StrBuf::addStr(p, e, "FLT");
        p = StrBuf::addPadded(p, e, n, 4);
        p = StrBuf::addStr(p, e, ".CSV");
        StrBuf::terminate(p);

        if (g_sd.exists(name))
            continue;

        /* O_EXCL страхует от гонки: если файл всё-таки появился между
         * проверкой и открытием, открытие не состоится и мы возьмём
         * следующий номер, а не затрём чужой журнал. */
        if (!g_file.open(name, O_WRONLY | O_CREAT | O_EXCL))
            continue;

        strncpy_P(g_line, CSV_HEADER, sizeof(g_line) - 1);
        g_line[sizeof(g_line) - 1] = '\0';
        g_file.println(g_line);
        g_file.sync();
        return true;
    }

    return false;
}

void init(void)
{
    g_status = SUBSYS_ABSENT;

    pinMode(PIN_SD_CS, OUTPUT);

    /* 8 МГц вместо максимума: длинные провода до модуля на макете
     * держат полную скорость не всегда, а выигрыш во времени записи
     * нам не нужен — пишем по 25 строк в секунду. */
    if (!g_sd.begin(PIN_SD_CS, SD_SCK_MHZ(8))) {
        /* Карты нет. Это штатная конфигурация по п. 27 ТЗ, поэтому
         * ошибку не взводим — только фиксируем событие. */
        Diagnostics::logEvent(EV_SD_FAIL);
        return;
    }

    if (!openNewFile()) {
        /* Карта есть, но файл не создался. А вот это уже ошибка. */
        Diagnostics::raise(ERROR_SD_INIT);
        Diagnostics::logEvent(EV_SD_FAIL);
        return;
    }

    g_status = SUBSYS_OK;
    Diagnostics::logEvent(EV_SD_OK);
}

void writeRecord(const TelemetryRecord &rec)
{
    if (g_status != SUBSYS_OK)
        return;

    char *p = g_line;
    char *e = g_line + sizeof(g_line);

    p = StrBuf::addULong(p, e, rec.timestamp_ms);    *p++ = ',';
    p = StrBuf::addULong(p, e, rec.flight_time_ms);  *p++ = ',';
    p = StrBuf::addULong(p, e, rec.packet_id);       *p++ = ',';

    /* Имена состояний берём из общей таблицы, чтобы журнал на карте и
     * радиопакеты не разъехались в названиях. */
    p = StrBuf::addProgmem(p, e, flightStateName(rec.flight_state));
    *p++ = ',';

    p = StrBuf::addLong(p, e, rec.pressure_pa);           *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.temperature_c, 1);     *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.altitude_m, 2);        *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.max_altitude_m, 2);    *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.accel_x, 2);           *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.accel_y, 2);           *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.accel_z, 2);           *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.gyro_x, 1);            *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.gyro_y, 1);            *p++ = ',';
    p = StrBuf::addFloat(p, e, rec.gyro_z, 1);            *p++ = ',';

    p = StrBuf::addProgmem(p, e, recoveryStateName(rec.recovery_state));
    *p++ = ',';

    p = StrBuf::addULong(p, e, rec.error_flags);
    *p = '\0';

    /* println пишет в буфер библиотеки, обращения к карте здесь ещё нет.
     * Побайтовой записи, запрещённой п. 20 ТЗ, не происходит. */
    if (g_file.println(g_line) == 0) {
        Diagnostics::raise(ERROR_SD_WRITE);
        g_status = SUBSYS_FAILED;
    }
}

void writeEvent(uint32_t timeMs, uint8_t ev)
{
    if (g_status != SUBSYS_OK)
        return;

    char *p = g_line;
    char *e = g_line + sizeof(g_line);

    /* События помечаем решёткой, чтобы не ломать разбор CSV наземной
     * программой. */
    *p++ = '#';
    p = StrBuf::addULong(p, e, timeMs);
    *p++ = ',';
    p = StrBuf::addProgmem(p, e, eventName(ev));
    *p = '\0';

    g_file.println(g_line);

    /* Событий мало, и каждое из них важно для разбора полёта, поэтому
     * сбрасываем сразу: потерять их при жёсткой посадке нельзя. */
    flush();
}

void flush(void)
{
    if (g_status != SUBSYS_OK)
        return;

    g_file.sync();
}

void close(void)
{
    if (g_status != SUBSYS_OK)
        return;

    g_file.sync();
    g_file.close();
    g_status = SUBSYS_ABSENT;
}

bool isAvailable(void)
{
    return g_status == SUBSYS_OK;
}

#else  /* HAS_SD == 0 */

/* Набор без карты. Библиотека SdFat в сборку не попадает вовсе. */

void init(void)                              { g_status = SUBSYS_ABSENT; }
void writeRecord(const TelemetryRecord &)    { }
void writeEvent(uint32_t, uint8_t)           { }
void flush(void)                             { }
void close(void)                             { }
bool isAvailable(void)                       { return false; }

#endif /* HAS_SD */

uint8_t status(void)
{
    return g_status;
}

} /* namespace SdLogger */
