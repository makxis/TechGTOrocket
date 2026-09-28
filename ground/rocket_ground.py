#!/usr/bin/env python3
"""
ВРО-1 / RocketBoard — наземная станция приёма телеметрии.

Пункты 24, 25 и 45 ТЗ.

Принимает телеметрию от HC-12 через переходник FT232RL, показывает
состояние ракеты, ведёт учёт принятых и потерянных пакетов и
автоматически сохраняет всё принятое в файл.

Запуск с окном:

    python3 rocket_ground.py

Запуск без окна, для проверки и для работы по SSH:

    python3 rocket_ground.py --console --port /dev/ttyUSB0

Требуется пакет pyserial:

    pip install pyserial
"""

import argparse
import os
import queue
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List

# pyserial лежит в проекте (vendor/), чтобы не нужны были pip и интернет.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from telemetry import Session, Packet, Event, describe_errors

# --------------------------------------------------------------------
#  Работа с портом
# --------------------------------------------------------------------

# Строка считается устаревшей, если пакетов нет дольше этого времени.
# Борт передаёт 5 раз в секунду, поэтому полторы секунды тишины — это
# уже потеря связи, а не задержка.
DATA_TIMEOUT_S = 1.5

# Приметы переходников USB-UART в описании порта. Нужны, чтобы в списке
# сразу подсветить нужный порт: п. 45 ТЗ требует «видеть FT232RL».
USB_UART_HINTS = ("FT232", "FTDI", "CP210", "CH340", "CH341", "USB-SERIAL", "USB Serial")


def list_ports() -> List[tuple]:
    """
    Список доступных портов: (имя, описание, похож ли на переходник).
    Пустой список, если pyserial не установлен.
    """
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []

    result = []
    for p in lp.comports():
        text = f"{p.description} {p.manufacturer or ''}".upper()
        likely = any(h.upper() in text for h in USB_UART_HINTS)
        # Вторая плата RocketBoard со скетчем hc12_setup тоже годится как
        # приёмник; в Windows без драйвера Arduino её имя ничего не говорит,
        # поэтому узнаём её по VID: Arduino, SparkFun, FTDI.
        if getattr(p, "vid", None) in (0x2341, 0x1B4F, 0x2A03, 0x0403):
            likely = True
        result.append((p.device, p.description or p.device, likely))

    # Похожие на переходник — наверх, чтобы школьник не гадал.
    result.sort(key=lambda r: (not r[2], r[0]))
    return result


class SerialReader(threading.Thread):
    """
    Чтение порта в отдельном потоке.

    Строки складываются в очередь, интерфейс забирает их оттуда. Так
    окно не подвисает, пока порт молчит, и обрыв связи не роняет
    программу — этого требует п. 45 ТЗ.
    """

    def __init__(self, port: str, baud: int, out: "queue.Queue") -> None:
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.out = out
        self._stop = threading.Event()
        self.error: Optional[str] = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            import serial
        except ImportError:
            self.out.put(("error", "Не установлен пакет pyserial. "
                                   "Выполните: pip install pyserial"))
            return

        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.5)
        except Exception as exc:
            self.out.put(("error", f"Не удалось открыть порт {self.port}: {exc}"))
            return

        self.out.put(("opened", self.port))

        try:
            buf = b""
            while not self._stop.is_set():
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
            try:
                ser.close()
            except Exception:
                pass
            self.out.put(("closed", self.port))


# --------------------------------------------------------------------
#  Сохранение принятого
# --------------------------------------------------------------------

