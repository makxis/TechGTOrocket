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
    /* dtostrf, в отличие от printf с %f, входит в avr-libc без
     * подключения дополнительного варианта библиотеки. */
    char tmp[16];
    dtostrf(v, 1, prec, tmp);
    return addStr(p, end, tmp);
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
