// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — драйвер ICM-20948
 *
 * ПРОВЕРЕН НА ЖЕЛЕЗЕ 21.09.2026. Именно эта микросхема стоит на плате,
 * несмотря на то что п. 4.1 ТЗ предполагал MPU9250. Подробности и дамп
 * регистров — в docs/HARDWARE.md, раздел 2.
 *
 * Главное отличие от MPU9250, из-за которого готовые библиотеки не
 * подходят: карта регистров разбита на четыре банка, переключаемых
 * записью в REG_BANK_SEL. Настроечные регистры лежат в банке 2, данные —
 * в банке 0. У MPU9250 никаких банков нет вообще, и адреса ключевых
 * регистров другие (PWR_MGMT_1 там 0x6B, а не 0x06).
 */

#include "../config.h"

#if IMU_DRIVER == IMU_DRIVER_ICM20948

#include "imu.h"
#include "i2c_util.h"
#include "../pins.h"

namespace ImuDriver {

/* --- банк 0 --- */
static const uint8_t REG_WHO_AM_I     = 0x00;
static const uint8_t REG_PWR_MGMT_1   = 0x06;
static const uint8_t REG_PWR_MGMT_2   = 0x07;
static const uint8_t REG_ACCEL_XOUT_H = 0x2D;
static const uint8_t REG_BANK_SEL     = 0x7F;

/* --- банк 2 --- */
static const uint8_t REG_GYRO_SMPLRT_DIV = 0x00;
static const uint8_t REG_GYRO_CONFIG_1   = 0x01;
static const uint8_t REG_ACCEL_CONFIG    = 0x14;

static const uint8_t WHO_AM_I_VALUE = 0xEA;

static float g_accelScale = 2048.0f;
static float g_gyroScale  = 16.4f;

static void selectBank(uint8_t bank)
{
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_BANK_SEL, (uint8_t)(bank << 4));
}

/* Пересчёт диапазона из config.h в биты регистра. Побочно выставляет
 * масштабный коэффициент, чтобы настройка и пересчёт не разъехались. */
static uint8_t accelRangeBits(void)
{
    switch (IMU_ACCEL_RANGE_G) {
    case 2:  g_accelScale = 16384.0f; return 0;
    case 4:  g_accelScale =  8192.0f; return 1;
    case 8:  g_accelScale =  4096.0f; return 2;
    default: g_accelScale =  2048.0f; return 3;   /* ±16 g */
    }
}

static uint8_t gyroRangeBits(void)
{
    switch (IMU_GYRO_RANGE_DPS) {
    case 250:  g_gyroScale = 131.0f; return 0;
    case 500:  g_gyroScale =  65.5f; return 1;
    case 1000: g_gyroScale =  32.8f; return 2;
    default:   g_gyroScale =  16.4f; return 3;    /* ±2000 °/с */
    }
}

bool init(void)
{
    uint8_t id = 0;

    selectBank(0);
    if (!I2cUtil::readReg(IMU_I2C_ADDR, REG_WHO_AM_I, &id))
        return false;

    if (id != WHO_AM_I_VALUE)
        return false;

    /* Сброс. Микросхема поднимается около 50 мс, берём с запасом. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_PWR_MGMT_1, 0x80);
    delay(100);

    selectBank(0);
    /* Снять SLEEP. Заводское значение регистра — 0x41, бит SLEEP взведён,
     * и без этой записи датчик молчит. Проверено на стенде. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_PWR_MGMT_1, 0x01);
    delay(30);
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_PWR_MGMT_2, 0x00);
    delay(30);

    selectBank(2);
    /* Биты: [5:3] DLPFCFG, [2:1] FS_SEL, [0] FCHOICE.
     * DLPFCFG = 0 даёт полосу около 250 Гц — достаточно, чтобы не смазать
     * короткий стартовый импульс водяной ракеты. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_ACCEL_CONFIG,
                      (uint8_t)((accelRangeBits() << 1) | 0x01));
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_GYRO_CONFIG_1,
                      (uint8_t)((gyroRangeBits() << 1) | 0x01));
    /* Делитель 0 — максимальная внутренняя частота. Она должна быть выше
     * частоты нашего опроса, иначе будем читать одно и то же значение. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_GYRO_SMPLRT_DIV, 0x00);
    delay(30);

    selectBank(0);
    return true;
}

bool readRaw(int16_t *accel, int16_t *gyro)
{
    uint8_t b[12];

    if (!I2cUtil::readRegs(IMU_I2C_ADDR, REG_ACCEL_XOUT_H, b, 12))
        return false;

    accel[0] = (int16_t)((b[0] << 8) | b[1]);
    accel[1] = (int16_t)((b[2] << 8) | b[3]);
    accel[2] = (int16_t)((b[4] << 8) | b[5]);

    gyro[0]  = (int16_t)((b[6]  << 8) | b[7]);
    gyro[1]  = (int16_t)((b[8]  << 8) | b[9]);
    gyro[2]  = (int16_t)((b[10] << 8) | b[11]);

    return true;
}

float accelScale(void) { return g_accelScale; }
float gyroScale(void)  { return g_gyroScale;  }

const __FlashStringHelper *name(void) { return F("ICM-20948"); }

} /* namespace ImuDriver */

#endif /* IMU_DRIVER == IMU_DRIVER_ICM20948 */
