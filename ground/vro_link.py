"""
ВРО-1 / RocketBoard — связь с бортом и журналы CSV (без окна).

Здесь всё, что не связано с рисованием: разбор отладочной строки при
работе по проводу, работа с портом на чтение и запись, запись журналов
в CSV, команды привода. Окно (vro_station.py) только показывает и
нажимает кнопки, поэтому логика проверяется без экрана и без платы
(test_vro_link.py).
"""

import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import random

from telemetry import Session, Event, Packet, Ack, crc8


# --------------------------------------------------------------------
#  Отладочная строка при подключении по проводу (USB)
# --------------------------------------------------------------------

# Формат печатает printDebug() в firmware.ino, 5 раз в секунду:
#   15635 READY h=-0.06 max=0.00 |a|=1.01 p=99311 rec=ARMED z=1 vb=6.79 err=0x200 rdrop=0
# Поля vb= и rdrop= есть не во всех сборках, поэтому необязательные.
_DEBUG_RE = re.compile(
    r"^(?P<t>\d+)\s+(?P<state>[A-Z_]+)\s+"
    r"h=(?P<h>-?[\d.]+)\s+max=(?P<max>-?[\d.]+)\s+"
    r"\|a\|=(?P<a>-?[\d.]+)\s+p=(?P<p>-?\d+)\s+"
    r"rec=(?P<rec>[A-Z_]+)\s+z=(?P<z>\d)"
    r"(?:\s+vb=(?P<vb>-?[\d.]+))?"
    r"\s+err=0x(?P<err>[0-9A-Fa-f]+)"
    r"(?:\s+rdrop=(?P<rdrop>\d+))?\s*$"
)


@dataclass
class DebugSample:
    """Одна разобранная отладочная строка."""
    time_ms: int
    state: str
    altitude_m: float
    max_altitude_m: float
    accel_g: float
    pressure_pa: int
    recovery: str
    zero_set: bool
    vbat_v: Optional[float]
    error_flags: int
    rdrop: Optional[int]


def parse_debug_line(line: str) -> Optional[DebugSample]:
    """Разобрать отладочную строку. None, если это не она."""
    m = _DEBUG_RE.match(line.strip())
    if not m:
        return None
    try:
        return DebugSample(
            time_ms=int(m["t"]),
            state=m["state"],
            altitude_m=float(m["h"]),
            max_altitude_m=float(m["max"]),
            accel_g=float(m["a"]),
            pressure_pa=int(m["p"]),
            recovery=m["rec"],
            zero_set=m["z"] == "1",
            vbat_v=float(m["vb"]) if m["vb"] is not None else None,
            error_flags=int(m["err"], 16),
            rdrop=int(m["rdrop"]) if m["rdrop"] is not None else None,
        )
    except ValueError:
        return None


# --------------------------------------------------------------------
#  Команды привода (сервисный режим борта, п. 40 ТЗ)
# --------------------------------------------------------------------

# Команды принимаются только в состоянии READY: в воздухе порт не читается.
CMD_SAFE = b"s"
CMD_DEPLOY = b"d"
CMD_CYCLE = b"t"      # SAFE -> DEPLOYED -> SAFE
CMD_SIM = b"r"        # прогон тестового профиля полёта
CMD_ZERO = b"z"       # предполётное обнуление высоты (борт неподвижен ~8 с)
CMD_READY = b"R"      # явный возврат в READY из «посадки» (только оператор)
CMD_EMERGENCY = b"D"   # аварийное раскрытие парашюта (борт принимает и в полёте)
CMD_INFO = b"i"
CMD_HELP = b"?"

ANGLE_STEP = 20       # борт понимает цифры 0..9 как угол 0..180 с шагом 20
ANGLE_MAX = 180


def angle_command(angle_deg: int) -> bytes:
    """Команда на ближайший угол, который умеет ставить борт."""
    angle = max(0, min(ANGLE_MAX, int(angle_deg)))
    return str(round(angle / ANGLE_STEP)).encode()


def angle_actual(angle_deg: int) -> int:
    """Угол, который реально будет выставлен (кратный шагу)."""
    return int(angle_command(angle_deg).decode()) * ANGLE_STEP


# --------------------------------------------------------------------
#  Команды по радио (отладка без провода)
# --------------------------------------------------------------------

# Разрешённые борту команды: привод, цикл, прогон профиля (cmdframe.h).
RADIO_CMDS = set("sdtrzRD0123456789")


def build_command_frame(seq: int, cmd: str) -> bytes:
    """Кадр "!номер|команда*XX\\n": XX это CRC-8 от всего до '*'."""
    body = f"!{seq & 0xFF}|{cmd}"
    return f"{body}*{crc8(body.encode('ascii')):02X}\n".encode("ascii")


