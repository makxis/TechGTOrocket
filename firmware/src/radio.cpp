/*
 * ВРО-1 / RocketBoard — радиотелеметрия через HC-12
 *
 * HC-12 висит на аппаратном UART Serial1 (D0/D1). Отладочный вывод идёт
 * по USB через Serial и с радиоканалом не пересекается.
 *
 * Обнаружить присутствие HC-12 по одному UART невозможно: модуль не
 * отвечает ничем, пока не переведён в командный режим выводом SET, а его
 * подключение по п. 47.12 ТЗ ещё не подтверждено. Поэтому передаём
 * вслепую. Это безопасно: если модуля нет, байты просто уходят в никуда,
 * а передатчик UART всё равно не блокируется. Ровно такого поведения
 * требует п. 23 ТЗ.
 */

#include "radio.h"
#include "config.h"
#include "pins.h"
#include "strbuf.h"
#include "cmdframe.h"

namespace Radio {

static uint8_t  g_status = SUBSYS_ABSENT;
static uint16_t g_dropped = 0;

/* Собственный сквозной номер радиопакета.
 *
 * Поле packet_id из общей записи телеметрии для этого не годится: оно
 * увеличивается при каждой сборке записи, то есть в разы чаще, чем идёт
 * передача. Разница между соседними радиопакетами получалась бы в сотню
 * с лишним и вдобавок плавала. Наземная программа по такому номеру
 * пропуск не обнаружит, а п. 17 и п. 45 ТЗ требуют именно этого.
 *
 * Здесь номер увеличивается ровно на единицу на каждый ушедший в эфир
 * пакет, поэтому любой разрыв на земле означает потерю. */
static uint16_t g_radioSeq = 0;

#if HAS_RADIO

/* Самый длинный пакет по п. 22 ТЗ укладывается примерно в 50 байт.
 * Берём с запасом, но без расточительства — ОЗУ всего 2560 байт. */
static char g_buf[64];

/* Очередь событий, которым не хватило места в буфере передатчика.
 *
 * Буфер Serial1 — 64 байта, а на 9600 бод он пустеет примерно по байту
 * в миллисекунду. События идут пачками: в апогее в одном такте
 * возникают APOGEE_CONFIRMED, RECOVERY_DEPLOY и DESCENT, около 65 байт.
 * Без очереди два последних молча терялись, и с земли не было видно
 * самого важного — раскрытия спасения (стенд, 28.09.2026).
 *
 * Ждать освобождения буфера нельзя (пп. 23 и 31 ТЗ), поэтому событие
 * откладывается и досылается из update() на следующих проходах цикла.
 * Порядок событий сохраняется. */
#define EVQ_SIZE 4          /* степень двойки */

struct PendingEvent {
    uint32_t timeMs;
    uint8_t  ev;
};

static PendingEvent g_evq[EVQ_SIZE];
static uint8_t g_evHead = 0;
static uint8_t g_evCount = 0;

void init(void)
{
    Serial1.begin(RADIO_BAUD);
    g_status = SUBSYS_OK;
    g_dropped = 0;
    g_radioSeq = 0;
    g_evHead = 0;
    g_evCount = 0;
}

/* Хватит ли места в буфере передатчика, чтобы отправить len байт
 * не блокируясь. Именно эта проверка и делает радиоканал безопасным
 * для полётного цикла. */
static bool canSend(uint8_t len)
{
    return Serial1.availableForWrite() >= (int)len;
}

static void flushEvents(void);

/* Контрольная сумма строки: CRC-8, полином 0x07. Дописывается как "*XX"
 * (два шестнадцатеричных знака), как в NMEA. Нужна, потому что порчу
 * в эфире иначе не отличить от настоящих данных: 29.09.2026 на стенде
 * порченый номер пакета прошёл на земле как валидный. */
static char *addCrc(char *p, char *e)
{
    static const char DIGITS[] = "0123456789ABCDEF";
    uint8_t crc = CmdFrame::crc8(g_buf, p);
    p = StrBuf::addChar(p, e, '*');
    p = StrBuf::addChar(p, e, DIGITS[crc >> 4]);
    p = StrBuf::addChar(p, e, DIGITS[crc & 0x0F]);
    return p;
}

/* Недавние события, которые досылаются ещё пару раз вслед за
 * телеметрией: одноразовая строка при порче пропадает насовсем, а по
 * контрольной сумме земля дубль опознает и лишний раз не покажет. */
#define EV_REPEATS   3
#define EV_REPEAT_GAP_MS 700UL   /* помеха от привода держится дольше 400 мс */
#define RECENT_SIZE  4

struct RecentEvent {
    uint32_t timeMs;
    uint8_t  ev;
    uint8_t  left;
    uint32_t lastMs;     /* когда уходила последняя копия */
};

/* Когда ушёл последний пакет. Повторы событий шлём из update() спустя
 * REPEAT_DELAY_MS: сразу после пакета (около 46 байт из 64) длинное
 * событие в буфер не влезает, а вместе с пакетом вытесняет его. */
#define REPEAT_DELAY_MS 60UL
static uint32_t g_lastPktMs = 0;

static RecentEvent g_recent[RECENT_SIZE];
static uint8_t g_recentNext = 0;

static bool trySendEvent(uint32_t timeMs, uint8_t ev);

static void resendRecent(void)
{
    for (uint8_t i = 0; i < RECENT_SIZE; i++) {
        RecentEvent &r = g_recent[(g_recentNext + i) % RECENT_SIZE];
        if (r.left > 0 && (uint32_t)(millis() - r.lastMs) >= EV_REPEAT_GAP_MS &&
            trySendEvent(r.timeMs, r.ev)) {
            r.left--;
            r.lastMs = millis();
            return;     /* по одному за такт, иначе вытесняют пакеты */
        }
    }
}

void sendRecord(const TelemetryRecord &rec)
{
    if (g_status != SUBSYS_OK)
        return;

    /* Отложенные события важнее очередной строки телеметрии: пусть
     * первыми займут место в буфере. */
    flushEvents();

    char *p = g_buf;
    char *e = g_buf + sizeof(g_buf);

    /* Состав пакета и разделитель заданы п. 22 ТЗ:
     * packet_id|time_ms|flight_state|altitude_m|pressure_pa|recovery_state|error_flags
     *
     * Собирается вручную, без snprintf_P: реализация printf в avr-libc
     * весит около полутора килобайт, и с ней набор D в плату не влезал.
     * Подробности в strbuf.h. */
    /* Номер увеличиваем заранее, но в эфир он уйдёт только если пакет
     * поместится в буфер. Пропущенный из-за переполнения пакет всё равно
     * считается потерянным, и разрыв номеров на земле это покажет —
     * так честнее, чем скрывать потерю. */
    p = StrBuf::addULong(p, e, (uint32_t)(++g_radioSeq));
    p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addULong(p, e, rec.timestamp_ms);  p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addProgmem(p, e, flightStateName(rec.flight_state));
    p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addFloat(p, e, rec.altitude_m, 2); p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addLong(p, e, rec.pressure_pa);    p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addProgmem(p, e, recoveryStateName(rec.recovery_state));
    p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addULong(p, e, rec.error_flags);
    p = addCrc(p, e);
    StrBuf::terminate(p);

    uint8_t len = (uint8_t)(p - g_buf);

    if (!canSend((uint8_t)(len + 2))) {
        g_dropped++;
        return;
    }

    Serial1.println(g_buf);
    g_lastPktMs = millis();
}

/* Собрать строку события и отправить, если она целиком помещается
 * в буфер передатчика. */
static bool trySendEvent(uint32_t timeMs, uint8_t ev)
{
    char *p = g_buf;
    char *e = g_buf + sizeof(g_buf);

    /* События помечаются решёткой, чтобы наземная программа отличала их
     * от строк телеметрии и не пыталась разбирать как пакет. */
    p = StrBuf::addChar(p, e, '#');
    p = StrBuf::addULong(p, e, timeMs);
    p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addProgmem(p, e, eventName(ev));
    p = addCrc(p, e);
    StrBuf::terminate(p);

    uint8_t len = (uint8_t)(p - g_buf);

    if (!canSend((uint8_t)(len + 2)))
        return false;

    Serial1.println(g_buf);
    return true;
}

static void flushEvents(void)
{
    while (g_evCount > 0) {
        const PendingEvent &pe = g_evq[g_evHead];
        if (!trySendEvent(pe.timeMs, pe.ev))
            return;
        g_evHead = (uint8_t)((g_evHead + 1) & (EVQ_SIZE - 1));
        g_evCount--;
    }
}

void sendEvent(uint32_t timeMs, uint8_t ev)
{
    if (g_status != SUBSYS_OK)
        return;

    /* Пока в очереди что-то есть, новое событие встаёт за ним, иначе
     * порядок на земле перепутается. */
    RecentEvent &rc = g_recent[g_recentNext];
    rc.timeMs = timeMs;
    rc.ev = ev;
    rc.left = EV_REPEATS;
    rc.lastMs = millis();
    g_recentNext = (uint8_t)((g_recentNext + 1) % RECENT_SIZE);

    flushEvents();
    if (g_evCount == 0 && trySendEvent(timeMs, ev))
        return;

    if (g_evCount == EVQ_SIZE) {
        g_dropped++;
        return;
    }

    PendingEvent &slot = g_evq[(g_evHead + g_evCount) & (EVQ_SIZE - 1)];
    slot.timeMs = timeMs;
    slot.ev = ev;
    g_evCount++;
}

#if HAS_RADIO_SERVICE

/* Приём кадров команд с земли. Читается в любом состоянии полёта, чтобы
 * буфер приёма не залипал старым, но выполнять команду можно только в
 * READY и пока она свежая: это решает Service::update и takeCommand. */
#define RX_MAX 16

static char     g_rx[RX_MAX];
static uint8_t  g_rxLen = 0;
static bool     g_cmdPending = false;
static uint8_t  g_cmdSeq = 0;
static char     g_cmdChar = 0;
static uint32_t g_cmdMs = 0;

static void pollRx(void)
{
    /* Не больше 32 байт за проход: полётный цикл не должен зависать
     * на потоке помех из эфира. */
    for (uint8_t guard = 32; guard > 0 && Serial1.available(); guard--) {
        char c = (char)Serial1.read();
        if (c == '\n') {
            uint8_t seq;
            char cmd;
            if (g_rxLen > 0 && CmdFrame::parse(g_rx, g_rxLen, seq, cmd)) {
                g_cmdSeq = seq;
                g_cmdChar = cmd;
                g_cmdMs = millis();
                g_cmdPending = true;
            }
            g_rxLen = 0;
        } else if (c == '\r') {
            /* пропускаем */
        } else if (c == '!') {
            g_rx[0] = c;          /* начало кадра: всё, что было до него, мусор */
            g_rxLen = 1;
        } else if (g_rxLen > 0 && g_rxLen < RX_MAX) {
            g_rx[g_rxLen++] = c;
        } else {
            g_rxLen = 0;
        }
    }
}

bool takeCommand(uint8_t &seq, char &cmd, uint32_t nowMs)
{
    if (!g_cmdPending)
        return false;
    g_cmdPending = false;
    if ((uint32_t)(nowMs - g_cmdMs) > 2000UL)
        return false;             /* устарела: в полёте такое исполнять нельзя */
    seq = g_cmdSeq;
    cmd = g_cmdChar;
    return true;
}

bool sendAck(uint8_t seq, char cmd)
{
    if (g_status != SUBSYS_OK)
        return false;

    char *p = g_buf;
    char *e = g_buf + sizeof(g_buf);
    p = StrBuf::addStr(p, e, "@ACK|");
    p = StrBuf::addULong(p, e, seq);
    p = StrBuf::addChar(p, e, '|');
    p = StrBuf::addChar(p, e, cmd);
    p = addCrc(p, e);
    StrBuf::terminate(p);

    if (!canSend((uint8_t)(p - g_buf + 2)))
        return false;
    Serial1.println(g_buf);
    return true;
}

#endif /* HAS_RADIO_SERVICE */

void update(void)
{
    if (g_status != SUBSYS_OK)
        return;
#if HAS_RADIO_SERVICE
    pollRx();
#endif
    flushEvents();

    uint32_t since = millis() - g_lastPktMs;
    if (since >= REPEAT_DELAY_MS && since < 2 * REPEAT_DELAY_MS)
        resendRecent();
}

#else  /* HAS_RADIO == 0 */

/* Набор без радиомодуля. Ни Serial1, ни буфер пакета в сборку не
 * попадают. Вызывающий код при этом не меняется: он вызывает те же
 * функции, просто они ничего не делают (пп. 23 и 36 ТЗ). */

void init(void)                           { g_status = SUBSYS_ABSENT; }
void sendRecord(const TelemetryRecord &)  { }
void sendEvent(uint32_t, uint8_t)         { }
void update(void)                         { }

#endif /* HAS_RADIO */

#if !HAS_RADIO_SERVICE
bool takeCommand(uint8_t &, char &, uint32_t)  { return false; }
bool sendAck(uint8_t, char)                     { return false; }
#endif

uint8_t status(void)
{
    return g_status;
}

uint16_t droppedCount(void)
{
    return g_dropped;
}

} /* namespace Radio */
