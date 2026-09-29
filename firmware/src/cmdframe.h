/*
 * ВРО-1 / RocketBoard — кадр команды с земли по радио
 *
 * Формат:  !<номер>|<команда>*XX
 *
 *   номер    0..255, растёт с каждой новой командой; повтор того же номера
 *            борт не выполняет второй раз, а только подтверждает
 *   команда  один символ из сервисного режима: s d t r z R D 0..9
 *   XX       CRC-8 (полином 0x07) от всего, что до '*', два знака hex
 *
 * Заголовок без зависимостей от Arduino, чтобы разбор проверялся обычным
 * компилятором на компьютере (firmware/test/test_cmdframe.cpp) и теми же
 * тестовыми векторами, что и на стороне станции (ground/vro_link.py).
 */

#ifndef CMDFRAME_H
#define CMDFRAME_H

#include <stdint.h>

namespace CmdFrame {

/* CRC-8, полином 0x07, начальное значение 0. Тот же, что в радиострока
 * телеметрии (radio.cpp) и в ground/telemetry.py. */
inline uint8_t crc8(const char *s, const char *end)
{
    uint8_t crc = 0;
    while (s < end) {
        crc ^= (uint8_t)*s++;
        for (uint8_t i = 0; i < 8; i++)
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
    return crc;
}

/* Какие команды вообще можно передать по радио. Только то, что уже есть
 * в сервисном режиме и касается привода и прогона профиля. */
inline bool allowed(char c)
{
    return c == 's' || c == 'd' || c == 't' || c == 'r' || c == 'z' || c == 'R' || c == 'D' ||
           (c >= '0' && c <= '9');
}

inline int hexVal(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* Разбор одной строки без перевода строки. true только если кадр
 * целиком корректен: начало, номер, разделитель, разрешённая команда,
 * верная CRC. Любой мусор из эфира даёт false. */
inline bool parse(const char *line, uint8_t len, uint8_t &seq, char &cmd)
{
    if (len < 6 || line[0] != '!')
        return false;

    const char *end = line + len;
    if (end[-3] != '*')
        return false;

    int hi = hexVal(end[-2]);
    int lo = hexVal(end[-1]);
    if (hi < 0 || lo < 0)
        return false;
    if (crc8(line, end - 3) != (uint8_t)(hi * 16 + lo))
        return false;

    const char *p = line + 1;
    uint16_t n = 0;
    uint8_t digits = 0;
    while (p < end - 3 && *p >= '0' && *p <= '9') {
        n = (uint16_t)(n * 10 + (*p - '0'));
        if (++digits > 3 || n > 255)
            return false;
        p++;
    }
    if (digits == 0 || p >= end - 3 || *p != '|')
        return false;
    p++;
    if (p + 1 != end - 3)             /* ровно один символ команды */
        return false;
    if (!allowed(*p))
        return false;

    seq = (uint8_t)n;
    cmd = *p;
    return true;
}

} /* namespace CmdFrame */

#endif /* CMDFRAME_H */