class FlightLog:
    """
    Сохранение всего принятого в файл (п. 24 ТЗ).

    Пишется всё подряд, включая мусор из эфира: при разборе полёта
    важно видеть и то, что не удалось разобрать.
    """

    def __init__(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(directory, f"flight_{stamp}.txt")
        self._fh = open(self.path, "w", encoding="utf-8")
        self._fh.write(f"# приём начат {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        self._fh.flush()

    def write(self, line: str) -> None:
        self._fh.write(line + "\n")
        # Сбрасываем сразу: если программу закроют или компьютер
        # выключат, записанное не должно пропасть.
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.write(f"# приём завершён {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------
#  Режим без окна
# --------------------------------------------------------------------

def run_console(port: str, baud: int, log_dir: str, seconds: Optional[int]) -> int:
    """Приём с выводом в терминал. Нужен для проверки и работы по SSH."""
    session = Session()
    q: "queue.Queue" = queue.Queue()
    reader = SerialReader(port, baud, q)
    log = FlightLog(log_dir)

    print(f"Порт:   {port} @ {baud}")
    print(f"Журнал: {log.path}")
    print("Останов: Ctrl+C\n")

    reader.start()
    started = time.time()
    last_report = 0.0
    last_data = time.time()
    failed = False

    try:
        while True:
            if seconds is not None and time.time() - started > seconds:
                break

            try:
                kind, payload = q.get(timeout=0.2)
            except queue.Empty:
                kind, payload = None, None

            if kind == "error":
                # Не выходим сразу: итог сеанса нужен и после обрыва,
                # иначе принятое до обрыва останется непосчитанным.
                print(f"ОШИБКА: {payload}")
                failed = True
                break
            elif kind == "opened":
                print(f"порт открыт: {payload}")
            elif kind == "closed":
                print(f"порт закрыт: {payload}")
                break
            elif kind == "line":
                log.write(payload)
                last_data = time.time()
                item = session.feed(payload)
                if isinstance(item, Event):
                    print(f"  СОБЫТИЕ  {item.time_ms:>8} мс  {item.name}")

            now = time.time()
            if now - last_report >= 1.0:
                last_report = now
                p = session.last_packet
                if p is not None:
                    # Показываем не только значения, но и свежесть:
                    # п. 24 ТЗ требует отображать наличие потока данных.
                    silent = now - last_data
                    mark = "" if silent <= DATA_TIMEOUT_S \
                        else f"   [нет данных {silent:.0f} с]"
                    print(f"{p.state_ru:<20} h={p.altitude_m:7.2f} м  "
                          f"макс={session.max_altitude:7.2f} м  "
                          f"p={p.pressure_pa} Па  "
                          f"спасение={p.recovery_ru:<12} "
                          f"принято={session.received} потеряно={session.lost} "
                          f"({session.loss_percent:.1f}%){mark}")
    except KeyboardInterrupt:
        print("\nостановлено оператором")
    finally:
        reader.stop()
        log.close()

    print(f"\nИтог: принято {session.received}, потеряно {session.lost} "
          f"({session.loss_percent:.1f}%), нечитаемых строк {session.bad_lines}")
    print(f"Максимальная высота: {session.max_altitude:.2f} м")
    print(f"Журнал сохранён: {log.path}")
    return 1 if failed else 0


# --------------------------------------------------------------------
#  Окно
# --------------------------------------------------------------------

def run_gui(baud: int, log_dir: str) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("Не найден tkinter. В Debian и Ubuntu: apt install python3-tk")
        return 1

    BG = "#101418"
    FG = "#e6edf3"
    DIM = "#8b949e"
    OK = "#3fb950"
    WARN = "#d29922"
    BAD = "#f85149"

    root = tk.Tk()
    root.title("ВРО-1 — наземная станция")
    root.configure(bg=BG)
    root.minsize(860, 620)

    session = Session()
    q: "queue.Queue" = queue.Queue()
    state = {"reader": None, "log": None, "last_data": 0.0, "connected": False}

    # ---------------- верхняя строка: порт и подключение -------------

    top = tk.Frame(root, bg=BG)
    top.pack(fill="x", padx=14, pady=(14, 6))

    tk.Label(top, text="Порт:", bg=BG, fg=FG,
             font=("TkDefaultFont", 11)).pack(side="left")

    port_var = tk.StringVar()
    port_box = ttk.Combobox(top, textvariable=port_var, width=42, state="readonly")
    port_box.pack(side="left", padx=8)

    def refresh_ports(select_first: bool = True) -> None:
        ports = list_ports()
        if not ports:
            port_box["values"] = ["(портов не найдено)"]
            port_box.current(0)
            return

        labels = [f"{dev} — {desc}" + ("   [переходник]" if likely else "")
                  for dev, desc, likely in ports]
        port_box["values"] = labels
        if select_first and labels:
            port_box.current(0)

    def selected_port() -> Optional[str]:
        text = port_var.get()
        if not text or text.startswith("("):
            return None
        return text.split(" — ")[0].strip()

    tk.Button(top, text="Обновить", command=refresh_ports,
              bg="#21262d", fg=FG, relief="flat", padx=12).pack(side="left")

    connect_btn = tk.Button(top, text="Подключиться", bg="#238636", fg="white",
                            relief="flat", padx=20, pady=4,
                            font=("TkDefaultFont", 11, "bold"))
    connect_btn.pack(side="left", padx=10)

    # ---------------- индикатор потока -------------------------------

    flow = tk.Frame(root, bg=BG)
    flow.pack(fill="x", padx=14)

    flow_dot = tk.Label(flow, text="●", bg=BG, fg=DIM, font=("TkDefaultFont", 16))
    flow_dot.pack(side="left")

    flow_text = tk.Label(flow, text="не подключено", bg=BG, fg=DIM,
                         font=("TkDefaultFont", 11))
    flow_text.pack(side="left", padx=6)

    # ---------------- крупные показания ------------------------------

    tiles = tk.Frame(root, bg=BG)
    tiles.pack(fill="x", padx=14, pady=12)

    def make_tile(parent, caption, col, big=False):
        f = tk.Frame(parent, bg="#161b22", padx=14, pady=10)
        f.grid(row=0, column=col, sticky="nsew", padx=4)
        parent.grid_columnconfigure(col, weight=1)
        tk.Label(f, text=caption, bg="#161b22", fg=DIM,
                 font=("TkDefaultFont", 9)).pack(anchor="w")
        v = tk.Label(f, text="—", bg="#161b22", fg=FG,
                     font=("TkDefaultFont", 22 if big else 16, "bold"))
        v.pack(anchor="w")
        return v

    val_state = make_tile(tiles, "СОСТОЯНИЕ", 0, big=True)
    val_alt = make_tile(tiles, "ВЫСОТА, м", 1, big=True)
    val_max = make_tile(tiles, "МАКСИМУМ, м", 2)
    val_press = make_tile(tiles, "ДАВЛЕНИЕ, Па", 3)
    val_rec = make_tile(tiles, "СИСТЕМА СПАСЕНИЯ", 4)

    # ---------------- счётчики пакетов -------------------------------

    stats = tk.Frame(root, bg=BG)
    stats.pack(fill="x", padx=14)

    stats_label = tk.Label(stats, text="принято 0   потеряно 0   нечитаемых 0",
                           bg=BG, fg=DIM, font=("TkDefaultFont", 11))
    stats_label.pack(side="left")

    # ---------------- ошибки -----------------------------------------

    err_label = tk.Label(root, text="", bg=BG, fg=BAD, anchor="w", justify="left",
                         font=("TkDefaultFont", 11, "bold"))
    err_label.pack(fill="x", padx=14, pady=(8, 0))

    # ---------------- журнал событий ---------------------------------

    tk.Label(root, text="События полёта", bg=BG, fg=DIM,
             anchor="w").pack(fill="x", padx=14, pady=(12, 2))

    log_frame = tk.Frame(root, bg=BG)
    log_frame.pack(fill="both", expand=True, padx=14, pady=(0, 6))

    log_box = tk.Text(log_frame, bg="#0d1117", fg=FG, relief="flat",
                      font=("TkFixedFont", 10), height=12, wrap="none")
    log_box.pack(side="left", fill="both", expand=True)

    scroll = tk.Scrollbar(log_frame, command=log_box.yview)
    scroll.pack(side="right", fill="y")
    log_box.config(yscrollcommand=scroll.set)

    def log_line(text: str, colour: str = FG) -> None:
        log_box.insert("end", text + "\n")
        log_box.tag_add(colour, "end-2l", "end-1l")
        log_box.tag_config(colour, foreground=colour)
        log_box.see("end")

    # ---------------- нижняя строка ----------------------------------

    bottom = tk.Frame(root, bg=BG)
    bottom.pack(fill="x", padx=14, pady=(0, 14))

    file_label = tk.Label(bottom, text="журнал не начат", bg=BG, fg=DIM,
                          font=("TkDefaultFont", 9))
    file_label.pack(side="left")

    def open_log_folder():
        path = os.path.abspath(log_dir)
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)            # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
        except Exception as exc:
            messagebox.showerror("Не удалось открыть папку", str(exc))

    tk.Button(bottom, text="Открыть папку с журналами", command=open_log_folder,
              bg="#21262d", fg=FG, relief="flat", padx=12).pack(side="right")

    # ---------------- подключение и отключение -----------------------

    def do_connect() -> None:
        port = selected_port()
        if port is None:
            messagebox.showwarning(
                "Порт не выбран",
                "Подключите переходник FT232RL и нажмите «Обновить».")
            return

        state["log"] = FlightLog(log_dir)
        file_label.config(text=f"журнал: {state['log'].path}")

        reader = SerialReader(port, baud, q)
        reader.start()
        state["reader"] = reader
        state["connected"] = True
        connect_btn.config(text="Отключиться", bg="#8b2c2c")
        port_box.config(state="disabled")
        log_line(f"[{datetime.now():%H:%M:%S}] подключение к {port}", DIM)

    def do_disconnect(reason: str = "") -> None:
        if state["reader"] is not None:
            state["reader"].stop()
            state["reader"] = None
        if state["log"] is not None:
            state["log"].close()
            state["log"] = None

        state["connected"] = False
        connect_btn.config(text="Подключиться", bg="#238636")
        port_box.config(state="readonly")
        flow_dot.config(fg=DIM)
        flow_text.config(text="не подключено", fg=DIM)
        if reason:
            log_line(f"[{datetime.now():%H:%M:%S}] {reason}", WARN)

    def toggle() -> None:
        if state["connected"]:
            do_disconnect("отключено оператором")
        else:
            do_connect()

    connect_btn.config(command=toggle)

    # ---------------- обновление экрана ------------------------------

    def drain_queue() -> None:
        handled = 0
        while handled < 400:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                break
            handled += 1

            if kind == "error":
                log_line(f"[{datetime.now():%H:%M:%S}] {payload}", BAD)
                do_disconnect()
                messagebox.showerror("Ошибка связи", payload)

            elif kind == "opened":
                log_line(f"[{datetime.now():%H:%M:%S}] порт открыт", OK)

            elif kind == "closed":
                if state["connected"]:
                    do_disconnect("связь потеряна")

            elif kind == "line":
                if state["log"] is not None:
                    state["log"].write(payload)
                state["last_data"] = time.time()

                item = session.feed(payload)
                if isinstance(item, Event):
                    log_line(f"{item.time_ms:>9} мс   {item.name}", WARN)

    def refresh_display() -> None:
        drain_queue()

        # --- индикатор потока (п. 24 ТЗ) ---
        if state["connected"]:
            silent = time.time() - state["last_data"]
            if state["last_data"] == 0.0:
                flow_dot.config(fg=WARN)
                flow_text.config(text="порт открыт, данных пока нет", fg=WARN)
            elif silent > DATA_TIMEOUT_S:
                flow_dot.config(fg=BAD)
                flow_text.config(
                    text=f"данные не поступают {silent:.0f} с", fg=BAD)
            else:
                flow_dot.config(fg=OK)
                flow_text.config(text="данные поступают", fg=OK)

        # --- показания ---
        p = session.last_packet
        if p is not None:
            val_state.config(text=p.state_ru,
                             fg=BAD if p.state == "ERROR" else FG)
            val_alt.config(text=f"{p.altitude_m:.2f}")
            val_max.config(text=f"{session.max_altitude:.2f}")
            val_press.config(text=f"{p.pressure_pa}")
            val_rec.config(text=p.recovery_ru,
                           fg=OK if p.recovery == "DEPLOYED" else FG)

            errors = describe_errors(p.error_flags)
            if errors:
                err_label.config(
                    text=("КРИТИЧЕСКАЯ ОШИБКА: " if p.is_critical else "Ошибки: ")
                         + "; ".join(errors),
                    fg=BAD if p.is_critical else WARN)
            else:
                err_label.config(text="")

        stats_label.config(
            text=f"принято {session.received}   "
                 f"потеряно {session.lost} ({session.loss_percent:.1f}%)   "
                 f"нечитаемых {session.bad_lines}")

        root.after(200, refresh_display)

    def on_close() -> None:
        do_disconnect()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh_ports()
    if not list_ports():
        log_line("Портов не найдено. Подключите переходник FT232RL "
                 "и нажмите «Обновить».", WARN)
    log_line("Порядок работы: выбрать порт, нажать «Подключиться», "
             "убедиться что индикатор зелёный.", DIM)

    refresh_display()
    root.mainloop()
    return 0


# --------------------------------------------------------------------

def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))

    ap = argparse.ArgumentParser(
        description="Наземная станция приёма телеметрии ВРО-1")
    ap.add_argument("--console", action="store_true",
                    help="работать без окна, выводить в терминал")
    ap.add_argument("--port", help="имя порта, например /dev/ttyUSB0 или COM3")
    ap.add_argument("--baud", type=int, default=9600,
                    help="скорость порта, по умолчанию 9600 (заводская у HC-12)")
    ap.add_argument("--log-dir", default=os.path.join(here, "logs"),
                    help="куда сохранять принятое")
    ap.add_argument("--seconds", type=int,
                    help="остановиться через столько секунд (для проверок)")
    ap.add_argument("--list", action="store_true", help="показать порты и выйти")
    args = ap.parse_args()

    if args.list:
        ports = list_ports()
        if not ports:
            print("портов не найдено (или не установлен pyserial)")
            return 1
        for dev, desc, likely in ports:
            print(f"{dev:<24} {desc}" + ("   [похоже на переходник]" if likely else ""))
        return 0

    if args.console:
        if not args.port:
            print("В режиме без окна нужно указать --port. "
                  "Список портов: --list")
            return 2
        return run_console(args.port, args.baud, args.log_dir, args.seconds)

    return run_gui(args.baud, args.log_dir)


if __name__ == "__main__":
    sys.exit(main())
