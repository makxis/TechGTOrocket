"""
ВРО-1 / RocketBoard — разбор телеметрии и учёт пакетов.

Здесь нет ни интерфейса, ни работы с последовательным портом: только
разбор строк и подсчёт статистики. Так эту часть можно проверить
тестами, не имея ни платы, ни радиомодуля, ни экрана.

Формат радиопакета задан п. 22 ТЗ:

    номер|время_мс|СОСТОЯНИЕ|высота|давление|СПАСЕНИЕ|флаги
    642|12840|COAST|87.42|99584|ARMED|0

События по п. 33 ТЗ начинаются с решётки:

    #12840|APOGEE_CONFIRMED

Борт разделяет поля вертикальной чертой в радиоканале и запятой в
журнале на карте. Принимаются оба варианта: это позволяет одной и той
же программой разбирать и эфир, и файл, снятый с карты после полёта.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict

# --------------------------------------------------------------------
#  Диагностические флаги (п. 28 ТЗ)
# --------------------------------------------------------------------

ERROR_FLAGS: Dict[int, str] = {
    0x0001: "отказ инициализации датчиков",
    0x0002: "отказ барометра",
    0x0004: "отказ инерциального модуля",
    0x0008: "карта памяти не инициализирована",
    0x0010: "ошибка записи на карту",
    0x0020: "ошибка радиоканала",
    0x0040: "ошибка сервопривода",
    0x0080: "нарушение таймингов",
    0x0100: "большой разброс при калибровке давления",
}

# Ошибки, при которых полёт невозможен (ERROR_CRITICAL_MASK в types.h).
CRITICAL_MASK = 0x0001 | 0x0002 | 0x0004

FLIGHT_STATES = (
    "INIT", "READY", "BOOST", "COAST",
    "APOGEE", "DESCENT", "RECOVERY", "LANDED", "ERROR",
)

RECOVERY_STATES = ("SAFE", "ARMED", "DEPLOYED", "ERROR")

# Человеческие названия для показа школьнику: п. 24 ТЗ требует
# понятного интерфейса, а English-only состояния таковым не являются.
STATE_RU = {
    "INIT":     "инициализация",
    "READY":    "готовность",
    "BOOST":    "разгон",
    "COAST":    "подъём по инерции",
    "APOGEE":   "апогей",
    "DESCENT":  "снижение",
    "RECOVERY": "снижение на парашюте",
    "LANDED":   "посадка",
    "ERROR":    "ОШИБКА",
}

RECOVERY_RU = {
    "SAFE":     "безопасно",
    "ARMED":    "взведена",
    "DEPLOYED": "РАСКРЫТА",
    "ERROR":    "ОШИБКА",
}

# Номер пакета на борту 16-разрядный и переполняется.
SEQ_MODULO = 65536

# Разрыв больше этого считаем не потерей, а перезапуском борта:
# терять полтысячи пакетов подряд радиоканал может только вместе с
# ракетой, а вот перезагрузка платы — дело обычное.
RESTART_GAP = 500


def describe_errors(flags: int) -> List[str]:
    """Расшифровка битовой маски ошибок в список понятных строк."""
    if flags == 0:
        return []
    return [text for bit, text in ERROR_FLAGS.items() if flags & bit]


@dataclass
class Packet:
    """Разобранный пакет телеметрии."""
    seq: int
    time_ms: int
    state: str
    altitude_m: float
    pressure_pa: int
    recovery: str
    error_flags: int

    @property
    def is_critical(self) -> bool:
        return bool(self.error_flags & CRITICAL_MASK)

    @property
    def state_ru(self) -> str:
        return STATE_RU.get(self.state, self.state)

    @property
    def recovery_ru(self) -> str:
        return RECOVERY_RU.get(self.recovery, self.recovery)


@dataclass
class Event:
    """Событие полёта (п. 33 ТЗ)."""
    time_ms: int
    name: str


class ParseError(Exception):
    """Строка получена, но разобрать её не удалось."""


def _split(text: str) -> List[str]:
    """Разделить поля. Борт использует '|' в эфире и ',' в журнале."""
    return text.split("|") if "|" in text else text.split(",")


def parse_line(line: str) -> Optional[object]:
    """
    Разобрать одну строку.

    Возвращает Packet, Event или None, если строка пустая либо это
    заголовок CSV. Бросает ParseError, если строка похожа на данные,
    но разобрать её не вышло.
    """
    line = line.strip().strip("\x00")
    if not line:
        return None

    # Заголовок CSV из журнала на карте.
    if line.startswith("time_ms"):
        return None

    # Событие.
    if line.startswith("#"):
        parts = _split(line[1:])
        if len(parts) < 2:
            raise ParseError(f"событие без имени: {line!r}")
        try:
            return Event(time_ms=int(parts[0]), name=parts[1].strip())
        except ValueError as exc:
            raise ParseError(f"неверное время в событии: {line!r}") from exc

    parts = _split(line)

    # Строка из журнала на карте содержит 16 полей (п. 19 ТЗ).
    # Приводим её к тем же семи, что и радиопакет.
    if len(parts) >= 16:
        try:
            return Packet(
                seq=int(parts[2]),
                time_ms=int(parts[0]),
                state=parts[3].strip(),
                altitude_m=float(parts[6]),
                pressure_pa=int(parts[4]),
                recovery=parts[14].strip(),
                error_flags=int(parts[15]),
            )
        except (ValueError, IndexError) as exc:
            raise ParseError(f"не разобрать строку журнала: {line!r}") from exc

    if len(parts) != 7:
        raise ParseError(f"ожидалось 7 полей, получено {len(parts)}: {line!r}")

    try:
        return Packet(
            seq=int(parts[0]),
            time_ms=int(parts[1]),
            state=parts[2].strip(),
            altitude_m=float(parts[3]),
            pressure_pa=int(parts[4]),
            recovery=parts[5].strip(),
            error_flags=int(parts[6]),
        )
    except ValueError as exc:
        raise ParseError(f"не разобрать пакет: {line!r}") from exc


def seq_gap(previous: int, current: int) -> int:
    """
    Сколько пакетов пропущено между двумя номерами.

    Ноль означает, что пропусков нет. Учитывается переполнение
    16-разрядного счётчика на борту.
    """
    return (current - previous - 1) % SEQ_MODULO


class Session:
    """
    Учёт одного сеанса приёма.

    Считает принятые и потерянные пакеты (п. 24 и п. 45 ТЗ), хранит
    последние значения для показа и накапливает события.
    """

    def __init__(self) -> None:
        self.received = 0
        self.lost = 0
        self.bad_lines = 0
        self.restarts = 0

        self.last_seq: Optional[int] = None
        self.last_packet: Optional[Packet] = None
        self.max_altitude: float = 0.0

        self.events: List[Event] = []
        self.packets: List[Packet] = []
        self.problems: List[str] = []

    # ----------------------------------------------------------------

    def feed(self, line: str) -> Optional[object]:
        """
        Скормить строку. Возвращает то, что из неё получилось,
        либо None. Ошибки разбора не выбрасываются наружу: обрыв в
        эфире — обычное дело, и ронять из-за него программу нельзя.
        """
        try:
            item = parse_line(line)
        except ParseError as exc:
            self.bad_lines += 1
            self.problems.append(str(exc))
            return None

        if item is None:
            return None

        if isinstance(item, Event):
            self.events.append(item)
            return item

        self._account(item)
        return item

    # ----------------------------------------------------------------

    def _account(self, pkt: Packet) -> None:
        if self.last_seq is not None:
            gap = seq_gap(self.last_seq, pkt.seq)

            if gap >= RESTART_GAP:
                # Скорее всего борт перезапустился, а не потерялись
                # полтысячи пакетов. Считать это потерей — врать в
                # статистике.
                self.restarts += 1
                self.problems.append(
                    f"похоже на перезапуск борта: номер {self.last_seq} -> {pkt.seq}"
                )
            elif gap > 0:
                self.lost += gap

        self.last_seq = pkt.seq
        self.last_packet = pkt
        self.received += 1
        self.packets.append(pkt)

        if pkt.altitude_m > self.max_altitude:
            self.max_altitude = pkt.altitude_m

    # ----------------------------------------------------------------

    @property
    def expected(self) -> int:
        """Сколько пакетов должно было прийти."""
        return self.received + self.lost

    @property
    def loss_percent(self) -> float:
        if self.expected == 0:
            return 0.0
        return 100.0 * self.lost / self.expected

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]
