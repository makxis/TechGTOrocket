/*
 * ВРО-1 / RocketBoard — стресс-тест: радио под нагрузкой от привода
 *
 * Проверяет гипотезу: бросок тока сервопривода на общей шине +5 В портит
 * передачу HC-12 (стенд 29.09.2026: строка события в апогее, где
 * раскрывается привод, рвалась на середине).
 *
 * Плата: Arduino Pro Micro / Leonardo. HC-12 на Serial1 (D0/D1),
 * 9600 бод, привод на D6 (как в полётной прошивке, углы 20 и 120).
 *
 * Что делает:
 *   - постоянно льёт в эфир пронумерованные строки в формате п. 22 ТЗ,
 *     как только в буфере передатчика есть место, то есть канал занят
 *     почти на все 100 %, что хуже реального полёта;
 *   - через нарастающие интервалы переставляет привод SAFE <-> DEPLOY и
 *     перед каждым движением шлёт строку "#время|SERVO_MOVE", чтобы на
 *     земле было видно, где именно рвётся поток;
 *   - в поле состояния восстановления идёт SAFE или DEPLOYED, по
 *     текущему положению привода.
 *
 * Управление с USB (115200), одиночные символы:
 *   0  привод выключен: контрольный прогон, идёт только поток
 *   1  привод включён (по умолчанию, первое движение через 5 с)
 *   f  переключить поток: насыщение канала <-> 5 пакетов/с, как в полёте
 *   r  плавное движение привода (шаги 4 градуса по 10 мс) <-> рывком
 *   x  разнести во времени: дослать буфер, двинуть привод, молчать 400 мс
 *   d  повторы: номер меняется раз в секунду, эта же строка уходит 5 раз/с
 *   i  вывести состояние
 *
 * Принимать: python3 tools/radio_test.py --ground <порт> --ground-baud 9600
 * Сравнить прогон с приводом (1) и без (0). Разница в потерях и мусорных
 * строках и есть вклад привода.
 */

#include <Servo.h>

#define PIN_SERVO        6
#define ANGLE_SAFE       20
#define ANGLE_DEPLOY     120
#define HC12_BAUD        9600UL
#define START_DELAY_MS   5000UL

static Servo    g_servo;
static bool     g_servoOn = true;
static bool     g_slow = false;         /* 5 пакетов/с вместо насыщения */
static bool     g_ramp = false;         /* плавно вместо рывка */
static uint8_t  g_angle = ANGLE_SAFE;   /* текущий угол при плавном ходе */
static uint8_t  g_target = ANGLE_SAFE;
static uint32_t g_lastStep = 0;
static bool     g_gate = false;         /* радио молчит, пока ходит привод */
static uint32_t g_quietUntil = 0;
#define QUIET_MS 400UL
static bool     g_dup = false;          /* один номер в секунду, повторы */
static uint32_t g_seqStamp = 0;
static uint32_t g_seqTime = 0;
static bool     g_deployed = false;
static uint16_t g_seq = 0;
static uint32_t g_lastMove = 0;
static uint32_t g_lastSend = 0;
static uint16_t g_moves = 0;
static uint8_t  g_ivIdx = 0;
static uint8_t  g_ivRep = 0;

/* Интервалы между движениями, мс. Каждый повторяется по 4 раза. */
static const uint16_t INTERVALS[] = { 3000, 1500, 700, 300, 150 };
#define N_INTERVALS (sizeof(INTERVALS) / sizeof(INTERVALS[0]))

static void sendPacket(uint32_t now)
{
    char buf[48];
    uint32_t stamp = now;
    if (g_dup) {
        if (now - g_lastSend < 200)
            return;
        if (now - g_seqStamp >= 1000) {
            g_seq++;
            g_seqStamp = now;
            g_seqTime = now;
        }
        stamp = g_seqTime;
    }
    snprintf(buf, sizeof(buf), "%u|%lu|READY|0.00|101325|%s|0",
             g_seq, (unsigned long)stamp, g_deployed ? "DEPLOYED" : "SAFE");
    if (Serial1.availableForWrite() < (int)strlen(buf) + 2)
        return;
    Serial1.println(buf);
    if (g_dup)
        g_lastSend = now;
    else
        g_seq++;
}

static bool sendEvent(uint32_t now)
{
    char buf[32];
    snprintf(buf, sizeof(buf), "#%lu|SERVO_MOVE", (unsigned long)now);
    if (Serial1.availableForWrite() < (int)strlen(buf) + 2)
        return false;
    Serial1.println(buf);
    return true;
}

void setup()
{
    Serial.begin(115200);
    Serial1.begin(HC12_BAUD);
    /* Привод трогаем не сразу: сначала пусть перечислится USB, иначе
     * бросок тока срывает его (см. firmware.ino). */
    g_lastMove = millis() + START_DELAY_MS;
}

void loop()
{
    uint32_t now = millis();

    if (Serial.available()) {
        char c = Serial.read();
        if (c == '0') g_servoOn = false;
        if (c == '1') g_servoOn = true;
        if (c == 'f') g_slow = !g_slow;
        if (c == 'r') g_ramp = !g_ramp;
        if (c == 'x') g_gate = !g_gate;
        if (c == 'd') g_dup = !g_dup;
        if (c == 'i') {
            Serial.print(F("servo="));  Serial.print(g_servoOn);
            Serial.print(F(" slow="));  Serial.print(g_slow);
            Serial.print(F(" ramp="));  Serial.print(g_ramp);
            Serial.print(F(" gate="));  Serial.print(g_gate);
            Serial.print(F(" moves=")); Serial.print(g_moves);
            Serial.print(F(" seq="));   Serial.println(g_seq);
        }
    }

    /* Движение привода. Сначала событие в эфир, потом сам привод. Если
     * событие не влезло в буфер, ждём следующего прохода: метка нужна. */
    if (g_servoOn && (int32_t)(now - g_lastMove) >= (int32_t)INTERVALS[g_ivIdx]) {
        if (sendEvent(now)) {
            if (g_gate) {
                Serial1.flush();            /* всё уже ушло в эфир */
                g_quietUntil = millis() + QUIET_MS;
            }
            g_deployed = !g_deployed;
            g_servo.attach(PIN_SERVO);
            g_target = g_deployed ? ANGLE_DEPLOY : ANGLE_SAFE;
            if (!g_ramp) {
                g_angle = g_target;
                g_servo.write(g_angle);
            }
            g_lastMove = now;
            g_moves++;
            if (++g_ivRep >= 4) {
                g_ivRep = 0;
                g_ivIdx = (g_ivIdx + 1) % N_INTERVALS;
            }
        }
    }

    /* Плавный ход: один шаг за проход не чаще раза в 10 мс. */
    if (g_ramp && g_angle != g_target && now - g_lastStep >= 10) {
        g_lastStep = now;
        if (g_angle < g_target)
            g_angle = (uint8_t)min((int)g_target, g_angle + 4);
        else
            g_angle = (uint8_t)max((int)g_target, g_angle - 4);
        g_servo.write(g_angle);
    }

    bool quiet = g_gate && (int32_t)(g_quietUntil - now) > 0;
    if (quiet) {
        /* радио молчит */
    } else if (g_dup) {
        sendPacket(now);
    } else if (g_slow) {
        if (now - g_lastSend >= 200) {
            g_lastSend = now;
            sendPacket(now);
        }
    } else {
        sendPacket(now);
    }
}
