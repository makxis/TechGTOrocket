#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
HC-12 radio link test with antennas at full power.

This script tests bidirectional HC-12 communication and measures packet loss
over the air at different distances. It uses the hc12_setup sketch for
both TX and RX sides and measures loss via sequence gaps in the telemetry.

Requirements:
  - Two boards flashed with hc12_setup, both at power P8 (+20 dBm)
  - Ground receiver connected via serial (FT232RL or second hc12_setup board)
  - Modules separated by at least a few metres or in different rooms

Usage:

    # List available ports and confirm antennas are connected
    python3 ground/rocket_ground.py --list

    # Prerequisite: set both HC-12 modules to P8
    # Flash tools/hc12_setup and press '8' on each, confirm with '~': OK+RP:+20dBm

    # Option A: Test point-to-point (two hc12_setup boards side by side)
    # On board A in picocom: press 'p' to send test packets (5 Hz, TZ format)
    # On board B in picocom: watch the sequence numbers for gaps

    # Option B: Full telemetry link test (flight simulation + ground station)
    python3 tools/radio_test.py --rocket /dev/ttyACM0 --ground /dev/ttyACM1

    # Option C: Measure link quality at multiple distances
    python3 tools/radio_test.py --rocket /dev/ttyACM0 --ground /dev/ttyACM1 \
        --distances "0.5m,2m,5m,10m" --duration-per 30

Statistics reported:
  - Total packets sent and expected
  - Packets lost (via sequence gaps) and loss %
  - Board-side dropped packets (rdrop) — if >0, some telemetry was lost onboard
  - Bad lines (garbled/unparseable) — rare with HC-12

If loss % is high but rdrop=0, the packets were lost over the air (signal).
If rdrop > 0, the flight computer was busy and dropped telemetry internally.
"""

import argparse
import os
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "ground"))
sys.path.insert(0, os.path.join(HERE, "..", "ground", "vendor"))

import serial
from telemetry import Session


def listen(port, baud, seconds, session, stop, echo_events=True, raw_path=None):
    """Читать порт и кормить строки в Session до истечения времени или stop."""
    ser = serial.Serial(port, baud, timeout=0.1)
    buf = b""
    rawf = open(raw_path, "wb") if raw_path else None
    end = time.time() + seconds
    try:
        while time.time() < end and not stop.is_set():
            chunk = ser.read(1024)
            if rawf:
                rawf.write(chunk)
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                item = session.feed(line)
                if echo_events and item is None and line.startswith("#"):
                    print(f"  {line}")
    finally:
        ser.close()
        if rawf:
            rawf.close()


def report(session, want_events=0):
    print("\n[РЕЗУЛЬТАТ]")
    print(f"  принято:   {session.received}")
    print(f"  потеряно:  {session.lost} ({session.loss_percent:.1f}%)")
    if session.duplicates:
        print(f"  принятых повторов: {session.duplicates}")
    print(f"  мусорных строк: {session.bad_lines}, перезапусков борта: {session.restarts}")
    print(f"  событий: {len(session.events)}")
    for e in session.events:
        print(f"    {e}")
    for pr in session.problems[:5]:
        print(f"    ! {pr}")
    pct = session.loss_percent
    if session.received == 0:
        print("✗ FAIL: ничего не принято")
    elif want_events and len(session.events) < want_events:
        print(f"✗ FAIL: событий {len(session.events)} из {want_events}, часть потеряна в эфире")
    elif pct >= 5:
        print("✗ FAIL: потери > 5%")
    elif session.bad_lines or pct >= 1:
        print("⚠ WARN: есть потери или испорченные строки")
    else:
        print("✓ PASS")


def test_p2p(ground, baud, seconds):
    print(f"\n=== p2p, {seconds} с ===")
    print("На передающей плате должна быть нажата 'p' (5 Гц).")
    s = Session()
    listen(ground, baud, seconds, s, threading.Event())
    report(s)
    return s


def test_flight(rocket, ground, baud, seconds, raw_path=None):
    print(f"\n=== имитация полёта, {seconds} с ===")
    s = Session()
    stop = threading.Event()
    t = threading.Thread(target=listen, args=(ground, baud, seconds, s, stop, True, raw_path))
    t.start()
    time.sleep(1)
    r = serial.Serial(rocket, 115200, timeout=1)
    time.sleep(0.5)
    r.write(b"r")
    r.flush()
    print(f"[БОРТ] 'r' отправлена в {rocket}")
    log = r.read(4096).decode("utf-8", errors="replace").strip()
    r.close()
    started = "прогон тестового профиля" in log
    if not started:
        print("[БОРТ] прогон не стартовал: борт не в READY (уже сел или не прошла "
              "инициализация). Сбросьте борт: питание или 1200 бод, и повторите.")
    t.join()
    if not started:
        print("✗ FAIL: недействительный прогон")
        return s
    report(s, want_events=6)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rocket", help="порт борта (для имитации полёта)")
    ap.add_argument("--ground", required=True, help="порт наземного приёмника")
    ap.add_argument("--ground-baud", type=int, default=115200,
                    help="скорость порта земли (115200 для hc12_setup, 9600 для FT232RL)")
    ap.add_argument("--duration", "--duration-per", dest="duration", type=int, default=35)
    ap.add_argument("--raw", help="сохранять принятые с земли байты в этот файл")
    ap.add_argument("--distances", help='метки дистанций, например "0.5m,2m,5m"')
    a = ap.parse_args()

    print(f"Проверка радиоканала HC-12, {datetime.now().isoformat(timespec='seconds')}")
    labels = [d.strip() for d in a.distances.split(",")] if a.distances else [None]
    for lab in labels:
        if lab:
            input(f"\nДистанция {lab}: расставьте модули и нажмите Enter...")
        if a.rocket:
            test_flight(a.rocket, a.ground, a.ground_baud, a.duration, a.raw)
        else:
            test_p2p(a.ground, a.ground_baud, a.duration)


if __name__ == "__main__":
    main()
