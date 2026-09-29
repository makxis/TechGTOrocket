// Проверка разбора кадров команд на компьютере, без платы.
//
//   g++ -std=c++11 -Wall -Wextra -I../src test_cmdframe.cpp -o /tmp/t && /tmp/t
//
// Контрольные суммы посчитаны на стороне станции (ground/telemetry.py crc8):
// разбор в прошивке и сборка кадра на земле обязаны сходиться.

#include <cstdio>
#include <cstring>
#include "cmdframe.h"

static int failures = 0;

static void expectOk(const char *frame, uint8_t seq, char cmd)
{
    uint8_t s = 0; char c = 0;
    bool ok = CmdFrame::parse(frame, (uint8_t)strlen(frame), s, c);
    if (!ok || s != seq || c != cmd) {
        printf("ОШИБКА: %s должен разобраться как (%u, %c), получено ok=%d (%u, %c)\n",
               frame, seq, cmd, ok, s, c);
        failures++;
    }
}

static void expectBad(const char *frame, const char *why)
{
    uint8_t s = 0; char c = 0;
    if (CmdFrame::parse(frame, (uint8_t)strlen(frame), s, c)) {
        printf("ОШИБКА: %s должен быть отвергнут (%s)\n", frame, why);
        failures++;
    }
}

int main()
{
    // Векторы из Python.
    expectOk("!7|d*4A", 7, 'd');
    expectOk("!0|s*39", 0, 's');
    expectOk("!255|9*B7", 255, '9');
    expectOk("!12|r*89", 12, 'r');
    expectOk("!100|t*7B", 100, 't');
    expectOk("!5|z*C6", 5, 'z');            // предполётное обнуление
    expectOk("!7|d*4a", 7, 'd');            // строчные hex тоже

    expectBad("!7|d*4B", "неверная CRC");
    expectBad("!7|d", "нет CRC");
    expectBad("!7|d*4", "обрезана CRC");
    expectBad("7|d*92", "нет '!'");
    expectBad("!|d*4D", "нет номера");
    expectBad("!256|d*9E", "номер больше 255");
    expectBad("!7|dd*CA", "две команды");
    expectBad("!7|i*69", "команда не разрешена по радио");
    expectBad("!7|d!*16", "лишний символ");
    expectBad("", "пусто");
    expectBad("!7|d*ZZ", "не hex");

    // Вся 8-битная ерунда: ни одна случайная строка не должна пройти.
    // (Детерминированный перебор, не случайный.)
    int accepted = 0;
    for (int a = 0; a < 256; a++) {
        for (int b = 0; b < 256; b++) {
            char buf[8] = { '!', (char)a, '|', (char)b, '*', 'A', 'A', 0 };
            uint8_t s; char c;
            if (CmdFrame::parse(buf, 7, s, c))
                accepted++;
        }
    }
    // Проходят только кадры, у которых номер (одна цифра) и команда
    // корректны, а CRC случайно оказалась 0xAA: их немного.
    if (accepted > 4) {
        printf("ОШИБКА: слишком много случайных кадров прошло: %d\n", accepted);
        failures++;
    }

    if (failures == 0)
        printf("cmdframe: все проверки пройдены\n");
    return failures ? 1 : 0;
}
