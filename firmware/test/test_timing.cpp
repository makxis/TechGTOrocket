// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
// Проверка сравнения времени: регрессия на баг «привод отпускался сразу».
//
//   g++ -std=c++11 -Wall -Wextra -I../src test_timing.cpp -o /tmp/tt && /tmp/tt

#include <cstdio>
#include "timing.h"

static int failures = 0;

static void check(bool got, bool want, const char *what)
{
    if (got != want) {
        printf("ОШИБКА: %s (получено %d, ожидалось %d)\n", what, got, want);
        failures++;
    }
}

int main()
{
    const uint32_t HOLD = 1200;

    check(Timing::elapsed(5000, 5000, HOLD), false, "в тот же миг ещё не прошло");
    check(Timing::elapsed(5000 + 1199, 5000, HOLD), false, "1199 мс: ещё не прошло");
    check(Timing::elapsed(5000 + 1200, 5000, HOLD), true, "1200 мс: прошло");
    check(Timing::elapsed(5000 + 9000, 5000, HOLD), true, "давно прошло");

    // Тот самый баг: now взят в начале прохода и на 3 мс раньше момента
    // раскрытия. Раньше это считалось «прошло» и привод отпускался.
    check(Timing::elapsed(4997, 5000, HOLD), false, "now на 3 мс раньше since: НЕ прошло");
    check(Timing::elapsed(4000, 5000, HOLD), false, "now на секунду раньше: НЕ прошло");

    // Переполнение millis() через 2^32.
    check(Timing::elapsed(100, 0xFFFFFF00u, HOLD), false, "через переполнение: 356 мс, не прошло");
    check(Timing::elapsed(1300, 0xFFFFFF00u, HOLD), true, "через переполнение: 1556 мс, прошло");

    // Свежесть команды: момент «из будущего» (на несколько мс) не старше лимита.
    check(Timing::olderThan(4997, 5000, 2000), false, "команда на 3 мс из будущего не устарела");
    check(Timing::olderThan(5000 + 2000, 5000, 2000), false, "ровно 2000 мс не старше");
    check(Timing::olderThan(5000 + 2001, 5000, 2000), true, "2001 мс старше");

    if (failures == 0)
        printf("timing: все проверки пройдены\n");
    return failures ? 1 : 0;
}
