// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — живые показания ICM-20948
 *
 * Задачи скетча:
 *   1. окончательно подтвердить, что на 0x68 стоит ICM-20948:
 *      разбуженная микросхема должна выдать вектор ускорения длиной ~1 g;
 *   2. дать данные для определения ориентации осей IMU относительно
 *      продольной оси ракеты (п. 47.4 ТЗ).
 *
 * Как пользоваться для п. 47.4: положить плату каждой стороной вниз и
 * посмотреть, по какой оси появляется -1 g. Ось, дающая -1 g когда плата
 * лежит «как в ракете» соплом вниз, и есть продольная ось.
 *
 * Плата: Arduino Pro Micro / Leonardo (ATmega32U4), I2C: SDA=D2, SCL=D3.
 */

#include <Wire.h>

static const uint8_t IMU_ADDR = 0x68;

/* ---- банк 0 ---- */
static const uint8_t REG_WHO_AM_I    = 0x00;
static const uint8_t REG_PWR_MGMT_1  = 0x06;
static const uint8_t REG_PWR_MGMT_2  = 0x07;
static const uint8_t REG_ACCEL_XOUT_H = 0x2D;
static const uint8_t REG_BANK_SEL    = 0x7F;

/* ---- банк 2 ---- */
static const uint8_t REG_GYRO_CONFIG_1 = 0x01;
static const uint8_t REG_ACCEL_CONFIG  = 0x14;

static const uint8_t WHO_AM_I_ICM20948 = 0xEA;

/* Диапазон ±2 g выбран ради точности: так вектор силы тяжести читается
 * отчётливо. Для полёта диапазон будет другим — см. п. 38 ТЗ. */
static const float ACCEL_LSB_PER_G = 16384.0f;
static const float GYRO_LSB_PER_DPS = 131.0f;   /* ±250 °/с */

static bool writeReg(uint8_t reg, uint8_t val)
{
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

static bool readBytes(uint8_t reg, uint8_t *buf, uint8_t len)
{
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(IMU_ADDR, len) != len)
        return false;

    for (uint8_t i = 0; i < len; i++)
        buf[i] = Wire.read();

    return true;
}

/* У ICM-20948 карта регистров разбита на банки. Перед обращением к
 * настроечным регистрам банк надо выбрать явно — этим она и отличается
 * от MPU9250, у которой банков нет. */
static bool selectBank(uint8_t bank)
{
    return writeReg(REG_BANK_SEL, (uint8_t)(bank << 4));
}

static bool imuInit(void)
{
    uint8_t id = 0;

    selectBank(0);
    if (!readBytes(REG_WHO_AM_I, &id, 1)) {
        Serial.println(F("ОШИБКА: датчик не отвечает"));
        return false;
    }

    Serial.print(F("WHO_AM_I = 0x"));
    Serial.println(id, HEX);

    if (id != WHO_AM_I_ICM20948) {
        Serial.println(F("ОШИБКА: это не ICM-20948"));
        return false;
    }

    /* Сброс и ожидание, пока микросхема поднимется. */
    writeReg(REG_PWR_MGMT_1, 0x80);
    delay(100);

    selectBank(0);
    /* Снять SLEEP, выбрать автоматический источник тактирования. */
    writeReg(REG_PWR_MGMT_1, 0x01);
    delay(50);
    /* Включить все три оси акселерометра и гироскопа. */
    writeReg(REG_PWR_MGMT_2, 0x00);
    delay(50);

    selectBank(2);
    writeReg(REG_GYRO_CONFIG_1, 0x01);  /* ±250 °/с, фильтр включён */
    writeReg(REG_ACCEL_CONFIG, 0x01);   /* ±2 g,     фильтр включён */
    delay(50);

    selectBank(0);

    uint8_t pwr = 0;
    readBytes(REG_PWR_MGMT_1, &pwr, 1);
    Serial.print(F("PWR_MGMT_1 после инициализации = 0x"));
    Serial.println(pwr, HEX);

    return true;
}

static void printFloat(float v)
{
    if (v >= 0.0f)
        Serial.print(' ');
    Serial.print(v, 2);
}

void setup()
{
    Serial.begin(115200);
    Wire.begin();
    Wire.setClock(400000UL);
    delay(300);

    Serial.println(F("=== ICM-20948: живые показания ==="));

    if (!imuInit()) {
        Serial.println(F("инициализация не удалась, останов"));
        while (1)
            delay(1000);
    }

    Serial.println(F("формат: ax ay az [g] | gx gy gz [°/с] | |a| [g] | t [°C]"));
}

void loop()
{
    uint8_t raw[14];

    if (!readBytes(REG_ACCEL_XOUT_H, raw, 14)) {
        Serial.println(F("ошибка чтения"));
        delay(500);
        return;
    }

    int16_t axr = (int16_t)((raw[0] << 8) | raw[1]);
    int16_t ayr = (int16_t)((raw[2] << 8) | raw[3]);
    int16_t azr = (int16_t)((raw[4] << 8) | raw[5]);
    int16_t gxr = (int16_t)((raw[6] << 8) | raw[7]);
    int16_t gyr = (int16_t)((raw[8] << 8) | raw[9]);
    int16_t gzr = (int16_t)((raw[10] << 8) | raw[11]);
    int16_t tr  = (int16_t)((raw[12] << 8) | raw[13]);

    float ax = axr / ACCEL_LSB_PER_G;
    float ay = ayr / ACCEL_LSB_PER_G;
    float az = azr / ACCEL_LSB_PER_G;

    float mag = sqrt(ax * ax + ay * ay + az * az);
    float tc = (tr / 333.87f) + 21.0f;

    printFloat(ax); Serial.print(' ');
    printFloat(ay); Serial.print(' ');
    printFloat(az); Serial.print(F("  | "));
    printFloat(gxr / GYRO_LSB_PER_DPS); Serial.print(' ');
    printFloat(gyr / GYRO_LSB_PER_DPS); Serial.print(' ');
    printFloat(gzr / GYRO_LSB_PER_DPS); Serial.print(F("  | "));
    printFloat(mag); Serial.print(F("  | "));
    printFloat(tc);
    Serial.println();

    delay(500);
}
