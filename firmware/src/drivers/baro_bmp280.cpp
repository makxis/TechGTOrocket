// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — драйвер BMP280
 *
 * ПРОВЕРЕН НА ЖЕЛЕЗЕ 21.09.2026: микросхема отвечает на адресе 0x77,
 * CHIP_ID = 0x58. См. docs/HARDWARE.md, раздел 3.
 *
 * Даёт давление и температуру. Влажности у BMP280 нет, но по ТЗ она и
 * не требуется: относительная высота считается по давлению.
 */

#include "../config.h"

#if BARO_DRIVER == BARO_DRIVER_BMP280

#include "baro.h"
#include "bosch_pt.h"
#include "i2c_util.h"
#include "../pins.h"

namespace BaroDriver {

static const uint8_t REG_CHIP_ID   = 0xD0;
static const uint8_t REG_RESET     = 0xE0;
static const uint8_t REG_CALIB     = 0x88;
static const uint8_t REG_CTRL_MEAS = 0xF4;
static const uint8_t REG_CONFIG    = 0xF5;
static const uint8_t REG_DATA      = 0xF7;

static const uint8_t CHIP_ID_VALUE = 0x58;

static BoschPT::Calib g_calib;

bool init(void)
{
    uint8_t id = 0;

    if (!I2cUtil::readReg(BARO_I2C_ADDR, REG_CHIP_ID, &id))
        return false;

    if (id != CHIP_ID_VALUE)
        return false;

    I2cUtil::writeReg(BARO_I2C_ADDR, REG_RESET, 0xB6);
    delay(10);

    uint8_t raw[24];
    if (!I2cUtil::readRegs(BARO_I2C_ADDR, REG_CALIB, raw, 24))
        return false;

    if (!BoschPT::parseCalib(raw, g_calib))
        return false;

    /* config: интервал простоя 0,5 мс, аппаратный фильтр x4.
     * Лёгкий фильтр гасит одиночные выбросы, но не съедает задержку так,
     * как это сделал бы x16 — компромисс по п. 34 ТЗ. */
    I2cUtil::writeReg(BARO_I2C_ADDR, REG_CONFIG, (uint8_t)((0x00 << 5) | (0x02 << 2)));

    /* ctrl_meas: температура x1, давление x4, непрерывный режим.
     * Даёт около 100 измерений в секунду — с запасом относительно
     * BARO_RATE_HZ. */
    I2cUtil::writeReg(BARO_I2C_ADDR, REG_CTRL_MEAS,
                      (uint8_t)((0x01 << 5) | (0x03 << 2) | 0x03));
    delay(50);

    return true;
}

bool read(int32_t *pressure_pa, float *temp_c)
{
    uint8_t b[6];

    if (!I2cUtil::readRegs(BARO_I2C_ADDR, REG_DATA, b, 6))
        return false;

    int32_t adc_P = ((int32_t)b[0] << 12) | ((int32_t)b[1] << 4) | (b[2] >> 4);
    int32_t adc_T = ((int32_t)b[3] << 12) | ((int32_t)b[4] << 4) | (b[5] >> 4);

    /* Температуру считаем первой: она заполняет t_fine, без которого
     * компенсация давления не работает. */
    *temp_c = BoschPT::compensateT(g_calib, adc_T) / 100.0f;
    *pressure_pa = (int32_t)BoschPT::compensateP(g_calib, adc_P);

    return true;
}

const __FlashStringHelper *name(void) { return F("BMP280"); }

} /* namespace BaroDriver */

#endif /* BARO_DRIVER == BARO_DRIVER_BMP280 */
