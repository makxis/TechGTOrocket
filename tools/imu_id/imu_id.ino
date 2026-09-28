/*
 * ВРО-1 / RocketBoard — идентификация инерциального модуля
 *
 * hw_probe показал на 0x68 значение WHO_AM_I(0x75) = 0x40, которое не
 * соответствует ни одному известному MPU. Этот скетч выясняет, что это за
 * микросхема на самом деле:
 *
 *   - читает регистры двумя способами (с повторным стартом и без),
 *     потому что некоторые клоны MPU не поддерживают repeated start;
 *   - читает регистры-идентификаторы всех вероятных кандидатов;
 *   - снимает полный дамп 0x00..0x7F для ручного анализа.
 *
 * Плата: Arduino Pro Micro / Leonardo (ATmega32U4), I2C: SDA=D2, SCL=D3.
 */

#include <Wire.h>

static const uint8_t IMU_ADDR = 0x68;

static void printHex(uint8_t v)
{
    if (v < 0x10)
        Serial.print('0');
    Serial.print(v, HEX);
}

/* repeated == true  -> Sr (повторный старт, как в большинстве библиотек)
 * repeated == false -> P затем S (полная остановка между записью и чтением) */
static bool readReg(uint8_t addr, uint8_t reg, uint8_t *out, bool repeated)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(!repeated ? true : false) != 0)
        return false;

    if (Wire.requestFrom(addr, (uint8_t)1) != 1)
        return false;

    *out = Wire.read();
    return true;
}

static void showId(const __FlashStringHelper *label, uint8_t reg)
{
    uint8_t vSr = 0, vSp = 0;
    bool okSr = readReg(IMU_ADDR, reg, &vSr, true);
    bool okSp = readReg(IMU_ADDR, reg, &vSp, false);

    Serial.print(F("  "));
    Serial.print(label);
    Serial.print(F(" (0x"));
    printHex(reg);
    Serial.print(F("): Sr="));
    if (okSr) {
        Serial.print(F("0x"));
        printHex(vSr);
    } else {
        Serial.print(F("--"));
    }
    Serial.print(F("  Stop="));
    if (okSp) {
        Serial.print(F("0x"));
        printHex(vSp);
    } else {
        Serial.print(F("--"));
    }
    Serial.println();
}

static void dumpAll(void)
{
    Serial.println(F("--- дамп 0x00..0x7F (repeated start) ---"));
    Serial.println(F("     +0 +1 +2 +3 +4 +5 +6 +7 +8 +9 +A +B +C +D +E +F"));

    for (uint8_t base = 0x00; base < 0x80; base += 0x10) {
        Serial.print(F("  "));
        printHex(base);
        Serial.print(F(":"));

        for (uint8_t off = 0; off < 0x10; off++) {
            uint8_t v;
            Serial.print(' ');
            if (readReg(IMU_ADDR, base + off, &v, true))
                printHex(v);
            else
                Serial.print(F("--"));
        }
        Serial.println();
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
    Serial.println(F("=== идентификация IMU на 0x68 ==="));

    Serial.println(F("--- регистры-идентификаторы ---"));
    showId(F("MPU WHO_AM_I    "), 0x75);
    showId(F("ICM20948 WHO_AM_I"), 0x00);
    showId(F("BMI160 CHIPID   "), 0x00);
    showId(F("LSM6 WHO_AM_I   "), 0x0F);
    showId(F("BMI270 CHIP_ID  "), 0x00);
    showId(F("QMI8658 WHO_AM_I"), 0x00);

    dumpAll();

    Serial.println(F("=== конец ===\n"));
    delay(5000);
}
