/*
 * ВРО-1 / RocketBoard — настройка и проверка HC-12
 *
 * Назначение: пп. 47.11-47.12 ТЗ — проверить подключение HC-12 на
 * конкретном экземпляре и выставить мощность передатчика.
 *
 * Плата: Arduino Pro Micro / Leonardo (ATmega32U4)
 * HC-12: Serial1 (D0 RX, D1 TX), SET на D7.
 *
 * Через несколько секунд после старта скетч сам переводит модуль в
 * командный режим, проверяет связь и печатает параметры. Мощность при
 * этом НЕ меняется, если не задан HC12_POWER: её ставят явно клавишей
 * или мастером прошивки. Модуль хранит настройки в своей памяти, поэтому
 * после заливки полётной прошивки мощность остаётся той же.
 *
 * Мощность HC-12: P1 = -1 dBm (0,8 мВт) ... P8 = +20 dBm (100 мВт).
 * Заводская P8. На столе, модули рядом, 28.09.2026: P1 связи нет,
 * P2 теряет пакеты в одну сторону, P3 (+5 dBm, ~3 мВт) без потерь.
 * Перед полётом вернуть P8 (клавиша '8').
 *
 * После настройки скетч работает прозрачным мостом USB <-> радио.
 * Клавиши в мосте (одиночный символ без перевода строки не пересылается):
 *   ~      вывести параметры модуля, мощность не меняет
 *   1..8   поставить мощность P1..P8
 *   p      начать / остановить отправку пронумерованных пакетов
 *          в формате п. 22 ТЗ, 5 Гц
 * Остальной ввод уходит в эфир как есть.
 */

#define PIN_RADIO_SET   7

#ifndef HC12_POWER
#define HC12_POWER      0      /* 0 — не менять, 1..8 — поставить при старте */
#endif

#define HC12_BAUD       9600UL

static bool     g_configured = false;
static bool     g_ping = false;
static uint16_t g_seq = 0;
static uint32_t g_lastPing = 0;

/* Отправить AT-команду и напечатать ответ модуля. */
static void atCmd(const char *cmd, uint16_t waitMs)
{
    while (Serial1.available())
        Serial1.read();

    Serial.print(F("> "));
    Serial.println(cmd);
    Serial1.print(cmd);

    uint32_t t0 = millis();
    bool got = false;
    while (millis() - t0 < waitMs) {
        while (Serial1.available()) {
            Serial.write(Serial1.read());
            got = true;
        }
    }
    if (!got)
        Serial.println(F("  (нет ответа)"));
}

static void enterAt(void)
{
    digitalWrite(PIN_RADIO_SET, LOW);
    delay(80);
}

static void leaveAt(void)
{
    digitalWrite(PIN_RADIO_SET, HIGH);
    delay(80);
}

static void setPower(uint8_t p)
{
    char cmd[8] = "AT+P0";
    cmd[4] = (char)('0' + p);
    enterAt();
    atCmd(cmd, 300);
    leaveAt();
}

static void configure(bool setPwr)
{
    Serial.println(F("--- HC-12: командный режим ---"));
    enterAt();
    atCmd("AT", 200);
    atCmd("AT+V", 300);
    if (setPwr && HC12_POWER >= 1 && HC12_POWER <= 8) {
        char cmd[8] = "AT+P0";
        cmd[4] = (char)('0' + HC12_POWER);
        atCmd(cmd, 300);
    }
    atCmd("AT+RX", 500);
    leaveAt();
    Serial.println(F("--- мост USB <-> радио ---"));
}

void setup()
{
    pinMode(PIN_RADIO_SET, OUTPUT);
    digitalWrite(PIN_RADIO_SET, HIGH);
    Serial.begin(115200);
    Serial1.begin(HC12_BAUD);
    /* Не печатать здесь: хост ещё перечисляет USB. */
}

void loop()
{
    if (!g_configured && millis() > 4000) {
        configure(true);
        g_configured = true;
    }

    while (Serial1.available())
        Serial.write(Serial1.read());

    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '~') {
            configure(false);
        } else if (c >= '1' && c <= '8') {
            setPower((uint8_t)(c - '0'));
        } else if (c == 'p') {
            g_ping = !g_ping;
            Serial.println(g_ping ? F("[пакеты: вкл]") : F("[пакеты: выкл]"));
        } else if (c != '\r' && c != '\n') {
            Serial1.write(c);
        } else if (c == '\n') {
            Serial1.write('\n');
        }
    }

    if (g_ping && millis() - g_lastPing >= 200) {
        g_lastPing = millis();
        /* Строка в формате п. 22 ТЗ, чтобы её разобрала наземная программа. */
        Serial1.print(++g_seq);
        Serial1.print(F("|"));
        Serial1.print(g_lastPing);
        Serial1.println(F("|READY|0.00|101325|SAFE|0"));
    }
}
