#!/usr/bin/env python3
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
import os
import queue
import sys
import time
from datetime import datetime
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

from telemetry import Session, Event, Packet, describe_errors
from vro_link import (
    PortLink, CsvLog, RADIO_COLUMNS, DEBUG_COLUMNS, radio_row, debug_row,
    sent_row, parse_debug_line, angle_command, angle_actual,
    CMD_SAFE, CMD_DEPLOY, CMD_CYCLE, CMD_SIM, CMD_INFO, CMD_HELP, ANGLE_STEP,
    ANGLE_MAX,
)
from rocket_ground import list_ports, DATA_TIMEOUT_S
import vro_flash

MODE_RADIO = "radio"
MODE_DEBUG = "debug"
MODE_FLASH = "flash"

DEFAULT_BAUD = {MODE_RADIO: 9600, MODE_DEBUG: 115200, MODE_FLASH: 115200}

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
#  Режим без окна
# --------------------------------------------------------------------

def run_console(mode: str, port: str, baud: int, log_dir: str,
                seconds: Optional[int], commands: str) -> int:
    q: "queue.Queue" = queue.Queue()
    link = PortLink(port, baud, q)
    session = Session()
    columns = RADIO_COLUMNS if mode == MODE_RADIO else DEBUG_COLUMNS
    log = CsvLog(log_dir, "radio" if mode == MODE_RADIO else "debug", columns)

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
                if mode == MODE_RADIO:
                    log.write(radio_row(session, payload))
                else:
                    log.write(debug_row(payload))
                    if parse_debug_line(payload) is None:
                        print(f"  борт: {payload}")

            now = time.time()
            if script and now >= next_cmd_at:
                cmd = script.pop(0)
                data = cmd.encode()
                if link.send(data):
                    log.write(sent_row(data))
                    print(f"  -> команда {cmd!r}")
                next_cmd_at = now + 1.5

            if now - last_report >= 1.0:
                last_report = now
                if mode == MODE_RADIO and session.last_packet:
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

    BG, PANEL = "#101418", "#161b22"
    FG, DIM = "#e6edf3", "#8b949e"
    OK, WARN, BAD = "#3fb950", "#d29922", "#f85149"

    root = tk.Tk()
    root.title("ВРО-1 — станция и отладка")
    root.configure(bg=BG)
    root.minsize(980, 700)

    q: "queue.Queue" = queue.Queue()
    st = {"link": None, "log": None, "session": Session(), "mode": MODE_RADIO,
          "last_data": 0.0, "connected": False,
          "arrivals": collections.deque(maxlen=200),
          "alt_hist": collections.deque(maxlen=900),
          "debug": None}

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    def label(parent, text, fg=FG, size=11, bold=False, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", BG), fg=fg,
                        font=("TkDefaultFont", size, "bold" if bold else "normal"),
                        **kw)

    def button(parent, text, cmd, bg="#21262d", fg=FG, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief="flat", padx=kw.pop("padx", 12),
                         pady=kw.pop("pady", 4), activebackground="#30363d",
                         activeforeground=FG, disabledforeground="#484f58", **kw)

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

    for text, val in (("Боевой (радио)", MODE_RADIO), ("Отладка (провод)", MODE_DEBUG),
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

    connect_btn = button(top, "Подключиться", lambda: toggle(), bg="#238636",
                         fg="white", padx=18)
    connect_btn.pack(side="left", padx=12)

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

    content = tk.Frame(root, bg=BG)
    content.pack(fill="both", expand=True, padx=14)

    # ======== панель боевого режима ========
    radio_frame = tk.Frame(content, bg=BG)
    r_tiles = make_tiles(radio_frame, [
        ("state", "СОСТОЯНИЕ", True), ("alt", "ВЫСОТА, м", True),
        ("max", "МАКСИМУМ, м", False), ("press", "ДАВЛЕНИЕ, Па", False),
        ("rec", "СПАСЕНИЕ", False), ("t", "ВРЕМЯ БОРТА, с", False),
        ("seq", "ПАКЕТ №", False)])
    r_err = label(radio_frame, "", fg=BAD, bold=True, anchor="w", justify="left")
    r_err.pack(fill="x", pady=(2, 0))

    graph = tk.Canvas(radio_frame, bg="#0d1117", height=170, highlightthickness=0)
    graph.pack(fill="x", pady=6)

    label(radio_frame, "События и связь", fg=DIM, anchor="w").pack(fill="x")
    r_log = tk.Text(radio_frame, bg="#0d1117", fg=FG, relief="flat", height=9,
                    font=("TkFixedFont", 10), wrap="none")
    r_log.pack(fill="both", expand=True)

    # ======== панель отладки по проводу ========
    debug_frame = tk.Frame(content, bg=BG)
    d_tiles = make_tiles(debug_frame, [
        ("state", "СОСТОЯНИЕ", True), ("alt", "ВЫСОТА, м", True),
        ("acc", "|a|, g", False), ("press", "ДАВЛЕНИЕ, Па", False),
        ("rec", "ПРИВОД", False), ("vb", "БАТАРЕЯ, В", False),
        ("t", "ВРЕМЯ БОРТА, с", False)])
    d_err = label(debug_frame, "", fg=BAD, bold=True, anchor="w", justify="left")
    d_err.pack(fill="x", pady=(2, 0))

    servo = tk.LabelFrame(debug_frame, text=" Управление приводом (парашют) ",
                          bg=BG, fg=FG, padx=10, pady=8)
    servo.pack(fill="x", pady=6)
    servo_buttons = []

    def send_cmd(data: bytes, note: str = "") -> None:
        link = st["link"]
        if link is None or not st["connected"]:
            return
        if link.send(data):
            if st["log"] is not None:
                st["log"].write(sent_row(data))
            console_add(f"[{datetime.now():%H:%M:%S}] -> {note or data.decode()}", WARN)

    row1 = tk.Frame(servo, bg=BG)
    row1.pack(fill="x")
    for text, data, colour in (
            ("SAFE (закрыто)", CMD_SAFE, "#21262d"),
            ("DEPLOY (раскрыть)", CMD_DEPLOY, "#8b2c2c"),
            ("Цикл SAFE→DEPLOY→SAFE", CMD_CYCLE, "#21262d"),
            ("Прогон профиля полёта", CMD_SIM, "#21262d"),
            ("Сведения о плате", CMD_INFO, "#21262d"),
            ("Справка", CMD_HELP, "#21262d")):
        b = button(row1, text, lambda d=data, t=text: send_cmd(d, t), bg=colour)
        b.pack(side="left", padx=(0, 8))
        servo_buttons.append(b)

    row2 = tk.Frame(servo, bg=BG)
    row2.pack(fill="x", pady=(8, 0))
    label(row2, f"Угол, шаг {ANGLE_STEP}°:", fg=DIM).pack(side="left", padx=(0, 8))
    for deg in range(0, ANGLE_MAX + 1, ANGLE_STEP):
        b = button(row2, f"{deg}°", lambda d=deg: send_cmd(angle_command(d), f"угол {angle_actual(d)}°"),
                   padx=8)
        b.pack(side="left", padx=2)
        servo_buttons.append(b)

    label(servo, "Команды работают только в состоянии READY, в полёте борт их не читает. "
                 "Привод подключать до подачи питания.", fg=DIM, size=9,
          anchor="w", justify="left").pack(fill="x", pady=(8, 0))

    row3 = tk.Frame(debug_frame, bg=BG)
    row3.pack(fill="x")
    label(row3, "Вывод платы", fg=DIM).pack(side="left")
    raw_var = tk.StringVar()
    raw_entry = tk.Entry(row3, textvariable=raw_var, width=10, bg=PANEL, fg=FG,
                         insertbackground=FG, relief="flat")
    raw_entry.pack(side="right", padx=(6, 0))
    label(row3, "своя команда (символ):", fg=DIM).pack(side="right")

    def send_raw(_evt=None) -> None:
        text = raw_var.get()
        if text:
            send_cmd(text.encode("utf-8"), f"«{text}»")
            raw_var.set("")

    raw_entry.bind("<Return>", send_raw)

    d_log = tk.Text(debug_frame, bg="#0d1117", fg=FG, relief="flat", height=10,
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
    fw_box = tk.Frame(flash_frame, bg=BG)
    fw_box.pack(fill="x")

    def build_fw_list() -> None:
        for w in fw_box.winfo_children():
            w.destroy()
        fws = vro_flash.available_firmwares()
        if not fws:
            label(fw_box, "Готовых прошивок рядом с программой нет. "
                          "Выберите свой файл .hex.", fg=WARN, anchor="w").pack(fill="x")
        for key, text, path in fws:
            tk.Radiobutton(fw_box, text=text, value=key, variable=fw_var,
                           bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                           activeforeground=FG, anchor="w",
                           font=("TkDefaultFont", 11)).pack(fill="x", pady=1)
        if fws:
            fw_var.set(next((k for k, _, _ in fws if k == "c_radio"), fws[0][0]))
        tk.Radiobutton(fw_box, text="Свой файл .hex", value="custom", variable=fw_var,
                       bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, anchor="w",
                       font=("TkDefaultFont", 11)).pack(fill="x", pady=1)

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

    frow = tk.Frame(flash_frame, bg=BG)
    frow.pack(fill="x", pady=(8, 4))
    button(frow, "Выбрать файл .hex...", pick_hex).pack(side="left")
    flash_btn = button(frow, "Прошить плату", lambda: do_flash(), bg="#238636",
                       fg="white", padx=22, pady=6)
    flash_btn.pack(side="left", padx=14)
    custom_label.pack(fill="x")
    label(flash_frame, "Порт берётся из списка сверху, плата подключена USB-кабелем "
                       "(нужен кабель с передачей данных). Пока идёт прошивка, кабель не "
                       "отключать. После прошивки подождите 3 секунды.",
          fg=DIM, size=9, anchor="w", justify="left", wraplength=900).pack(fill="x")
    f_log = tk.Text(flash_frame, bg="#0d1117", fg=FG, relief="flat", height=14,
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
        {MODE_RADIO: radio_frame, MODE_DEBUG: debug_frame,
         MODE_FLASH: flash_frame}[mode].pack(fill="both", expand=True)
        is_flash = mode == MODE_FLASH
        connect_btn.config(state="disabled" if is_flash else "normal")
        baud_box.config(state="disabled" if is_flash else "normal")
        if is_flash:
            flow_dot.config(fg=DIM)
            flow_text.config(text="режим прошивки", fg=DIM)
        set_servo_state()

    def set_servo_state() -> None:
        on = st["connected"] and st["mode"] == MODE_DEBUG
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
        st["alt_hist"].clear()
        st["debug"] = None
        st["last_data"] = 0.0
        st["log"] = CsvLog(log_dir, "radio" if mode == MODE_RADIO else "debug",
                           RADIO_COLUMNS if mode == MODE_RADIO else DEBUG_COLUMNS)
        file_label.config(text=f"журнал: {st['log'].path}")

        link = PortLink(port, baud, q)
        link.start()
        st["link"] = link
        st["connected"] = True
        connect_btn.config(text="Отключиться", bg="#8b2c2c")
        port_box.config(state="disabled")
        set_servo_state()
        console_add(f"[{datetime.now():%H:%M:%S}] подключение к {port} @ {baud}", DIM)

    def do_disconnect(reason: str = "") -> None:
        if st["link"] is not None:
            st["link"].stop()
            st["link"] = None
        if st["log"] is not None:
            st["log"].close()
            st["log"] = None
        st["connected"] = False
        connect_btn.config(text="Подключиться", bg="#238636")
        port_box.config(state="readonly")
        flow_dot.config(fg=DIM)
        flow_text.config(text="не подключено", fg=DIM)
        set_servo_state()
        if reason:
            console_add(f"[{datetime.now():%H:%M:%S}] {reason}", WARN)

    def toggle() -> None:
        do_disconnect("отключено оператором") if st["connected"] else do_connect()

    # ---------------- обработка данных ----------------

    def handle_radio_line(line: str) -> None:
        session: Session = st["session"]
        events_before = len(session.events)
        row = radio_row(session, line)
        st["log"].write(row)
        kind = row["kind"]
        if kind == "packet":
            st["arrivals"].append(time.time())
            p: Packet = session.last_packet
            st["alt_hist"].append((p.time_ms / 1000.0, p.altitude_m))
        elif kind == "event" and len(session.events) > events_before:
            e = session.events[-1]
            add_line(r_log, f"{e.time_ms / 1000.0:>9.2f} с   {e.name}", WARN)
        elif kind == "bad":
            add_line(r_log, f"испорчена: {line[:70]}", DIM)

    def handle_debug_line(line: str) -> None:
        st["log"].write(debug_row(line))
        s = parse_debug_line(line)
        if s is not None:
            st["debug"] = s
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
                if st["mode"] == MODE_RADIO:
                    handle_radio_line(payload)
                else:
                    handle_debug_line(payload)

    def draw_graph() -> None:
        graph.delete("all")
        pts = list(st["alt_hist"])
        w = graph.winfo_width() or 900
        h = graph.winfo_height() or 170
        if len(pts) < 2:
            graph.create_text(w // 2, h // 2, text="график высоты", fill=DIM)
            return
        t0, t1 = pts[0][0], pts[-1][0]
        lo = min(p[1] for p in pts)
        hi = max(p[1] for p in pts)
        hi = max(hi, lo + 1.0)
        pad = 24
        coords = []
        for t, a in pts:
            x = pad + (t - t0) / max(t1 - t0, 0.001) * (w - 2 * pad)
            y = h - pad - (a - lo) / (hi - lo) * (h - 2 * pad)
            coords += [x, y]
        graph.create_line(*coords, fill=OK, width=2)
        graph.create_text(6, pad, text=f"{hi:.1f} м", fill=DIM, anchor="w")
        graph.create_text(6, h - pad, text=f"{lo:.1f} м", fill=DIM, anchor="w")

    def refresh_display() -> None:
        drain_queue()
        drain_flash()
        now = time.time()

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
        elif st["mode"] == MODE_RADIO:
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
            recent = [a for a in st["arrivals"] if now - a <= 5.0]
            rate = len(recent) / 5.0
            age = f"{now - st['arrivals'][-1]:.1f} с назад" if st["arrivals"] else "—"
            link_stats.config(
                text=f"принято {session.received}  потеряно {session.lost} "
                     f"({session.loss_percent:.1f}%)  повторов {session.duplicates}  "
                     f"испорчено {session.bad_lines}  |  {rate:.1f} пак/с  |  "
                     f"последний: {age}")
            draw_graph()
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
                if s.vbat_v is None:
                    d_tiles["vb"].config(text="—", fg=DIM)
                elif s.vbat_v < VBAT_USB_ONLY_V:
                    d_tiles["vb"].config(text=f"{s.vbat_v:.2f}  (только USB?)", fg=DIM)
                elif s.vbat_v < VBAT_LOW_V:
                    d_tiles["vb"].config(text=f"{s.vbat_v:.2f}  НИЗКОЕ", fg=BAD)
                else:
                    d_tiles["vb"].config(text=f"{s.vbat_v:.2f}", fg=OK)
                errs = describe_errors(s.error_flags)
                d_err.config(text="Ошибки: " + "; ".join(errs) if errs else "", fg=WARN)
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
    refresh_display()
    root.mainloop()
    return 0


# --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Станция и отладка ВРО-1")
    ap.add_argument("--console", action="store_true", help="без окна")
    ap.add_argument("--mode", choices=[MODE_RADIO, MODE_DEBUG], default=MODE_DEBUG)
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
