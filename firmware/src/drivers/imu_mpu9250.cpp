/*
 * ВРО-1 / RocketBoard — драйвер MPU9250 / MPU6500
 *
 * !!! НЕ ПРОВЕРЕН НА ЖЕЛЕЗЕ !!!
 *
 * Экземпляра MPU9250 в проекте нет: на закупленном модуле стоит
 * ICM-20948 (см. docs/HARDWARE.md, раздел 2). Драйвер написан по
 * документации на случай, если попадётся модуль с другой микросхемой —
 * модули с маркировкой «GY-91» встречаются и с той, и с другой.
 *
 * Перед первым полётом с этой микросхемой ОБЯЗАТЕЛЬНО:
 *   1. прогнать tools/imu_live и убедиться, что модуль вектора
 *      ускорения в покое равен 1,00 g;
 *   2. проверить знаки осей;
 *   3. снять отметку «не проверен» из этой шапки и из hw_config.h.
 *
 * В отличие от ICM-20948, банков регистров здесь нет, и карта плоская.
 */

#include "../config.h"

#if IMU_DRIVER == IMU_DRIVER_MPU9250

#include "imu.h"
#include "i2c_util.h"
#include "../pins.h"

namespace ImuDriver {

static const uint8_t REG_SMPLRT_DIV   = 0x19;
static const uint8_t REG_CONFIG       = 0x1A;
static const uint8_t REG_GYRO_CONFIG  = 0x1B;
static const uint8_t REG_ACCEL_CONFIG = 0x1C;
static const uint8_t REG_ACCEL_CONFIG2 = 0x1D;
static const uint8_t REG_ACCEL_XOUT_H = 0x3B;
static const uint8_t REG_PWR_MGMT_1   = 0x6B;
static const uint8_t REG_PWR_MGMT_2   = 0x6C;
static const uint8_t REG_WHO_AM_I     = 0x75;

/* Клоны MPU9250 в ходу разные, и WHO_AM_I у них отличается.
 * Принимаем все известные значения семейства. */
static bool whoAmIKnown(uint8_t id)
{
    return id == 0x71 ||   /* MPU9250 */
           id == 0x73 ||   /* MPU9255 */
           id == 0x70 ||   /* MPU6500 */
           id == 0x68;     /* MPU6050 / MPU9150 */
}

static float g_accelScale = 2048.0f;
static float g_gyroScale  = 16.4f;

static uint8_t accelRangeBits(void)
{
    switch (IMU_ACCEL_RANGE_G) {
    case 2:  g_accelScale = 16384.0f; return 0;
    case 4:  g_accelScale =  8192.0f; return 1;
    case 8:  g_accelScale =  4096.0f; return 2;
    default: g_accelScale =  2048.0f; return 3;
    }
}

static uint8_t gyroRangeBits(void)
{
    switch (IMU_GYRO_RANGE_DPS) {
    case 250:  g_gyroScale = 131.0f; return 0;
    case 500:  g_gyroScale =  65.5f; return 1;
    case 1000: g_gyroScale =  32.8f; return 2;
    default:   g_gyroScale =  16.4f; return 3;
    }
}

bool init(void)
{
    uint8_t id = 0;

    if (!I2cUtil::readReg(IMU_I2C_ADDR, REG_WHO_AM_I, &id))
        return false;

    if (!whoAmIKnown(id))
        return false;

    I2cUtil::writeReg(IMU_I2C_ADDR, REG_PWR_MGMT_1, 0x80);   /* сброс */
    delay(100);

    /* Тактирование от гироскопа: стабильнее внутреннего генератора. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_PWR_MGMT_1, 0x01);
    delay(30);
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_PWR_MGMT_2, 0x00);

    /* DLPF гироскопа: полоса 184 Гц. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_CONFIG, 0x01);
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_SMPLRT_DIV, 0x00);

    I2cUtil::writeReg(IMU_I2C_ADDR, REG_GYRO_CONFIG,
                      (uint8_t)(gyroRangeBits() << 3));
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_ACCEL_CONFIG,
                      (uint8_t)(accelRangeBits() << 3));
    /* DLPF акселерометра: полоса 184 Гц. */
    I2cUtil::writeReg(IMU_I2C_ADDR, REG_ACCEL_CONFIG2, 0x01);
    delay(30);

    return true;
}

bool readRaw(int16_t *accel, int16_t *gyro)
{
    /* У MPU9250 между акселерометром и гироскопом вклинены два байта
     * температуры, поэтому читаем 14 байт и пропускаем средние. */
    uint8_t b[14];

    if (!I2cUtil::readRegs(IMU_I2C_ADDR, REG_ACCEL_XOUT_H, b, 14))
        return false;

    accel[0] = (int16_t)((b[0] << 8) | b[1]);
    accel[1] = (int16_t)((b[2] << 8) | b[3]);
    accel[2] = (int16_t)((b[4] << 8) | b[5]);

    /* b[6], b[7] — температура, не используется */

    gyro[0]  = (int16_t)((b[8]  << 8) | b[9]);
    gyro[1]  = (int16_t)((b[10] << 8) | b[11]);
    gyro[2]  = (int16_t)((b[12] << 8) | b[13]);

    return true;
}

float accelScale(void) { return g_accelScale; }
float gyroScale(void)  { return g_gyroScale;  }

const __FlashStringHelper *name(void) { return F("MPU9250 (НЕ ПРОВЕРЕН)"); }

} /* namespace ImuDriver */

#endif /* IMU_DRIVER == IMU_DRIVER_MPU9250 */