class RadioCommander:
    """
    Отправка команды борту по полудуплексному каналу.

    HC-12 не может одновременно слушать и передавать, поэтому кадр с земли
    иногда теряется в столкновении с телеметрией. Кадр повторяется каждые
    RETRY_S секунд, пока не придёт подтверждение от борта, но не более
    MAX_TRIES раз. Борт выполняет команду один раз (повтор того же номера
    только подтверждает). Чистая логика без порта и окна: send передаётся
    снаружи, время подаётся в tick().
    """

    RETRY_S = 0.6
    MAX_TRIES = 6
    # Аварийное раскрытие важнее всего: повторяем чаще и дольше (до 12 с).
    EMERGENCY_RETRY_S = 0.4
    EMERGENCY_TRIES = 30

    def _retry_s(self, cmd: str) -> float:
        return self.EMERGENCY_RETRY_S if cmd == "D" else self.RETRY_S

    def _max_tries(self, cmd: str) -> int:
        return self.EMERGENCY_TRIES if cmd == "D" else self.MAX_TRIES

    def __init__(self, send, seq_start: Optional[int] = None) -> None:
        self._send = send
        # Случайное начало: после перезапуска станции номер не совпадёт с
        # последним выполненным бортом.
        self._seq = random.randint(1, 200) if seq_start is None else seq_start
        self.pending: Optional[Dict[str, object]] = None
        self.status = ""
        self.last_cmd = ""

    def submit(self, cmd: str, now: float) -> None:
        if cmd not in RADIO_CMDS:
            raise ValueError(f"команда {cmd!r} по радио не передаётся")
        self._seq = (self._seq + 1) & 0xFF
        self.last_cmd = cmd
        self.pending = {"seq": self._seq, "cmd": cmd, "tries": 0, "next_at": now}
        self.tick(now)

    def tick(self, now: float) -> None:
        p = self.pending
        if p is None or now < p["next_at"]:
            return
        cmd = str(p["cmd"])
        if p["tries"] >= self._max_tries(cmd):
            self.status = (f"команда {cmd}: НЕТ ПОДТВЕРЖДЕНИЯ. Борт не в READY, "
                           "вне зоны или не принимает команды")
            if cmd == "D":
                self.status = ("АВАРИЙНОЕ РАСКРЫТИЕ: НЕТ ПОДТВЕРЖДЕНИЯ. Борт вне зоны, "
                               "выключен или без радиокоманд (набор c_radio). "
                               "Нажмите ещё раз или откройте парашют другим способом")
            self.pending = None
            return
        self._send(build_command_frame(int(p["seq"]), str(p["cmd"])))
        p["tries"] = int(p["tries"]) + 1
        p["next_at"] = now + self._retry_s(cmd)
        self.status = (f"команда {cmd}: отправлена {p['tries']}/{self._max_tries(cmd)}, "
                       "ждём подтверждение")
        if cmd == "D":
            self.status = (f"АВАРИЙНОЕ РАСКРЫТИЕ: отправлено {p['tries']}/"
                           f"{self._max_tries(cmd)}, ждём подтверждение борта")

    def on_ack(self, seq: int) -> bool:
        """Подтверждение от борта. True, если оно относится к текущей команде."""
        p = self.pending
        if p is not None and p["seq"] == seq:
            self.status = f"команда {p['cmd']}: ПОДТВЕРЖДЕНО бортом"
            if p["cmd"] == "D":
                self.status = "АВАРИЙНОЕ РАСКРЫТИЕ: ПОДТВЕРЖДЕНО бортом, парашют открывается"
            self.pending = None
            return True
        return False


# --------------------------------------------------------------------
#  Порт: чтение в потоке и запись команд
# --------------------------------------------------------------------

class PortLink(threading.Thread):
    """
    Порт в отдельном потоке: строки уходят в очередь, команды пишутся
    из любого потока. Обрыв связи не роняет программу (п. 45 ТЗ).

    В очередь кладутся кортежи (вид, данные): opened, line, error, closed.
    """

    def __init__(self, port: str, baud: int, out: "queue.Queue") -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.out = out
        self._halt = threading.Event()
        self._ser = None
        self._wlock = threading.Lock()

    def stop(self) -> None:
        self._halt.set()

    def send(self, data: bytes) -> bool:
        """Записать команду в порт. False, если порт не открыт."""
        with self._wlock:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write(data)
                ser.flush()
                return True
            except Exception as exc:
                self.out.put(("error", f"Не удалось записать в порт: {exc}"))
                return False

    def run(self) -> None:
        try:
            import serial
        except ImportError:
            self.out.put(("error", "Не установлен пакет pyserial. "
                                   "Выполните: pip install pyserial"))
            return

        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.3)
        except Exception as exc:
            self.out.put(("error", f"Не удалось открыть порт {self.port}: {exc}"))
            return

        self._ser = ser
        self.out.put(("opened", self.port))

        try:
            buf = b""
            while not self._halt.is_set():
                try:
                    chunk = ser.read(256)
                except Exception as exc:
                    self.out.put(("error", f"Связь с портом потеряна: {exc}"))
                    break
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.decode("utf-8", errors="replace").strip("\r\x00 ")
                    if text:
                        self.out.put(("line", text))
        finally:
            with self._wlock:
                self._ser = None
            try:
                ser.close()
            except Exception:
                pass
            self.out.put(("closed", self.port))


