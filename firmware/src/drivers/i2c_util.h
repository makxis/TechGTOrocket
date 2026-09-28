/*
 * ВРО-1 / RocketBoard — общие примитивы работы с шиной I2C
 *
 * Вынесены отдельно, чтобы драйверы разных микросхем не дублировали
 * один и тот же код обмена.
 */

#ifndef I2C_UTIL_H
#define I2C_UTIL_H

#include <Arduino.h>

namespace I2cUtil {

bool writeReg(uint8_t addr, uint8_t reg, uint8_t val);

/* Чтение с повторным стартом. Возвращает false, если устройство не
 * ответило или отдало не столько байт, сколько запрошено. */
bool readRegs(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t len);

inline bool readReg(uint8_t addr, uint8_t reg, uint8_t *val)
{
    return readRegs(addr, reg, val, 1);
}

} /* namespace I2cUtil */

#endif /* I2C_UTIL_H */
