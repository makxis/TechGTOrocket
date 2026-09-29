// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — сравнение моментов времени
 *
 * Заголовок без зависимостей от Arduino: проверяется обычным компилятором
 * (firmware/test/test_timing.cpp).
 *
 * Зачем отдельная функция. Главный цикл берёт время now один раз в начале
 * прохода, а модули внутри прохода могут записать millis(), которое на
 * несколько миллисекунд больше. Разность now - момент тогда получается
 * отрицательной, а как беззнаковое число она огромна, и условие «прошло
 * не меньше N мс» ложно срабатывает. Так привод спасения отпускался в тот же
 * проход, где его включили, и не успевал двинуться (стенд 29.09.2026).
 * Здесь разность знаковая: «раньше» это ещё не «прошло».
 */

#ifndef TIMING_H
#define TIMING_H

#include <stdint.h>

namespace Timing {

/* Прошло ли с момента since не меньше holdMs. Если now чуть раньше since
 * (отрицательная разность), не прошло. Корректно при переполнении millis(). */
inline bool elapsed(uint32_t now, uint32_t since, uint32_t holdMs)
{
    return (int32_t)(now - since) >= (int32_t)holdMs;
}

/* Старше ли момент since, чем limitMs. Момент из будущего (now чуть раньше
 * since) не старше. */
inline bool olderThan(uint32_t now, uint32_t since, uint32_t limitMs)
{
    return (int32_t)(now - since) > (int32_t)limitMs;
}

} /* namespace Timing */

#endif /* TIMING_H */