# --------------------------------------------------------------------
#  Журналы CSV
# --------------------------------------------------------------------

class CsvLog:
    """
    Журнал в CSV: одна строка на всё, что пришло. Файл сбрасывается на
    диск после каждой строки: если программу закроют или компьютер
    выключат, записанное не пропадёт.

    Кодировка utf-8 с BOM, чтобы Excel в Windows не ломал русский текст.
    Разделитель запятая, как в журнале на карте памяти.
    """

    def __init__(self, directory: str, prefix: str, columns) -> None:
        import csv
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(directory, f"{prefix}_{stamp}.csv")
        self.columns = list(columns)
        self.rows = 0
        self._fh = open(self.path, "w", encoding="utf-8-sig", newline="")
        self._csv = csv.DictWriter(self._fh, fieldnames=self.columns,
                                   extrasaction="ignore")
        self._csv.writeheader()
        self._fh.flush()

    def write(self, row: Dict[str, object]) -> None:
        self._csv.writerow(row)
        self._fh.flush()
        self.rows += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def _pc_time() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


RADIO_COLUMNS = ["pc_time", "kind", "seq", "time_ms", "state", "altitude_m",
                 "pressure_pa", "recovery", "error_flags", "raw"]

DEBUG_COLUMNS = ["pc_time", "kind", "time_ms", "state", "altitude_m",
                 "max_altitude_m", "accel_g", "pressure_pa", "recovery",
                 "zero_set", "vbat_v", "error_flags", "rdrop", "raw"]


def radio_row(session: Session, line: str) -> Dict[str, object]:
    """
    Скормить строку сеансу и вернуть строку журнала. kind:
      packet       новый пакет телеметрии
      duplicate    повтор уже принятого номера
      event        событие
      event_dup    повтор уже принятого события
      bad          испорчена (не сошлась CRC или не разобрать)
      raw          что-то иное (например, текст HC-12)
    """
    before = (session.received, session.duplicates, session.bad_lines,
              len(session.events))
    item = session.feed(line)
    row: Dict[str, object] = {"pc_time": _pc_time(), "raw": line}

    if session.bad_lines > before[2]:
        # Текст без разделителей (например, ответ HC-12) не «порча».
        looks_like_data = "|" in line or "*" in line or line.startswith("#")
        row["kind"] = "bad" if looks_like_data else "raw"
    elif session.duplicates > before[1]:
        row["kind"] = "duplicate"
        if session.last_packet is not None:
            row["seq"] = session.last_packet.seq
    elif isinstance(item, Packet):
        row.update(kind="packet", seq=item.seq, time_ms=item.time_ms,
                   state=item.state, altitude_m=item.altitude_m,
                   pressure_pa=item.pressure_pa, recovery=item.recovery,
                   error_flags=item.error_flags)
    elif isinstance(item, Ack):
        row.update(kind="ack", seq=item.seq, state=item.cmd)
    elif isinstance(item, Event):
        row.update(kind="event", time_ms=item.time_ms, state=item.name)
    elif line.startswith("#"):
        row["kind"] = "event_dup"
    else:
        row["kind"] = "raw"
    return row


def debug_row(line: str) -> Dict[str, object]:
    """Строка журнала для режима по проводу."""
    row: Dict[str, object] = {"pc_time": _pc_time(), "raw": line}
    s = parse_debug_line(line)
    if s is None:
        row["kind"] = "text"
        return row
    row.update(kind="debug", time_ms=s.time_ms, state=s.state,
               altitude_m=s.altitude_m, max_altitude_m=s.max_altitude_m,
               accel_g=s.accel_g, pressure_pa=s.pressure_pa,
               recovery=s.recovery, zero_set=int(s.zero_set),
               vbat_v=s.vbat_v if s.vbat_v is not None else "",
               error_flags=s.error_flags,
               rdrop=s.rdrop if s.rdrop is not None else "")
    return row


def sent_row(command: bytes) -> Dict[str, object]:
    """Запись в журнал о команде, ушедшей на борт (в обоих режимах)."""
    return {"pc_time": _pc_time(), "kind": "command",
            "raw": command.decode("ascii", errors="replace")}
