/*
 * ВРО-1 / RocketBoard — общие примитивы работы с шиной I2C
 */

#include <Wire.h>

#include "i2c_util.h"

namespace I2cUtil {

bool writeReg(uint8_t addr, uint8_t reg, uint8_t val)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t len)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);

    /* false оставляет шину захваченной и выдаёт повторный старт.
     * Обе микросхемы на плате это поддерживают — проверено на стенде
     * сравнением чтения с повторным стартом и с полной остановкой. */
    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(addr, len) != len)
        return false;

    for (uint8_t i = 0; i < len; i++)
        buf[i] = Wire.read();

    return true;
}

} /* namespace I2cUtil */
