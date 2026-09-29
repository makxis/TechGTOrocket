// SPDX-License-Identifier: MIT
// Copyright (c) 2026 makxis
/*
 * ВРО-1 / RocketBoard — диагностика железа
 *
 * Назначение: закрыть пп. 47.3 и 47.4 ТЗ — определить, какие именно
 * микросхемы стоят в конкретном экземпляре GY-91, и на каких адресах.
 *
 * Плата: Arduino Pro Micro / Leonardo (ATmega32U4)
 * I2C:   SDA = D2, SCL = D3
 *
 * Ничего не пишет в датчики кроме включения bypass у MPU — это нужно,
 * чтобы магнитометр AK8963 стал виден на шине.
 */

#include <Wire.h>

static const uint8_t MPU_ADDRS[]  = {0x68, 0x69};
static const uint8_t BARO_ADDRS[] = {0x76, 0x77};
static const uint8_t AK8963_ADDR  = 0x0C;

/* Чтение одного регистра. Возвращает false, если устройство не ответило. */
static bool readReg(uint8_t addr, uint8_t reg, uint8_t *out)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(addr, (uint8_t)1) != 1)
        return false;

    *out = Wire.read();
    return true;
}

static bool writeReg(uint8_t addr, uint8_t reg, uint8_t val)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

static void printHex(uint8_t v)
{
    if (v < 0x10)
        Serial.print('0');
    Serial.print(v, HEX);
}

/* Расшифровка WHO_AM_I инерциального модуля (регистр 0x75). */
static const __FlashStringHelper *decodeImu(uint8_t id)
{
    switch (id) {
    case 0x68: return F("MPU6050 / MPU9150");
    case 0x70: return F("MPU6500");
    case 0x71: return F("MPU9250");
    case 0x73: return F("MPU9255");
    case 0x75: return F("MPU6515");
    case 0x12: return F("ICM20948");
    case 0x11: return F("ICM20600");
    case 0xAC: return F("ICM20789");
    default:   return F("НЕИЗВЕСТНО");
    }
}

/* Расшифровка chip id барометра (регистр 0xD0). */
static const __FlashStringHelper *decodeBaro(uint8_t id)
{
    switch (id) {
    case 0x55: return F("BMP180");
    case 0x56:
    case 0x57:
    case 0x58: return F("BMP280");
    case 0x60: return F("BME280");
    case 0x61: return F("BME680");
    case 0x50: return F("BMP388");
    default:   return F("НЕИЗВЕСТНО");
    }
}

static void scanBus(void)
{
    Serial.println(F("--- скан шины I2C ---"));

    uint8_t found = 0;
    for (uint8_t addr = 0x01; addr <= 0x7E; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            Serial.print(F("  устройство на 0x"));
            printHex(addr);
            Serial.println();
            found++;
        }
    }

    Serial.print(F("  всего: "));
    Serial.println(found);
}

static void probeImu(void)
{
    Serial.println(F("--- инерциальный модуль ---"));

    for (uint8_t i = 0; i < sizeof(MPU_ADDRS); i++) {
        uint8_t addr = MPU_ADDRS[i];
        uint8_t id;

        if (!readReg(addr, 0x75, &id)) {
            Serial.print(F("  0x"));
            printHex(addr);
            Serial.println(F(": нет ответа"));
            continue;
        }

        Serial.print(F("  0x"));
        printHex(addr);
        Serial.print(F(": WHO_AM_I=0x"));
        printHex(id);
        Serial.print(F(" -> "));
        Serial.println(decodeImu(id));

        /* Выводим MPU из сна и включаем bypass, чтобы увидеть AK8963. */
        writeReg(addr, 0x6B, 0x00);   /* PWR_MGMT_1: wake */
        delay(20);
        writeReg(addr, 0x37, 0x02);   /* INT_PIN_CFG: BYPASS_EN */
        delay(20);
    }
}

static void probeMag(void)
{
    Serial.println(F("--- магнитометр ---"));

    uint8_t wia;
    if (!readReg(AK8963_ADDR, 0x00, &wia)) {
        Serial.println(F("  0x0C: нет ответа (магнитометра нет или bypass не включился)"));
        return;
    }

    Serial.print(F("  0x0C: WIA=0x"));
    printHex(wia);
    Serial.println(wia == 0x48 ? F(" -> AK8963") : F(" -> НЕИЗВЕСТНО"));
}

static void probeBaro(void)
{
    Serial.println(F("--- барометр ---"));

    for (uint8_t i = 0; i < sizeof(BARO_ADDRS); i++) {
        uint8_t addr = BARO_ADDRS[i];
        uint8_t id;

        if (!readReg(addr, 0xD0, &id)) {
            Serial.print(F("  0x"));
            printHex(addr);
            Serial.println(F(": нет ответа"));
            continue;
        }

        Serial.print(F("  0x"));
        printHex(addr);
        Serial.print(F(": CHIP_ID=0x"));
        printHex(id);
        Serial.print(F(" -> "));
        Serial.println(decodeBaro(id));
    }
}

void setup()
{
    Serial.begin(115200);
    Wire.begin();
    Wire.setClock(100000UL);
    delay(200);
}

void loop()
{
    Serial.println(F("=== RocketBoard hw_probe ==="));
    scanBus();
    probeImu();
    probeMag();
    probeBaro();
    Serial.println(F("=== конец ===\n"));
    delay(3000);
}
