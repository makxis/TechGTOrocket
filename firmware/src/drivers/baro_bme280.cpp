/*
 * ВРО-1 / RocketBoard — драйвер BME280
 *
 * !!! НЕ ПРОВЕРЕН НА ЖЕЛЕЗЕ !!!
 *
 * На закупленном модуле стоит BMP280 (CHIP_ID = 0x58). Этот драйвер
 * написан на случай модуля с BME280 (CHIP_ID = 0x60) — под маркировкой
 * «10 DOF» встречаются оба варианта.
 *
 * Отличия от BMP280 сводятся к двум вещам: другой CHIP_ID и наличие
 * канала влажности, который надо явно перевести в нужный режим записью
 * в ctrl_hum ДО записи в ctrl_meas — иначе настройка влажности не
 * применится. Сама влажность проекту не нужна, но выключить её корректно
 * всё равно требуется.
 *
 * Расчёт давления и температуры у обеих микросхем одинаков и лежит
 * в bosch_pt.cpp.
 *
 * Перед первым полётом с этой микросхемой убедиться, что давление на
 * столе близко к 100 000 Па, а высота после калибровки держится около нуля.
 */

#include "../config.h"

#if BARO_DRIVER == BARO_DRIVER_BME280

#include "baro.h"
#include "bosch_pt.h"
#include "i2c_util.h"
#include "../pins.h"

namespace BaroDriver {

static const uint8_t REG_CHIP_ID   = 0xD0;
static const uint8_t REG_RESET     = 0xE0;
static const uint8_t REG_CTRL_HUM  = 0xF2;
static const uint8_t REG_CALIB     = 0x88;
static const uint8_t REG_CTRL_MEAS = 0xF4;
static const uint8_t REG_CONFIG    = 0xF5;
static const uint8_t REG_DATA      = 0xF7;

static const uint8_t CHIP_ID_VALUE = 0x60;

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

    /* Влажность выключаем: проекту она не нужна, а каждое её измерение
     * удлиняет цикл преобразования и замедляет выдачу давления.
     * Запись обязана идти ДО ctrl_meas — таково требование микросхемы. */
    I2cUtil::writeReg(BARO_I2C_ADDR, REG_CTRL_HUM, 0x00);

    I2cUtil::writeReg(BARO_I2C_ADDR, REG_CONFIG, (uint8_t)((0x00 << 5) | (0x02 << 2)));

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

    *temp_c = BoschPT::compensateT(g_calib, adc_T) / 100.0f;
    *pressure_pa = (int32_t)BoschPT::compensateP(g_calib, adc_P);

    return true;
}

const __FlashStringHelper *name(void) { return F("BME280 (НЕ ПРОВЕРЕН)"); }

} /* namespace BaroDriver */

#endif /* BARO_DRIVER == BARO_DRIVER_BME280 */
