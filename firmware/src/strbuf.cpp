// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — сборка строк в статическом буфере
 */

#include "strbuf.h"

namespace StrBuf {

char *addChar(char *p, char *end, char c)
{
    if (p < end - 1)
        *p++ = c;

    return p;
}

char *addStr(char *p, char *end, const char *s)
{
    while (*s && p < end - 1)
        *p++ = *s++;

    return p;
}

char *addProgmem(char *p, char *end, const __FlashStringHelper *s)
{
    PGM_P src = (PGM_P)s;
    char c;

    while ((c = pgm_read_byte(src++)) != '\0' && p < end - 1)
        *p++ = c;

    return p;
}

char *addULong(char *p, char *end, uint32_t v)
{
    /* 10 знаков максимума uint32_t плюс завершающий ноль. */
    char tmp[12];
    ultoa(v, tmp, 10);
    return addStr(p, end, tmp);
}

char *addLong(char *p, char *end, int32_t v)
{
    char tmp[13];
    ltoa(v, tmp, 10);
    return addStr(p, end, tmp);
}

char *addFloat(char *p, char *end, float v, uint8_t prec)
{
    /* Своё вместо dtostrf: тот тянет за собой универсальный форматтер
     * avr-libc, около 1,1 КБ флеша. Нам нужны один-два знака после
     * запятой: умножаем, округляем и печатаем как целое. Без этого набор D
     * не поместился после установки нуля на площадке (28.09.2026).
     *
     * prec — 0, 1 или 2. */
    uint8_t mul = (prec >= 2) ? 100 : (prec == 1 ? 10 : 1);

    bool neg = v < 0.0f;
    if (neg)
        v = -v;

    /* Заодно ловит NaN и бесконечность: для них сравнение ложно. */
    if (!(v < 4.0e7f))
        return addStr(p, end, "ovf");

    uint32_t n = (uint32_t)(v * mul + 0.5f);

    if (neg && n != 0)
        p = addChar(p, end, '-');

    p = addULong(p, end, n / mul);
    if (prec == 0)
        return p;

    p = addChar(p, end, '.');
    return addPadded(p, end, (uint16_t)(n % mul), prec >= 2 ? 2 : 1);
}

char *addPadded(char *p, char *end, uint16_t v, uint8_t width)
{
    char tmp[6];
    utoa(v, tmp, 10);

    uint8_t len = strlen(tmp);

    while (len < width) {
        p = addChar(p, end, '0');
        len++;
    }

    return addStr(p, end, tmp);
}

} /* namespace StrBuf */
