#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — наземная станция и отладка.

Три режима в одном окне:

  Боевой (радио)    приём телеметрии от HC-12 через FT232RL. Показывает всё,
                    что передаёт ракета, в реальном времени, и пишет в CSV
                    всё принятое: пакеты, повторы, события, испорченные строки.

  Отладка (провод)  плата RocketBoard подключена к компьютеру по USB.
                    Живые показания с борта и ручное управление приводом
                    для наземной отладки парашюта. Всё пишется в CSV.

  Прошивка          выбрать готовую прошивку (или свой .hex) и залить в плату.

Запуск с окном:

    python vro_station.py

Без окна (проверка, работа по SSH):

    python3 vro_station.py --console --mode debug --port /dev/ttyACM0 \\
        --cmd "d,s" --seconds 20
"""

import argparse
import collections
import math
import os
import queue
import sys
import time
from datetime import datetime
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

from telemetry import Session, Event, Packet, describe_errors, STATE_RU
from vro_link import (
    PortLink, CsvLog, RADIO_COLUMNS, DEBUG_COLUMNS, radio_row, debug_row,
    sent_row, BatteryLog, parse_debug_line, RadioCommander, RADIO_CMDS, angle_command, angle_actual,
    CMD_SAFE, CMD_DEPLOY, CMD_CYCLE, CMD_SIM, CMD_INFO, CMD_HELP, CMD_ZERO, CMD_READY, ANGLE_STEP,
    ANGLE_MAX,
)
from rocket_ground import list_ports, DATA_TIMEOUT_S
import vro_flash
import vro_ready
import vro_shot
import vro_battery
from vro_stats import FlightStats
from vro_graph import (History, WINDOWS, DEFAULT_WINDOW_S, decimate, nice_step,
                       y_range, ease, time_step, fmt_ago)

MODE_RADIO = "radio"
MODE_DEBUG = "debug"
MODE_RADIO_DEBUG = "radiodebug"
MODE_FLASH = "flash"

DEFAULT_BAUD = {MODE_RADIO: 9600, MODE_RADIO_DEBUG: 9600, MODE_DEBUG: 115200,
                MODE_FLASH: 115200}


def not_ready_hint(state: str) -> str:
    """Почему борт не отвечает на команды и что делать оператору."""
    ru = STATE_RU.get(state, state)
    if state == "LANDED":
        return ("Борт после посадки: команды привода и обнуления не принимаются. "
                "Уложите парашют и нажмите «Вернуть в READY», либо перезагрузите плату питанием. "
                "Сам борт в READY не возвращается.")
    return (f"Борт не в READY (сейчас: {ru}): команды не принимаются, в полёте они "
            "заблокированы. Борт сам в READY не возвращается.")


def deployed_hint(state: str, recovery: str) -> str:
    """Спасение раскрыто вручную в READY: прогон профиля его не проверит."""
    if state == "READY" and recovery == "DEPLOYED":
        return ("Система спасения уже раскрыта (вручную): раскрытие выдаётся один раз, "
                "прогон профиля его не проверит. Сложите парашют и нажмите «Вернуть в READY».")
    return ""


def is_radio(mode: str) -> bool:
    """Режимы, где данные идут по радио: боевой и отладка без провода."""
    return mode in (MODE_RADIO, MODE_RADIO_DEBUG)

# Ниже этого напряжения Кроны борт взводит флаг (VBAT_LOW_V в config.h).
VBAT_LOW_V = 7.8
# На USB без батареи делитель показывает около 4,2 В.
VBAT_USB_ONLY_V = 5.5

# В собранном .exe (PyInstaller --onefile) папка со скриптом это временный
# каталог, стираемый при выходе. Журналы кладём рядом с самим .exe.
if getattr(sys, "frozen", False):
    LOG_DIR_DEFAULT = os.path.join(os.path.dirname(sys.executable), "logs")
else:
    LOG_DIR_DEFAULT = os.path.join(HERE, "logs")


# --------------------------------------------------------------------
#  Темы оформления
# --------------------------------------------------------------------

# Тёмная для помещения, светлая для работы на улице под солнцем. Значения
# в пределах одной темы различаются: перекраска работает по соответствию
# «цвет тёмной -> цвет светлой».
PALETTES = {
    "dark": {
        "BG": "#101418", "PANEL": "#161b22", "FG": "#e6edf3", "DIM": "#8b949e",
        "OK": "#3fb950", "WARN": "#d29922", "BAD": "#f85149",
        "BTN": "#30363d", "BTN_ACTIVE": "#3d444d", "LOGBG": "#0d1117",
        "GRID": "#21262d", "GO": "#238636", "SEL": "#1f7a33",
        "STOP": "#8b2c2c", "ACCENT": "#1f6feb", "OFF": "#6e7681",
    },
    "light": {
        "BG": "#ffffff", "PANEL": "#e6ebf0", "FG": "#000000", "DIM": "#1f2328",
        "OK": "#0f5c22", "WARN": "#7a4b00", "BAD": "#a4001a",
        "BTN": "#d0d7de", "BTN_ACTIVE": "#b6bec8", "LOGBG": "#f6f8fa",
        "GRID": "#e1e5ea", "GO": "#1f883d", "SEL": "#aceebb",
        "STOP": "#c62828", "ACCENT": "#0969da", "OFF": "#8c959f",
    },
}


def settings_path_for(log_dir: str) -> str:
    """Файл настроек лежит рядом с папкой журналов (в .exe рядом с самим .exe)."""
    return os.path.join(os.path.dirname(os.path.abspath(log_dir)), "vro_settings.json")


def load_theme(path: str) -> str:
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            name = json.load(fh).get("theme")
        return name if name in PALETTES else "dark"
    except (OSError, ValueError):
        return "dark"


def load_settings(path: str) -> dict:
    try:
        import json
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: str, **updates) -> None:
    """Дописать настройки, не стирая остальные (тема и батарея в одном файле)."""
    data = load_settings(path)
    data.update(updates)
    try:
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def save_theme(path: str, name: str) -> None:
    save_settings(path, theme=name)


def load_battery_empty(path: str, full_v: float) -> float:
    """Напряжение, принятое за 0 % (зафиксированное оператором или ориентир 7,0 В)."""
    try:
        v = float(load_settings(path).get("battery_empty_v", vro_battery.EMPTY_V))
    except (TypeError, ValueError):
        return vro_battery.EMPTY_V
    return v if vro_battery.valid_empty(v, full_v) else vro_battery.EMPTY_V


def load_battery_full(path: str) -> float:
    """Напряжение, принятое за 100 % (зафиксированное оператором или по умолчанию)."""
    try:
        v = float(load_settings(path).get("battery_full_v", vro_battery.FULL_DEFAULT_V))
    except (TypeError, ValueError):
        return vro_battery.FULL_DEFAULT_V
    return v if vro_battery.valid_full(v) else vro_battery.FULL_DEFAULT_V


# --------------------------------------------------------------------
#  Режим без окна
# --------------------------------------------------------------------

def run_console(mode: str, port: str, baud: int, log_dir: str,
                seconds: Optional[int], commands: str) -> int:
    q: "queue.Queue" = queue.Queue()
    link = PortLink(port, baud, q)
    session = Session()
    columns = RADIO_COLUMNS if is_radio(mode) else DEBUG_COLUMNS
    log = CsvLog(log_dir, "radio" if is_radio(mode) else "debug", columns)
    cmdr = None
    if mode == MODE_RADIO_DEBUG:
        def send_frame(frame: bytes) -> None:
            if link.send(frame):
                log.write(sent_row(frame.strip()))
                print(f"  -> кадр {frame.strip().decode()}")
        cmdr = RadioCommander(send_frame)

    print(f"Режим: {mode}, порт {port} @ {baud}")
    print(f"Журнал CSV: {log.path}")

    # Команды: через запятую, число секунд означает паузу, остальное шлётся.
    script = [c.strip() for c in commands.split(",") if c.strip()] if commands else []
    next_cmd_at = time.time() + 2.0

    link.start()
    started = time.time()
    last_report = 0.0
    failed = False
    lines = 0
    try:
        while seconds is None or time.time() - started < seconds:
            try:
                kind, payload = q.get(timeout=0.1)
            except queue.Empty:
                kind, payload = None, None

            if kind == "error":
                print(f"ОШИБКА: {payload}")
                failed = True
                break
            if kind == "closed":
                break
            if kind == "line":
                lines += 1
                if is_radio(mode):
                    row = radio_row(session, payload)
                    log.write(row)
                    if row["kind"] == "ack":
                        got = cmdr is not None and cmdr.on_ack(int(row["seq"]))
                        print(f"  <- подтверждение {row['seq']} «{row['state']}»"
                              + (" (наша команда)" if got else ""))
                        if got:
                            next_cmd_at = time.time() + 1.5
                else:
                    log.write(debug_row(payload))
                    if parse_debug_line(payload) is None:
                        print(f"  борт: {payload}")

            now = time.time()
            if script and now >= next_cmd_at:
                cmd = script.pop(0)
                if cmdr is not None:
                    cmdr.submit(cmd, now)
                    # следующую команду только после подтверждения или отказа
                    next_cmd_at = now + 60.0
                else:
                    data = cmd.encode()
                    if link.send(data):
                        log.write(sent_row(data))
                        print(f"  -> команда {cmd!r}")
                    next_cmd_at = now + 1.5

            if cmdr is not None:
                had = cmdr.pending is not None
                cmdr.tick(now)
                if had and cmdr.pending is None:
                    print(f"  {cmdr.status}")
                    next_cmd_at = now + 1.5

            if now - last_report >= 1.0:
                last_report = now
                if is_radio(mode) and session.last_packet:
                    p = session.last_packet
                    print(f"{p.state_ru:<18} h={p.altitude_m:7.2f} м  p={p.pressure_pa}  "
                          f"принято={session.received} потеряно={session.lost} "
                          f"повторов={session.duplicates} испорчено={session.bad_lines}")
                elif mode == MODE_DEBUG:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        link.stop()
        log.close()

    print(f"\nСтрок принято: {lines}, в журнал записано: {log.rows}")
    print(f"Журнал: {log.path}")
    return 1 if failed else 0


# --------------------------------------------------------------------
#  Окно
# --------------------------------------------------------------------

def run_gui(log_dir: str) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("Не найден tkinter. В Debian и Ubuntu: apt install python3-tk")
        return 1

    theme = {"name": load_theme(settings_path_for(log_dir))}
    P = PALETTES[theme["name"]]
    BG, PANEL, FG, DIM = P["BG"], P["PANEL"], P["FG"], P["DIM"]
    OK, WARN, BAD = P["OK"], P["WARN"], P["BAD"]
    BTN, BTN_ACTIVE, LOGBG, GRID = P["BTN"], P["BTN_ACTIVE"], P["LOGBG"], P["GRID"]
    GO, SEL, STOP, ACCENT = P["GO"], P["SEL"], P["STOP"], P["ACCENT"]
    OFF = P["OFF"]                       # текст выключенных кнопок

    root = tk.Tk()
    root.title("ВРО-1 — станция и отладка")
    root.configure(bg=BG)
    root.minsize(980, 700)

    q: "queue.Queue" = queue.Queue()
    st = {"link": None, "log": None, "session": Session(), "mode": MODE_RADIO,
          "last_data": 0.0, "connected": False,
          "arrivals": collections.deque(maxlen=200),
          "cmdr": None, "vbat": None, "blog": None, "empty_v": vro_battery.EMPTY_V, "full_v": vro_battery.FULL_DEFAULT_V, "flights": 0, "stats": FlightStats(), "hist": History(), "win_s": DEFAULT_WINDOW_S, "ylo": None, "yhi": None,
          "debug": None}

    st["full_v"] = load_battery_full(settings_path_for(log_dir))
    st["empty_v"] = load_battery_empty(settings_path_for(log_dir), st["full_v"])

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    def label(parent, text, fg=FG, size=11, bold=False, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", BG), fg=fg,
                        font=("TkDefaultFont", size, "bold" if bold else "normal"),
                        **kw)

    def button(parent, text, cmd, bg=BTN, fg=FG, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief="flat", padx=kw.pop("padx", 12),
                         pady=kw.pop("pady", 4), activebackground=BTN_ACTIVE,
                         activeforeground=FG,
                         disabledforeground=kw.pop("disabledforeground", OFF), **kw)

    # ---------------- верхняя панель: режим, порт ----------------

    top = tk.Frame(root, bg=BG)
    top.pack(fill="x", padx=14, pady=(12, 4))

    mode_var = tk.StringVar(value=MODE_RADIO)

    def on_mode_change() -> None:
        if st["connected"]:
            mode_var.set(st["mode"])
            messagebox.showinfo("Режим", "Сначала нажмите «Отключиться».")
            return
        if flashing["on"]:
            mode_var.set(MODE_FLASH)
            return
        baud_var.set(str(DEFAULT_BAUD[mode_var.get()]))
        show_mode(mode_var.get())

    for text, val in (("Боевой (радио)", MODE_RADIO),
                      ("Отладка (радио)", MODE_RADIO_DEBUG),
                      ("Отладка (провод)", MODE_DEBUG),
                      ("Прошивка", MODE_FLASH)):
        tk.Radiobutton(top, text=text, value=val, variable=mode_var,
                       command=on_mode_change, bg=BG, fg=FG, selectcolor=PANEL,
                       activebackground=BG, activeforeground=FG,
                       font=("TkDefaultFont", 11, "bold")).pack(side="left", padx=(0, 14))

    label(top, "Порт:").pack(side="left")
    port_var = tk.StringVar()
    port_box = ttk.Combobox(top, textvariable=port_var, width=36, state="readonly")
    port_box.pack(side="left", padx=6)

    def refresh_ports() -> None:
        ports = list_ports()
        if not ports:
            port_box["values"] = ["(портов не найдено)"]
            port_box.current(0)
            return
        port_box["values"] = [f"{d} — {desc}" + ("   [переходник/плата]" if lk else "")
                              for d, desc, lk in ports]
        port_box.current(0)

    def selected_port() -> Optional[str]:
        t = port_var.get()
        return None if not t or t.startswith("(") else t.split(" — ")[0].strip()

    button(top, "Обновить", refresh_ports).pack(side="left")

    label(top, "Скорость:").pack(side="left", padx=(12, 2))
    baud_var = tk.StringVar(value=str(DEFAULT_BAUD[MODE_RADIO]))
    baud_box = ttk.Combobox(top, textvariable=baud_var, width=8,
                            values=["9600", "19200", "57600", "115200"])
    baud_box.pack(side="left")

    connect_btn = button(top, "Подключиться", lambda: toggle(), bg=GO,
                         fg="white", padx=18)
    connect_btn.pack(side="left", padx=12)

    theme_btn = button(top, "Светлая тема" if theme["name"] == "dark" else "Тёмная тема",
                       lambda: apply_theme("light" if theme["name"] == "dark" else "dark"))
    theme_btn.pack(side="right")

    # индикатор потока
    flow = tk.Frame(root, bg=BG)
    flow.pack(fill="x", padx=14)
    flow_dot = label(flow, "●", fg=DIM, size=16)
    flow_dot.pack(side="left")
    flow_text = label(flow, "не подключено", fg=DIM)
    flow_text.pack(side="left", padx=6)
    link_stats = label(flow, "", fg=DIM)
    link_stats.pack(side="right")

    # ---------------- плитки ----------------

    def make_tiles(parent, items):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(8, 4))
        out = {}
        for i, (key, caption, big) in enumerate(items):
            f = tk.Frame(row, bg=PANEL, padx=12, pady=8)
            f.grid(row=0, column=i, sticky="nsew", padx=3)
            row.grid_columnconfigure(i, weight=1)
            tk.Label(f, text=caption, bg=PANEL, fg=DIM,
                     font=("TkDefaultFont", 9)).pack(anchor="w")
            v = tk.Label(f, text="—", bg=PANEL, fg=FG,
                         font=("TkDefaultFont", 20 if big else 14, "bold"))
            v.pack(anchor="w")
            out[key] = v
        return out

    def make_servo_panel(parent, send, buttons, wired: bool):
        """Кнопки привода. send(bytes, подпись). wired: ещё «Сведения» и «Справка» (USB)."""
        box = tk.LabelFrame(parent, text=" Управление платой: привод (парашют) и предполётное обнуление ",
                            bg=BG, fg=FG, padx=10, pady=8)
        row1 = tk.Frame(box, bg=BG)
        row1.pack(fill="x")
        items = [("Обнулить высоту", CMD_ZERO, ACCENT),
                 ("Вернуть в READY", CMD_READY, BTN),
                 ("Цикл: открыть → закрыть", CMD_CYCLE, BTN),
                 ("Прогон профиля полёта", CMD_SIM, BTN)]
        if wired:
            items += [("Сведения о плате", CMD_INFO, BTN),
                      ("Справка", CMD_HELP, BTN)]
        def click(d: bytes, t: str) -> None:
            if d == CMD_READY and not messagebox.askokcancel(
                    "Вернуть в READY",
                    "Парашют сложен и уложен, привод свободен?\n\n"
                    "Привод вернётся в SAFE, система спасения будет взведена заново, "
                    "высота обнулится (ракета неподвижна около 8 секунд). "
                    "Работает после посадки или после ручного раскрытия парашюта."):
                return
            send(d, t)

        for text, data, colour in items:
            sat = colour in (STOP, ACCENT)
            b = button(row1, text, lambda d=data, t=text: click(d, t), bg=colour,
                       fg="white" if sat else FG,
                       disabledforeground="white" if sat else OFF)
            b.pack(side="left", padx=(0, 8))
            b.cmd = data.decode()          # для блокировки по состоянию борта
            if sat:
                b.role = "STOP" if colour == STOP else "ACCENT"
            buttons.append(b)

        # Закрыть и открыть серву на разных краях строки, чтобы не перепутать.
        row2 = tk.Frame(box, bg=BG)
        row2.pack(fill="x", pady=(8, 0))
        b = button(row2, "Закрыть серву", lambda: send(CMD_SAFE, "закрыть серву"),
                   padx=16, pady=6)
        b.pack(side="left", padx=(0, 16))
        b.cmd = CMD_SAFE.decode()
        buttons.append(b)
        b = button(row2, "Открыть серву", lambda: send(CMD_DEPLOY, "открыть серву"),
                   bg=STOP, fg="white", disabledforeground="white", padx=16, pady=6)
        b.pack(side="right", padx=(16, 0))
        b.cmd = CMD_DEPLOY.decode()
        b.role = "STOP"
        buttons.append(b)
        label(row2, f"Угол, шаг {ANGLE_STEP}°:", fg=DIM).pack(side="left", padx=(0, 8))
        for deg in range(0, ANGLE_MAX + 1, ANGLE_STEP):
            b = button(row2, f"{deg}°",
                       lambda d=deg: send(angle_command(d), f"угол {angle_actual(d)}°"),
                       padx=8)
            b.pack(side="left", padx=2)
            b.cmd = angle_command(deg).decode()
            buttons.append(b)

        hint = ("Кнопки, которые в текущем состоянии борта не имеют смысла, выключены. "
                "Команды работают только в состоянии READY, в полёте борт их не читает. "
                "Привод подключать до подачи питания. «Обнулить высоту»: ракета "
                "неподвижна около 8 с, потом высота снова 0. «Вернуть в READY»: после "
                "посадки, когда парашют сложен (борт сам не возвращается)." if wired else
                "По радио: команда с номером и CRC повторяется, пока борт не подтвердит. "
                "Работает только в READY и в наборе c_radio. «Обнулить высоту»: ракета "
                "неподвижна около 8 с, потом высота снова 0. «Вернуть в READY»: после "
                "посадки, когда парашют сложен (борт сам не возвращается).")
        label(box, hint, fg=DIM, size=9, anchor="w", justify="left").pack(fill="x", pady=(8, 0))
        return box

    content = tk.Frame(root, bg=BG)
    content.pack(fill="both", expand=True, padx=14)

    # ======== панель боевого режима ========
    radio_frame = tk.Frame(content, bg=BG)
    r_tiles = make_tiles(radio_frame, [
        ("state", "СОСТОЯНИЕ", True), ("alt", "ВЫСОТА, м", True),
        ("max", "МАКСИМУМ, м", False), ("press", "ДАВЛЕНИЕ, Па", False),
        ("rec", "СПАСЕНИЕ", False), ("t", "ВРЕМЯ БОРТА, с", False),
        ("seq", "ПАКЕТ №", False)])
    # Итоги полёта. Ускорения в радиопакете нет, оно оценивается по высоте.
    r2_tiles = make_tiles(radio_frame, [
        ("apo", "АПОГЕЙ, м", False), ("v", "СКОРОСТЬ СЕЙЧАС, м/с", False),
        ("vup", "ВВЕРХ МАКС, м/с", False), ("vdesc", "СПУСК НА ПАРАШЮТЕ, м/с", False),
        ("acc", "УСКОРЕНИЕ МАКС, g (оценка)", False), ("dep", "РАСКРЫТИЕ", False),
        ("ft", "ВРЕМЯ ПОЛЁТА, с", False), ("bat", "БАТАРЕЯ (ЗА ДИОДОМ)", False)])
    r_err = label(radio_frame, "", fg=BAD, bold=True, anchor="w", justify="left")
    r_err.pack(fill="x", pady=(2, 0))

    # Открыть парашют: на случай, если автомат «завис». Видна и в боевом
    # режиме, стоит справа. Отправляет одну команду D по радио; борт принимает
    # её в любом состоянии, кроме «посадки» (набор c_radio).
    em_box = tk.Frame(radio_frame, bg=BG)     # виден только в боевом режиме

    def emergency_deploy() -> None:
        cmdr = st["cmdr"]
        if cmdr is None or not st["connected"]:
            return
        if not messagebox.askyesno(
                "Открыть парашют",
                "Открыть парашют прямо сейчас?\n\n"
                "Команда уйдёт на борт по радио и будет повторяться до подтверждения. "
                "Парашют выйдет сразу, отменить нельзя. Работает в любом состоянии "
                "полёта, кроме посадки, и только на наборе c_radio.",
                icon="warning", default="no"):
            return
        cmdr.submit("D", time.time())
        add_line(r_log, f"[{datetime.now():%H:%M:%S}] ОТКРЫТЬ ПАРАШЮТ: команда отправлена", BAD)

    em_btn = button(em_box, "ОТКРЫТЬ ПАРАШЮТ", emergency_deploy, bg=STOP,
                    fg="white", disabledforeground="white", padx=24, pady=8)
    em_btn.config(font=("TkDefaultFont", 12, "bold"))
    em_btn.pack(side="right")
    em_status = label(em_box, "", fg=DIM, size=10, anchor="e", justify="right")
    em_status.pack(side="right", padx=14)

    # График и события в разделителе: границу можно тянуть мышью, а при
    # растягивании окна лишнее место достаётся графику.
    rd_box = tk.Frame(radio_frame, bg=BG)          # только в режиме «Отладка (радио)»
    rd_buttons = []

    def radio_send(data: bytes, note: str = "") -> None:
        cmdr = st["cmdr"]
        cmd = data.decode("ascii", errors="replace")
        if cmdr is None or not st["connected"] or cmd not in RADIO_CMDS:
            return
        lp = st["session"].last_packet
        if lp is not None and lp.state != "READY":
            add_line(r_log, not_ready_hint(lp.state), BAD)
        cmdr.submit(cmd, time.time())
        add_line(r_log, f"[{datetime.now():%H:%M:%S}] -> {note or cmd} (по радио)", WARN)

    make_servo_panel(rd_box, radio_send, rd_buttons, wired=False).pack(fill="x")
    rd_status = label(rd_box, "", fg=WARN, size=10, anchor="w", justify="left", wraplength=1150)
    rd_status.pack(fill="x", pady=(2, 0))

    paned = tk.PanedWindow(radio_frame, orient="vertical", bg=BG, sashwidth=8,
                           sashrelief="flat", opaqueresize=True, bd=0)
    paned.pack(fill="both", expand=True, pady=6)

    graph_box = tk.Frame(paned, bg=BG)
    gbar = tk.Frame(graph_box, bg=BG)
    gbar.pack(fill="x")
    label(gbar, "Высота, м. Показать за:", fg=DIM, size=9).pack(side="left", padx=(0, 6))

    win_var = tk.StringVar(value=str(DEFAULT_WINDOW_S))

    def set_window() -> None:
        st["win_s"] = None if win_var.get() == "all" else float(win_var.get())

    for text, secs in WINDOWS:
        tk.Radiobutton(gbar, text=text, value="all" if secs is None else str(secs),
                       variable=win_var, command=set_window, indicatoron=0,
                       bg=GRID, fg=FG, selectcolor=SEL,
                       activebackground=BTN, activeforeground=FG,
                       relief="flat", bd=0, padx=10, pady=2).pack(side="left", padx=2)

    def clear_graph() -> None:
        st["hist"].clear()
        st["ylo"] = st["yhi"] = None

    button(gbar, "Очистить график", clear_graph, padx=10, pady=2).pack(side="right")

    graph = tk.Canvas(graph_box, bg=LOGBG, height=260, highlightthickness=0)
    graph.pack(fill="both", expand=True, pady=(4, 0))

    log_box = tk.Frame(paned, bg=BG)
    label(log_box, "События и связь (границу с графиком можно тянуть мышью)",
          fg=DIM, size=9, anchor="w").pack(fill="x")
    r_log = tk.Text(log_box, bg=LOGBG, fg=FG, relief="flat", height=6,
                    font=("TkFixedFont", 10), wrap="none")
    r_log.pack(fill="both", expand=True)

    paned.add(graph_box, stretch="always", minsize=150)
    paned.add(log_box, stretch="never", minsize=70)

    # ======== панель отладки по проводу ========
    debug_frame = tk.Frame(content, bg=BG)
    d_tiles = make_tiles(debug_frame, [
        ("state", "СОСТОЯНИЕ", True), ("alt", "ВЫСОТА, м", True),
        ("acc", "|a|, g", False), ("press", "ДАВЛЕНИЕ, Па", False),
        ("rec", "ПРИВОД", False), ("vb", "БАТАРЕЯ (ЗА ДИОДОМ)", False),
        ("t", "ВРЕМЯ БОРТА, с", False)])
    d_err = label(debug_frame, "", fg=BAD, bold=True, anchor="w", justify="left",
                  wraplength=1150)
    d_err.pack(fill="x", pady=(2, 0))

    servo_buttons = []

    def send_cmd(data: bytes, note: str = "") -> None:
        link = st["link"]
        if link is None or not st["connected"]:
            return
        ds = st["debug"]
        if ds is not None and ds.state != "READY":
            console_add(not_ready_hint(ds.state), BAD)
        if link.send(data):
            if st["log"] is not None:
                st["log"].write(sent_row(data))
            console_add(f"[{datetime.now():%H:%M:%S}] -> {note or data.decode()}", WARN)

    servo = make_servo_panel(debug_frame, send_cmd, servo_buttons, wired=True)
    servo.pack(fill="x", pady=6)

    row3 = tk.Frame(debug_frame, bg=BG)
    row3.pack(fill="x")
    label(row3, "Вывод платы", fg=DIM).pack(side="left")
    raw_var = tk.StringVar()
    raw_entry = tk.Entry(row3, textvariable=raw_var, width=10, bg=PANEL, fg=FG,
                         insertbackground=FG, relief="flat",
                         disabledbackground=PANEL, disabledforeground=DIM)
    raw_entry.pack(side="right", padx=(6, 0))
    label(row3, "своя команда (символ):", fg=DIM).pack(side="right")

    def send_raw(_evt=None) -> None:
        text = raw_var.get()
        if text:
            send_cmd(text.encode("utf-8"), f"«{text}»")
            raw_var.set("")

    raw_entry.bind("<Return>", send_raw)

    d_log = tk.Text(debug_frame, bg=LOGBG, fg=FG, relief="flat", height=10,
                    font=("TkFixedFont", 10), wrap="none")
    d_log.pack(fill="both", expand=True, pady=(2, 0))

    # ======== панель прошивки ========
    flash_frame = tk.Frame(content, bg=BG)
    flashing = {"on": False}
    fq: "queue.Queue" = queue.Queue()
    fw_var = tk.StringVar()
    custom = {"path": None}

    label(flash_frame, "Какую прошивку залить в плату", bold=True, anchor="w").pack(
        fill="x", pady=(10, 4))
    fw_row = tk.Frame(flash_frame, bg=BG)
    fw_row.pack(fill="x")
    fw_box = tk.Frame(fw_row, bg=BG)
    fw_box.pack(side="left", anchor="n")
    fw_info = tk.Text(fw_row, bg=PANEL, fg=FG, relief="flat", height=13, wrap="word",
                      padx=12, pady=8, font=("TkDefaultFont", 10), cursor="arrow")
    fw_info.pack(side="left", fill="both", expand=True, padx=(16, 0))
    fw_info.tag_config("title", font=("TkDefaultFont", 12, "bold"), foreground=FG)
    fw_info.tag_config("head", font=("TkDefaultFont", 10, "bold"), foreground=DIM)
    fw_info.tag_config("body", foreground=FG)

    def show_fw_info() -> None:
        key = fw_var.get()
        if key == "custom":
            path = custom["path"]
            rows = (vro_flash.describe("custom", path) if path else
                    [("", "Свой файл прошивки"),
                     ("", "Нажмите «Выбрать файл .hex...» и укажите файл.")])
        else:
            found = [p for k, _t, p in vro_flash.available_firmwares() if k == key]
            rows = vro_flash.describe(key, found[0]) if found else []
        fw_info.config(state="normal")
        fw_info.delete("1.0", "end")
        for head, text in rows:
            if head == "":
                fw_info.insert("end", text + "\n\n", "title")
            else:
                fw_info.insert("end", head + ": ", "head")
                fw_info.insert("end", text + "\n\n", "body")
        fw_info.config(state="disabled")

    def build_fw_list() -> None:
        for w in fw_box.winfo_children():
            w.destroy()
        fws = vro_flash.available_firmwares()
        if not fws:
            label(fw_box, "Готовых прошивок рядом с программой нет. "
                          "Выберите свой файл .hex.", fg=WARN, anchor="w").pack(fill="x")
        for key, text, path in fws:
            tk.Radiobutton(fw_box, text=text, value=key, variable=fw_var,
                           command=show_fw_info, bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                           activeforeground=FG, anchor="w",
                           font=("TkDefaultFont", 11)).pack(fill="x", pady=1)
        if fws:
            fw_var.set(next((k for k, _, _ in fws if k == "c_radio"), fws[0][0]))
        tk.Radiobutton(fw_box, text="Свой файл .hex", value="custom", variable=fw_var,
                       command=show_fw_info, bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, anchor="w",
                       font=("TkDefaultFont", 11)).pack(fill="x", pady=1)
        show_fw_info()

    custom_label = label(flash_frame, "", fg=DIM, size=9, anchor="w")

    def pick_hex() -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Файл прошивки",
            filetypes=[("Intel HEX", "*.hex"), ("Все файлы", "*.*")])
        if path:
            custom["path"] = path
            fw_var.set("custom")
            custom_label.config(text=path)
            show_fw_info()

    frow = tk.Frame(flash_frame, bg=BG)
    frow.pack(fill="x", pady=(8, 4))
    button(frow, "Выбрать файл .hex...", pick_hex).pack(side="left")
    flash_btn = button(frow, "Прошить плату", lambda: do_flash(), bg=GO,
                       fg="white", padx=22, pady=6)
    flash_btn.pack(side="left", padx=14)
    custom_label.pack(fill="x")
    label(flash_frame,
          "Как прошить\n"
          "1. Подключите плату USB-кабелем (нужен кабель с передачей данных, не только зарядка).\n"
          "2. Выберите порт платы в списке сверху, при необходимости нажмите «Обновить».\n"
          "3. Выберите прошивку слева и прочитайте пояснение справа.\n"
          "4. Нажмите «Прошить плату» и подтвердите. Пока идёт запись, кабель не отключайте.\n"
          "5. Подождите 3 секунды: плата перезапустится и может получить другой номер порта, "
          "тогда нажмите «Обновить».\n"
          "Если порта нет: другой кабель или разъём USB. При сбое запись повторяется до 3 раз "
          "сама. Полётную прошивку перед пуском проверьте в режиме «Боевой».",
          fg=DIM, size=10, anchor="w", justify="left", wraplength=1100).pack(fill="x", pady=(4, 0))
    f_log = tk.Text(flash_frame, bg=LOGBG, fg=FG, relief="flat", height=14,
                    font=("TkFixedFont", 10), wrap="none")
    f_log.pack(fill="both", expand=True, pady=(6, 0))

    def do_flash() -> None:
        port = selected_port()
        if port is None:
            messagebox.showwarning("Порт не выбран",
                                   "Подключите плату USB-кабелем и нажмите «Обновить».")
            return
        key = fw_var.get()
        if key == "custom":
            path = custom["path"]
            if not path:
                messagebox.showwarning("Файл не выбран", "Нажмите «Выбрать файл .hex...».")
                return
            title = os.path.basename(path)
        else:
            found = [(t, p) for k, t, p in vro_flash.available_firmwares() if k == key]
            if not found:
                messagebox.showwarning("Прошивка", "Файл прошивки не найден.")
                return
            title, path = found[0]
        if not messagebox.askokcancel("Прошить плату",
                                      f"Записать «{title}» в плату на {port}?\n"
                                      "Прежняя прошивка будет стёрта."):
            return
        flashing["on"] = True
        flash_btn.config(state="disabled")
        add_line(f_log, f"--- {datetime.now():%H:%M:%S} {title} -> {port} ---", DIM, keep=800)

        def work() -> None:
            ok = vro_flash.flash(path, port, log=lambda t: fq.put(("line", t)))
            fq.put(("done", ok))

        import threading
        threading.Thread(target=work, daemon=True).start()

    def drain_flash() -> None:
        while True:
            try:
                kind, payload = fq.get_nowait()
            except queue.Empty:
                return
            if kind == "line":
                add_line(f_log, payload, FG, keep=800)
            else:
                flashing["on"] = False
                flash_btn.config(state="normal")
                add_line(f_log, "ПРОШИТО" if payload else "ОШИБКА ПРОШИВКИ",
                         OK if payload else BAD, keep=800)
                refresh_ports()

    # ---------------- нижняя строка ----------------

    bottom = tk.Frame(root, bg=BG)
    bottom.pack(fill="x", padx=14, pady=(4, 12))
    file_label = label(bottom, "журнал не начат", fg=DIM, size=9)
    file_label.pack(side="left")

    def open_log_folder() -> None:
        path = os.path.abspath(log_dir)
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)                      # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
        except Exception as exc:
            messagebox.showerror("Не удалось открыть папку", str(exc))

    button(bottom, "Открыть папку с журналами", open_log_folder).pack(side="right")

    def take_screenshot(_event=None) -> None:
        """Снимок всего окна программы в PNG рядом с журналами."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(log_dir, f"screenshot_{stamp}.png")
        try:
            os.makedirs(log_dir, exist_ok=True)
            vro_shot.save_window_png(root, path)
            console_add(f"[{datetime.now():%H:%M:%S}] снимок экрана: {path}", OK)
        except Exception as exc:
            console_add(f"Снимок экрана не удался: {exc}", BAD)

    # С задержкой, чтобы кнопка успела отжаться и не попала в кадр нажатой.
    button(bottom, "Снимок экрана (F12)",
           lambda: root.after(250, take_screenshot)).pack(side="right", padx=(0, 8))
    root.bind("<F12>", take_screenshot)
    button(bottom, "Батарея = 0 %", lambda: fix_battery_empty()).pack(side="right", padx=(0, 8))
    button(bottom, "Батарея = 100 %", lambda: fix_battery_full()).pack(side="right", padx=(0, 8))

    # Готовность к пуску: компактно, справа внизу, рядом с кнопкой журналов.
    ready_lbl = tk.Label(bottom, text="", font=("TkDefaultFont", 11, "bold"),
                         padx=12, pady=3, bg=BG, fg=FG)
    ready_lbl.pack(side="right", padx=(0, 12))

    # ---------------- вспомогательное ----------------

    def add_line(box, text: str, colour: str = FG, keep: int = 600) -> None:
        box.insert("end", text + "\n")
        box.tag_add(colour, "end-2l", "end-1l")
        box.tag_config(colour, foreground=colour)
        if int(box.index("end-1c").split(".")[0]) > keep:
            box.delete("1.0", "50.0")
        box.see("end")

    def console_add(text: str, colour: str = FG) -> None:
        add_line(d_log if st["mode"] == MODE_DEBUG else r_log, text, colour)

    def show_mode(mode: str) -> None:
        st["mode"] = mode
        radio_frame.pack_forget()
        debug_frame.pack_forget()
        flash_frame.pack_forget()
        {MODE_RADIO: radio_frame, MODE_RADIO_DEBUG: radio_frame, MODE_DEBUG: debug_frame,
         MODE_FLASH: flash_frame}[mode].pack(fill="both", expand=True)
        em_box.pack_forget()
        if mode == MODE_RADIO:
            em_box.pack(fill="x", pady=(4, 0), before=paned)
        rd_box.pack_forget()
        if mode == MODE_RADIO_DEBUG:
            rd_box.pack(fill="x", pady=(4, 0), before=paned)
        is_flash = mode == MODE_FLASH
        connect_btn.config(state="disabled" if is_flash else "normal")
        baud_box.config(state="disabled" if is_flash else "normal")
        if is_flash:
            flow_dot.config(fg=DIM)
            flow_text.config(text="режим прошивки", fg=DIM)
        set_servo_state()

    def set_servo_state() -> None:
        on = st["connected"] and st["mode"] == MODE_DEBUG
        on_radio = st["connected"] and st["mode"] == MODE_RADIO_DEBUG
        em_on = st["connected"] and st["mode"] == MODE_RADIO
        em_btn.config(state="normal" if em_on else "disabled",
                      bg=STOP if em_on else BTN, fg="white" if em_on else OFF,
                      disabledforeground=OFF)
        for b in rd_buttons:
            b.config(state="normal" if on_radio else "disabled")
        for b in servo_buttons:
            b.config(state="normal" if on else "disabled")
        raw_entry.config(state="normal" if on else "disabled")

    # ---------------- подключение ----------------

    def do_connect() -> None:
        if mode_var.get() == MODE_FLASH:
            return
        port = selected_port()
        if port is None:
            messagebox.showwarning("Порт не выбран",
                                   "Подключите переходник или плату и нажмите «Обновить».")
            return
        try:
            baud = int(baud_var.get())
        except ValueError:
            messagebox.showwarning("Скорость", "Скорость должна быть числом.")
            return

        mode = mode_var.get()
        st["mode"] = mode
        st["session"] = Session()
        st["arrivals"].clear()
        st["flights"] = 0
        st["stats"] = FlightStats()
        st["hist"].clear()
        st["ylo"] = st["yhi"] = None
        st["stats"] = FlightStats()
        st["debug"] = None
        st["last_data"] = 0.0
        st["log"] = CsvLog(log_dir, "radio" if is_radio(mode) else "debug",
                           RADIO_COLUMNS if is_radio(mode) else DEBUG_COLUMNS)
        file_label.config(text=f"журнал: {st['log'].path}")

        link = PortLink(port, baud, q)
        link.start()
        st["link"] = link

        def send_frame(frame: bytes) -> None:
            if link.send(frame) and st["log"] is not None:
                st["log"].write(sent_row(frame.strip()))

        st["cmdr"] = RadioCommander(send_frame) if is_radio(mode) else None
        rd_status.config(text="")
        st["connected"] = True
        connect_btn.config(text="Отключиться", bg=STOP)
        port_box.config(state="disabled")
        set_servo_state()
        console_add(f"[{datetime.now():%H:%M:%S}] подключение к {port} @ {baud}", DIM)

    def save_summary(log_path: str) -> None:
        """Сводка полёта рядом с журналом: summary_<время>[_<номер полёта>].csv."""
        stats: FlightStats = st["stats"]
        if not (is_radio(st["mode"]) and stats.launched):
            return
        base = os.path.basename(log_path).split("_", 1)[1][:-4]
        st["flights"] += 1
        suffix = "" if st["flights"] == 1 else f"_{st['flights']}"
        summary_path = os.path.join(os.path.dirname(log_path), f"summary_{base}{suffix}.csv")
        try:
            stats.write_csv(summary_path)
            console_add(f"[{datetime.now():%H:%M:%S}] сводка полёта: {summary_path}", OK)
        except OSError as exc:
            console_add(f"Не удалось записать сводку: {exc}", BAD)

    def do_disconnect(reason: str = "") -> None:
        if st["link"] is not None:
            st["link"].stop()
            st["link"] = None
        if st["blog"] is not None:
            st["blog"].close()
            st["blog"] = None
        if st["log"] is not None:
            log_path = st["log"].path
            st["log"].close()
            st["log"] = None
            save_summary(log_path)
        st["connected"] = False
        st["cmdr"] = None
        rd_status.config(text="")
        em_status.config(text="")
        connect_btn.config(text="Подключиться", bg=GO)
        port_box.config(state="readonly")
        flow_dot.config(fg=DIM)
        flow_text.config(text="не подключено", fg=DIM)
        set_servo_state()
        if reason:
            console_add(f"[{datetime.now():%H:%M:%S}] {reason}", WARN)

    def toggle() -> None:
        do_disconnect("отключено оператором") if st["connected"] else do_connect()

    # ---------------- обработка данных ----------------

    def log_battery(volts) -> None:
        """Точка в отдельный файл battery_log.csv (дата, время, вольты, проценты)."""
        if volts is None or volts <= 0:
            return
        try:
            if st["blog"] is None:
                st["blog"] = BatteryLog(log_dir)
            first = st["blog"].rows == 0
            if st["blog"].write(volts, vro_battery.percent(volts, st["full_v"], st["empty_v"])) and first:
                console_add(f"[{datetime.now():%H:%M:%S}] батарея пишется в {st['blog'].path}", DIM)
        except OSError as exc:
            console_add(f"Не удалось писать журнал батареи: {exc}", BAD)

    def handle_radio_line(line: str) -> None:
        session: Session = st["session"]
        events_before = len(session.events)
        row = radio_row(session, line)
        st["log"].write(row)
        kind = row["kind"]
        if kind == "packet":
            st["arrivals"].append(time.time())
            p: Packet = session.last_packet
            st["hist"].add(p.time_ms / 1000.0, p.altitude_m)
            if st["stats"].state == "LANDED" and p.state == "READY":
                # Оператор вернул борт в READY: итоги прошлого полёта в файл,
                # учёт следующего начинается с нуля.
                save_summary(st["log"].path)
                st["stats"] = FlightStats()
                add_line(r_log, "борт снова в READY, итоги нового полёта считаются заново", DIM)
            st["stats"].add_packet(p.time_ms / 1000.0, p.altitude_m, p.state, p.recovery)
            log_battery(p.vbat_v)
        elif kind == "event" and len(session.events) > events_before:
            e = session.events[-1]
            add_line(r_log, f"{e.time_ms / 1000.0:>9.2f} с   {e.name}", WARN)
            st["stats"].add_event(e.time_ms / 1000.0, e.name)
            if e.name == "LANDED":
                add_line(r_log, "--- итоги полёта ---", OK)
                for _key, text, value in st["stats"].summary():
                    add_line(r_log, f"  {text}: {value}", OK)
        elif kind == "ack":
            a = session.acks[-1]
            if st["cmdr"] is not None and st["cmdr"].on_ack(a.seq):
                add_line(r_log, f"[{datetime.now():%H:%M:%S}] борт подтвердил команду «{a.cmd}»", OK)
        elif kind == "bad":
            add_line(r_log, f"испорчена: {line[:70]}", DIM)

    def handle_debug_line(line: str) -> None:
        st["log"].write(debug_row(line))
        s = parse_debug_line(line)
        if s is not None:
            st["debug"] = s
            log_battery(s.vbat_v)
        else:
            add_line(d_log, line, FG)

    def drain_queue() -> None:
        handled = 0
        while handled < 300:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                break
            handled += 1
            if kind == "error":
                console_add(f"[{datetime.now():%H:%M:%S}] {payload}", BAD)
                do_disconnect()
                messagebox.showerror("Ошибка связи", payload)
            elif kind == "opened":
                console_add(f"[{datetime.now():%H:%M:%S}] порт открыт", OK)
            elif kind == "closed":
                if st["connected"]:
                    do_disconnect("связь потеряна")
            elif kind == "line" and st["log"] is not None:
                st["last_data"] = time.time()
                if is_radio(st["mode"]):
                    handle_radio_line(payload)
                else:
                    handle_debug_line(payload)

    def draw_graph() -> None:
        """
        График высоты. Правый край окна плавно едет по часам компьютера
        между пакетами (иначе картинка дёргается по 5 раз в секунду), а
        границы по высоте догоняют цель плавно, без скачков.
        """
        if not is_radio(st["mode"]):
            return
        c = graph
        w, h = c.winfo_width(), c.winfo_height()
        if w < 120 or h < 80:
            return
        c.delete("all")
        hist: History = st["hist"]
        ml, mr, mt, mb = 56, 16, 12, 24
        pw, ph = w - ml - mr, h - mt - mb
        c.create_rectangle(ml, mt, ml + pw, mt + ph, outline=BTN)

        if not hist.t:
            c.create_text(ml + pw // 2, mt + ph // 2, fill=DIM,
                          text="ждём данные" if st["connected"] else "не подключено")
            return

        # --- окно по времени ---
        run = min(max(time.time() - hist.last_wall, 0.0), 0.6)
        right = hist.last_x + (run if st["connected"] else 0.0)
        win = st["win_s"]
        left = right - win if win is not None else min(hist.t[0], right - 5.0)
        span_t = max(right - left, 1e-6)

        ts, vs = hist.visible(left)
        inside = [v for t, v in zip(ts, vs) if t >= left] or vs

        # --- шкала высоты, плавно ---
        lo_t, hi_t = y_range(inside)
        if st["ylo"] is None:
            st["ylo"], st["yhi"] = lo_t, hi_t
        else:
            st["ylo"] = ease(st["ylo"], lo_t)
            st["yhi"] = ease(st["yhi"], hi_t)
        lo, hi = st["ylo"], st["yhi"]
        span_v = max(hi - lo, 1e-6)

        def px(t: float) -> float:
            return ml + (t - left) / span_t * pw

        def py(v: float) -> float:
            return mt + ph - (v - lo) / span_v * ph

        # --- сетка по высоте ---
        vstep = nice_step(span_v, 5)
        for k in range(math.ceil(lo / vstep), math.floor(hi / vstep) + 1):
            v = k * vstep
            y = py(v)
            c.create_line(ml, y, ml + pw, y, fill=GRID)
            c.create_text(ml - 6, y, text=f"{v:g}", fill=DIM, anchor="e")

        # --- сетка по времени ---
        tstep = time_step(win) if win is not None else nice_step(span_t, 6)
        for k in range(int(span_t / tstep) + 1):
            ago = k * tstep
            x = px(right - ago)
            if x < ml:
                break
            c.create_line(x, mt, x, mt + ph, fill=GRID)
            c.create_text(x, mt + ph + 4, text=fmt_ago(ago), fill=DIM,
                          anchor="ne" if k == 0 else "n")

        # --- линия ---
        dt, dv = decimate(ts, vs, max(pw, 1))
        pts = [(px(t), py(v)) for t, v in zip(dt, dv)]
        # обрезка слева по рамке: точки левее рамки заменяем одной на границе
        if len(pts) >= 2 and pts[0][0] < ml:
            i = 0
            while i + 1 < len(pts) and pts[i + 1][0] < ml:
                i += 1
            if i + 1 < len(pts):
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                yc = y0 + (y1 - y0) * (ml - x0) / max(x1 - x0, 1e-6)
                pts = [(ml, yc)] + pts[i + 1:]
            else:
                pts = pts[-1:]
        flat = []
        for x, y in pts:
            flat += [min(x, ml + pw), min(max(y, mt), mt + ph)]
        if len(flat) >= 4:
            c.create_line(*flat, fill=OK, width=2)

        # --- текущее значение ---
        lx, ly = flat[-2], flat[-1]
        c.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=OK, outline="")
        c.create_text(min(lx + 8, ml + pw - 4), max(ly - 10, mt + 8),
                      text=f"{vs[-1]:.2f} м", fill=FG,
                      anchor="w" if lx + 60 < ml + pw else "e")

    THEME_OPTIONS = ("bg", "fg", "activebackground", "activeforeground",
                     "disabledforeground", "selectcolor", "insertbackground",
                     "highlightbackground", "highlightcolor", "disabledbackground")

    def apply_theme(name: str) -> None:
        """Переключить тему: перекрасить всё созданное и сохранить выбор."""
        nonlocal BG, PANEL, FG, DIM, OK, WARN, BAD, BTN, BTN_ACTIVE, LOGBG, GRID
        nonlocal GO, SEL, STOP, ACCENT, OFF
        old, new = PALETTES[theme["name"]], PALETTES[name]
        if old is new:
            return
        mapping = {old[k].lower(): new[k] for k in new}

        BG, PANEL, FG, DIM = new["BG"], new["PANEL"], new["FG"], new["DIM"]
        OK, WARN, BAD = new["OK"], new["WARN"], new["BAD"]
        BTN, BTN_ACTIVE, LOGBG, GRID = new["BTN"], new["BTN_ACTIVE"], new["LOGBG"], new["GRID"]
        GO, SEL, STOP, ACCENT = new["GO"], new["SEL"], new["STOP"], new["ACCENT"]
        OFF = new["OFF"]

        def walk_widgets(w):
            yield w
            for c in w.winfo_children():
                yield from walk_widgets(c)

        for w in walk_widgets(root):
            for opt in THEME_OPTIONS:
                try:
                    cur = str(w.cget(opt)).lower()
                except tk.TclError:
                    continue
                if cur in mapping:
                    try:
                        w.config(**{opt: mapping[cur]})
                    except tk.TclError:
                        pass
            if isinstance(w, tk.Text):
                # Цвет строк журнала живёт в тегах: их тоже перекрашиваем.
                for tag in w.tag_names():
                    try:
                        cur = str(w.tag_cget(tag, "foreground")).lower()
                    except tk.TclError:
                        continue
                    if cur in mapping:
                        w.tag_config(tag, foreground=mapping[cur])

        theme["name"] = name
        theme_btn.config(text="Светлая тема" if name == "dark" else "Тёмная тема")
        save_theme(settings_path_for(log_dir), name)
        refit_text()

    import tkinter.font as tkfont
    orig_weight = {}

    def luminance(color: str) -> float:
        try:
            r, g, b = (c / 65535.0 for c in root.winfo_rgb(color))
        except tk.TclError:
            return 0.5

        def lin(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    def contrast(c1: str, c2: str) -> float:
        l1, l2 = luminance(c1), luminance(c2)
        hi, lo = max(l1, l2), min(l1, l2)
        return (hi + 0.05) / (lo + 0.05)

    def readable(fg: str, bg: str) -> str:
        """fg, если он различим на bg, иначе чёрный или белый."""
        if contrast(fg, bg) >= 4.0:
            return fg
        return "#000000" if luminance(bg) > 0.4 else "#ffffff"

    def refit_text() -> None:
        """
        Светлая тема: весь шрифт жирный (виднее на солнце). Любая тема:
        текст, который сливается с фоном (светлое на светлом, тёмное на
        тёмном), принудительно становится чёрным или белым.
        """
        light = theme["name"] == "light"

        def walk_all(w):
            yield w
            for c in w.winfo_children():
                yield from walk_all(c)

        for w in walk_all(root):
            try:
                fnt = w.cget("font")
            except tk.TclError:
                fnt = None
            if fnt:
                try:
                    a = tkfont.Font(root=root, font=fnt).actual()
                    key = str(w)
                    orig_weight.setdefault(key, a["weight"])
                    want = "bold" if light else orig_weight[key]
                    if a["weight"] != want:
                        w.config(font=(a["family"], a["size"], want))
                except tk.TclError:
                    pass

            try:
                bg = str(w.cget("bg"))
            except tk.TclError:
                continue
            for opt in ("fg",):
                try:
                    fg = str(w.cget(opt))
                except tk.TclError:
                    continue
                if not fg:
                    continue
                fixed = readable(fg, bg)
                if fixed != fg:
                    try:
                        w.config(**{opt: fixed})
                    except tk.TclError:
                        pass
            if isinstance(w, tk.Text):
                for tag in w.tag_names():
                    try:
                        fg = str(w.tag_cget(tag, "foreground"))
                    except tk.TclError:
                        continue
                    if fg:
                        fixed = readable(fg, bg)
                        if fixed != fg:
                            w.tag_config(tag, foreground=fixed)

    def graph_tick() -> None:
        try:
            draw_graph()
        finally:
            root.after(50, graph_tick)

    graph.bind("<Configure>", lambda _e: draw_graph())

    def show_battery(tile, volts) -> None:
        """Вольты и проценты: «9.31 В · 98 %». Красный ниже порога, серый при питании только от USB."""
        if volts is None or volts <= 0:
            tile.config(text="—", fg=DIM)
        elif volts < VBAT_USB_ONLY_V:
            tile.config(text=f"{volts:.2f} В  (только USB?)", fg=DIM)
        elif volts < VBAT_LOW_V:
            tile.config(text=vro_battery.fmt(volts, st["full_v"], st["empty_v"]) + "  НИЗКОЕ", fg=BAD)
        else:
            pct = vro_battery.percent(volts, st["full_v"], st["empty_v"]) or 0
            tile.config(text=vro_battery.fmt(volts, st["full_v"], st["empty_v"]),
                        fg=OK if pct >= 30 else WARN)

    def fix_battery_empty() -> None:
        """Принять текущее напряжение за 0 % (аккумулятор разряжен до отказа)."""
        v = st["vbat"]
        if not vro_battery.valid_empty(v, st["full_v"]):
            shown = "неизвестно" if v is None else f"{v:.2f} В"
            messagebox.showwarning(
                "Батарея = 0 %",
                f"Сейчас напряжение: {shown}. Зафиксировать как 0 % можно от 5,5 В до "
                f"{st['full_v'] - 0.2:.2f} В (заметно ниже уровня 100 %). Нажимайте, когда "
                "аккумулятор разряжен настолько, что плата вот-вот выключится.")
            return
        st["empty_v"] = v
        save_settings(settings_path_for(log_dir), battery_empty_v=v)
        console_add(f"[{datetime.now():%H:%M:%S}] 0 % батареи зафиксировано: {v:.2f} В "
                    f"(100 % = {st['full_v']:.2f} В)", OK)

    def fix_battery_full() -> None:
        """Принять текущее напряжение за 100 % (батарея сейчас свежая)."""
        v = st["vbat"]
        if not vro_battery.valid_full(v):
            shown = "неизвестно" if v is None else f"{v:.2f} В"
            messagebox.showwarning(
                "Батарея = 100 %",
                f"Сейчас напряжение: {shown}. Зафиксировать как 100 % можно только "
                f"{vro_battery.FULL_MIN_V:.1f}–{vro_battery.FULL_MAX_V:.1f} В "
                "(свежая батарея под нагрузкой платы). Проверьте, что батарея "
                "подключена и данные идут.")
            return
        st["full_v"] = v
        if not vro_battery.valid_empty(st["empty_v"], v):
            st["empty_v"] = vro_battery.EMPTY_V
        save_settings(settings_path_for(log_dir), battery_full_v=v)
        console_add(f"[{datetime.now():%H:%M:%S}] 100 % батареи зафиксировано: {v:.2f} В "
                    f"(0 % = {st['empty_v']:.2f} В)", OK)

    def set_ready(level: str, text: str) -> None:
        """Статус готовности в углу: цвет по уровню, текст читаемый на любом фоне."""
        if not text:
            ready_lbl.config(text="", bg=BG)
            return
        bg = {"ok": OK, "warn": WARN, "bad": BAD, "info": ACCENT, "off": BTN}[level]
        fg = "#000000" if luminance(bg) > 0.35 else "#ffffff"
        ready_lbl.config(text=text, bg=bg, fg=fg)

    def gate_buttons(buttons, allowed) -> None:
        """Выключить кнопки, команды которых борт сейчас проигнорирует."""
        for b in buttons:
            cmd = getattr(b, "cmd", None)
            on = st["connected"] and cmd in allowed
            role = getattr(b, "role", None)
            if role:
                # Насыщенная кнопка в выключенном виде серая: иначе не видно, что она мертва.
                b.config(bg={"STOP": STOP, "ACCENT": ACCENT}[role] if on else BTN,
                         fg="white" if on else OFF, disabledforeground=OFF)
            b.config(state="normal" if on else "disabled")

    def update_readiness(now: float) -> None:
        mode = st["mode"]
        age = (now - st["last_data"]) if st["last_data"] else None
        if not (is_radio(mode) or mode == MODE_DEBUG):
            set_ready("off", "")
            return
        if is_radio(mode):
            p = st["session"].last_packet
            state, rec, flags = (p.state, p.recovery, p.error_flags) if p else (None, None, 0)
        elif mode == MODE_DEBUG:
            d = st["debug"]
            state, rec, flags = (d.state, d.recovery, d.error_flags) if d else (None, None, 0)
        else:
            return

        if not st["connected"]:
            set_ready("off", "не подключено")
            allowed = set()
        else:
            level, text = vro_ready.readiness(state, rec, flags, age)
            set_ready(level, vro_ready.short(text))
            fresh = age is not None and age <= vro_ready.DATA_TIMEOUT_S
            allowed = vro_ready.allowed_commands(state if fresh else None, rec,
                                                 wired=(mode == MODE_DEBUG))
        if mode == MODE_RADIO_DEBUG:
            gate_buttons(rd_buttons, allowed)
        elif mode == MODE_DEBUG:
            gate_buttons(servo_buttons, allowed)

    def refresh_display() -> None:
        drain_queue()
        drain_flash()
        if st["cmdr"] is not None:
            st["cmdr"].tick(time.time())
            status = st["cmdr"].status
            lp = st["session"].last_packet
            hint = ""
            if lp is not None:
                hint = (not_ready_hint(lp.state) if lp.state != "READY"
                        else deployed_hint(lp.state, lp.recovery))
            cmd_now = st["cmdr"].last_cmd
            em_status.config(text=status if cmd_now == "D" else "",
                             fg=OK if "ПОДТВЕРЖДЕНО" in status else BAD)
            rd_status.config(text="\n".join(t for t in (status, hint) if t),
                             fg=OK if "ПОДТВЕРЖДЕНО" in status and not hint
                             else BAD if ("НЕТ" in status or hint) else WARN)
        now = time.time()
        update_readiness(now)

        if st["connected"] and st["mode"] != MODE_FLASH:
            silent = now - st["last_data"]
            if st["last_data"] == 0.0:
                flow_dot.config(fg=WARN)
                flow_text.config(text="порт открыт, данных пока нет", fg=WARN)
            elif silent > DATA_TIMEOUT_S:
                flow_dot.config(fg=BAD)
                flow_text.config(text=f"данные не поступают {silent:.0f} с", fg=BAD)
            else:
                flow_dot.config(fg=OK)
                flow_text.config(text="данные поступают", fg=OK)

        if st["mode"] == MODE_FLASH:
            link_stats.config(text="")
        elif is_radio(st["mode"]):
            session: Session = st["session"]
            p = session.last_packet
            if p is not None:
                r_tiles["state"].config(text=p.state_ru, fg=BAD if p.state == "ERROR" else FG)
                r_tiles["alt"].config(text=f"{p.altitude_m:.2f}")
                r_tiles["max"].config(text=f"{session.max_altitude:.2f}")
                r_tiles["press"].config(text=f"{p.pressure_pa}")
                r_tiles["rec"].config(text=p.recovery_ru,
                                      fg=OK if p.recovery == "DEPLOYED" else FG)
                r_tiles["t"].config(text=f"{p.time_ms / 1000.0:.1f}")
                r_tiles["seq"].config(text=f"{p.seq}")
                errs = describe_errors(p.error_flags)
                r_err.config(text=(("КРИТИЧЕСКАЯ ОШИБКА: " if p.is_critical else "Ошибки: ")
                                   + "; ".join(errs)) if errs else "",
                             fg=BAD if p.is_critical else WARN)
            fs: FlightStats = st["stats"]

            def fmt(v, spec, unit=""):
                return "—" if v is None else format(v, spec) + unit

            r2_tiles["apo"].config(text=fmt(fs.max_alt, ".1f")
                                   + (f"  @ {fs.time_to_apogee:.1f} с" if fs.time_to_apogee is not None else ""))
            r2_tiles["v"].config(text=fmt(fs.v_now, "+.1f"))
            r2_tiles["vup"].config(text=fmt(fs.v_up, ".1f"))
            r2_tiles["vdesc"].config(text=fmt(fs.descent_speed_avg, ".1f"))
            r2_tiles["acc"].config(text=fmt(fs.a_max, ".2f"))
            r2_tiles["dep"].config(
                text=("—" if fs.deploy_time_from_launch is None else
                      f"{fs.deploy_time_from_launch:.1f} с, {fmt(fs.deploy_altitude, '.0f')} м"),
                fg=OK if fs.deploy_time_from_launch is not None else FG)
            r2_tiles["ft"].config(text=fmt(fs.flight_time, ".1f"))
            if p is not None and p.vbat_v is not None:
                st["vbat"] = p.vbat_v
            show_battery(r2_tiles["bat"], st["vbat"])

            recent = [a for a in st["arrivals"] if now - a <= 5.0]
            rate = len(recent) / 5.0
            age = f"{now - st['arrivals'][-1]:.1f} с назад" if st["arrivals"] else "—"
            link_stats.config(
                text=f"принято {session.received}  потеряно {session.lost} "
                     f"({session.loss_percent:.1f}%)  повторов {session.duplicates}  "
                     f"испорчено {session.bad_lines}  |  {rate:.1f} пак/с  |  "
                     f"последний: {age}")
        else:
            s = st["debug"]
            if s is not None:
                d_tiles["state"].config(text=s.state, fg=BAD if s.state == "ERROR" else FG)
                d_tiles["alt"].config(text=f"{s.altitude_m:.2f}")
                d_tiles["acc"].config(text=f"{s.accel_g:.2f}")
                d_tiles["press"].config(text=f"{s.pressure_pa}")
                d_tiles["rec"].config(text=s.recovery,
                                      fg=OK if s.recovery == "DEPLOYED" else FG)
                d_tiles["t"].config(text=f"{s.time_ms / 1000.0:.1f}")
                if s.vbat_v is not None:
                    st["vbat"] = s.vbat_v
                show_battery(d_tiles["vb"], s.vbat_v)
                errs = describe_errors(s.error_flags)
                lines = []
                if errs:
                    lines.append("Ошибки: " + "; ".join(errs))
                if s.state != "READY":
                    lines.append(not_ready_hint(s.state))
                elif deployed_hint(s.state, s.recovery):
                    lines.append(deployed_hint(s.state, s.recovery))
                d_err.config(text="\n".join(lines), fg=WARN, justify="left")
            link_stats.config(text="")

        root.after(150, refresh_display)

    def on_close() -> None:
        do_disconnect()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    refresh_ports()
    build_fw_list()
    show_mode(MODE_RADIO)
    add_line(f_log, "Выберите прошивку, порт платы сверху и нажмите «Прошить плату».", DIM)
    add_line(r_log, "Боевой режим: подключите FT232RL с HC-12, скорость 9600, «Подключиться».", DIM)
    add_line(d_log, "Отладка: подключите плату USB-кабелем, скорость 115200, «Подключиться».", DIM)
    refit_text()
    refresh_display()
    graph_tick()
    root.mainloop()
    return 0


# --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Станция и отладка ВРО-1")
    ap.add_argument("--console", action="store_true", help="без окна")
    ap.add_argument("--mode", choices=[MODE_RADIO, MODE_RADIO_DEBUG, MODE_DEBUG], default=MODE_DEBUG)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--log-dir", default=LOG_DIR_DEFAULT)
    ap.add_argument("--seconds", type=int)
    ap.add_argument("--cmd", default="",
                    help="команды платы через запятую, например \"d,s\" (только --console)")
    ap.add_argument("--list", action="store_true", help="показать порты и выйти")
    a = ap.parse_args()

    if a.list:
        for dev, desc, likely in list_ports():
            print(f"{dev:<24} {desc}" + ("   [переходник/плата]" if likely else ""))
        return 0

    if a.console:
        if not a.port:
            print("В режиме без окна нужен --port. Список: --list")
            return 2
        return run_console(a.mode, a.port, a.baud or DEFAULT_BAUD[a.mode],
                           a.log_dir, a.seconds, a.cmd)

    return run_gui(a.log_dir)


if __name__ == "__main__":
    sys.exit(main())
