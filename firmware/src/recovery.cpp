/*
 * ВРО-1 / RocketBoard — система спасения
 */

#include "recovery.h"
#include "config.h"
#include "pins.h"
#include "diagnostics.h"

#if HAS_SERVO
#include <Servo.h>
#endif

namespace Recovery {

static uint8_t g_state = RECOVERY_SAFE;

#if HAS_SERVO

static Servo   g_servo;

/* Когда была выдана команда на раскрытие. Ноль — не выдавалась. */
static uint32_t g_deployMs = 0;

/* Питание с привода уже снято? */
static bool g_released = false;

/* Когда привод трогали в сервисном режиме. Ноль — не трогали.
 * Нужно, чтобы и после ручной проверки питание снималось само:
 * забытый под нагрузкой SG90 греется и сажает батарею. */
static uint32_t g_serviceMs = 0;

/* Прицепить привод и выставить угол. Вынесено отдельно, потому что
 * attach/detach используются и в полёте, и в сервисном режиме. */
static void setAngle(uint8_t angle)
{
    if (!g_servo.attached())
        g_servo.attach(PIN_SERVO_RECOVERY);

    g_servo.write(angle);
}

void init(void)
{
    g_state = RECOVERY_SAFE;
    g_deployMs = 0;
    g_released = false;

    /* Пункт 7 ТЗ: привод выставляется в SAFE сразу после инициализации
     * датчиков, до всего остального. */
    setAngle(RECOVERY_SERVO_SAFE_ANGLE);
    delay(300);          /* дать приводу физически дойти до положения */

    /* Отпускаем: удержание под нагрузкой греет SG90 и сажает батарею,
     * а держать SAFE механически не требуется. */
    g_servo.detach();
}

void arm(void)
{
    if (g_state == RECOVERY_SAFE)
        g_state = RECOVERY_ARMED;
}

void deploy(bool backup)
{
    /* Пункт 13 ТЗ: команда не должна неконтролируемо повторно
     * переключать привод на каждом цикле. */
    if (g_state == RECOVERY_DEPLOYED)
        return;

    setAngle(RECOVERY_SERVO_DEPLOY_ANGLE);

    g_state = RECOVERY_DEPLOYED;
    g_deployMs = millis();
    g_released = false;

    Diagnostics::logEvent(backup ? EV_BACKUP_DEPLOY : EV_RECOVERY_DEPLOY);
}

void update(uint32_t nowMs)
{
    /* Снятие питания после ручной проверки в сервисном режиме. */
    if (g_serviceMs != 0 &&
        (uint32_t)(nowMs - g_serviceMs) >= RECOVERY_SERVO_HOLD_MS) {
        g_servo.detach();
        g_serviceMs = 0;
    }

    if (g_state != RECOVERY_DEPLOYED || g_released)
        return;

    if ((uint32_t)(nowMs - g_deployMs) < RECOVERY_SERVO_HOLD_MS)
        return;

    /* Время удержания вышло — снимаем питание. Механизм уже раскрыт,
     * дальше привод только тратит заряд и греется. */
    g_servo.detach();
    g_released = true;
}

void serviceSetAngle(uint8_t angle)
{
    setAngle(angle);

    /* Засекаем время, чтобы update() сам снял питание. Иначе привод
     * остался бы под нагрузкой на всё время проверки. */
    g_serviceMs = millis();
    if (g_serviceMs == 0)
        g_serviceMs = 1;        /* ноль зарезервирован под «не трогали» */
}

#else  /* HAS_SERVO == 0 */

/* Сборка без привода. Собрать такую прошивку можно только с явным
 * флагом ALLOW_NO_RECOVERY — проверка стоит в hw_config.h. Состояние
 * системы спасения продолжает отслеживаться и попадает в телеметрию,
 * чтобы по журналу было видно, когда команда была бы выдана. */

static uint32_t g_deployMs = 0;

void init(void)                 { g_state = RECOVERY_SAFE; g_deployMs = 0; }
void arm(void)                  { if (g_state == RECOVERY_SAFE) g_state = RECOVERY_ARMED; }
void update(uint32_t)           { }
void serviceSetAngle(uint8_t)   { }

void deploy(bool backup)
{
    if (g_state == RECOVERY_DEPLOYED)
        return;

    g_state = RECOVERY_DEPLOYED;
    g_deployMs = millis();
    Diagnostics::logEvent(backup ? EV_BACKUP_DEPLOY : EV_RECOVERY_DEPLOY);
}

#endif /* HAS_SERVO */

uint8_t state(void)
{
    return g_state;
}

bool isDeployed(void)
{
    return g_state == RECOVERY_DEPLOYED;
}

uint32_t deployTimeMs(void)
{
    return g_deployMs;
}

} /* namespace Recovery */
