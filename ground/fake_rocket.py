#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — имитатор борта.

Выдаёт в порт такие же строки, какие передаёт настоящая ракета, чтобы
можно было проверить наземную программу, не имея ни радиомодуля, ни
самой ракеты.

Профиль повторяет тот, что зашит в бортовой прошивке (`src/sim.cpp`):
разгон, подъём по инерции, апогей, раскрытие, снижение, посадка.

Проверка наземной программы на одной машине, без всякого железа:

    # терминал 1: создать пару виртуальных портов
    socat -d -d pty,raw,echo=0,link=/tmp/rocket_tx pty,raw,echo=0,link=/tmp/rocket_rx

    # терминал 2: борт
    python3 fake_rocket.py --port /tmp/rocket_tx

    # терминал 3: земля
    python3 rocket_ground.py --console --port /tmp/rocket_rx

Ключ --loss задаёт долю теряемых пакетов: так проверяется подсчёт
потерь, которого требует п. 45 ТЗ.
"""

import argparse
import math
import random
import sys
import time
from typing import Optional

# Параметры профиля повторяют src/sim.cpp.
BOOST_S = 0.25
BOOST_ACCEL = 70.0
GRAVITY = 9.81
CHUTE_SPEED = 5.0

GROUND_PRESSURE = 98450
PA_PER_METER = 11.8

# Шум барометра, замеренный на стенде: СКО высоты около 0,2 м.
ALT_NOISE_SD = 0.2

# Пороги повторяют config.h, чтобы события возникали там же, где на борту.
APOGEE_DROP_M = 2.0
APOGEE_CONFIRM_S = 0.15
LANDING_CONFIRM_S = 2.0


class Flight:
    """Модель полёта. Считается интегрированием, как и на борту."""

    def __init__(self) -> None:
        self.t = 0.0
        self.alt = 0.0
        self.vel = 0.0
        self.max_alt = 0.0

        self.state = "READY"
        self.recovery = "ARMED"
        self.launched_at: Optional[float] = None
        self.deployed = False
        self.landed = False

        self._drop_since: Optional[float] = None
        self._still_since: Optional[float] = None
        self.pending_events = []

    def _event(self, name: str) -> None:
        self.pending_events.append((int(self.t * 1000), name))

    def step(self, dt: float) -> None:
        self.t += dt

        if self.state == "READY":
            # Пара секунд на площадке, затем старт.
            if self.t >= 2.0:
                self.state = "BOOST"
                self.launched_at = self.t
                self._event("LAUNCH_DETECTED")
            return

        ft = self.t - (self.launched_at or self.t)

        if self.landed:
            return

        if self.state == "BOOST":
            self.vel += BOOST_ACCEL * dt
            self.alt += self.vel * dt
            if ft >= BOOST_S:
                self.state = "COAST"
                self._event("BOOST_END")

        elif not self.deployed:
            self.vel -= GRAVITY * dt
            self.alt += self.vel * dt

            if self.alt > self.max_alt:
                self.max_alt = self.alt

            # Обнаружение апогея тем же правилом, что и на борту.
            if (self.max_alt - self.alt) >= APOGEE_DROP_M:
                if self._drop_since is None:
                    self._drop_since = self.t
                elif self.t - self._drop_since >= APOGEE_CONFIRM_S:
                    self.state = "APOGEE"
                    self._event("APOGEE_CONFIRMED")
                    self.deployed = True
                    self.recovery = "DEPLOYED"
                    self._event("RECOVERY_DEPLOY")
                    self.state = "DESCENT"
                    self._event("DESCENT")
            else:
                self._drop_since = None

        else:
            self.vel = -CHUTE_SPEED
            self.alt += self.vel * dt

        if self.alt <= 0.0:
            self.alt = 0.0
            self.vel = 0.0
            if self._still_since is None:
                self._still_since = self.t
            elif self.t - self._still_since >= LANDING_CONFIRM_S:
                self.state = "LANDED"
                self._event("LANDED")
                self.landed = True

    @property
    def measured_alt(self) -> float:
        """Высота с шумом — так же, как её видит настоящий барометр."""
        return self.alt + random.gauss(0.0, ALT_NOISE_SD)

    @property
    def pressure(self) -> int:
        return int(GROUND_PRESSURE - self.alt * PA_PER_METER)


def run(port: Optional[str], rate: float, loss: float,
        errors: int, repeat: bool) -> int:
    out = None
    if port:
        try:
            import serial
        except ImportError:
            print("Не установлен pyserial: pip install pyserial", file=sys.stderr)
            return 1
        try:
            out = serial.Serial(port, 9600, timeout=1)
        except Exception as exc:
            print(f"Не открыть порт {port}: {exc}", file=sys.stderr)
            return 1

    def emit(text: str) -> None:
        if out is not None:
            out.write((text + "\r\n").encode("utf-8"))
            out.flush()
        else:
            print(text, flush=True)

    dt = 1.0 / rate
    seq = 0

    while True:
        flight = Flight()
        finished_at: Optional[float] = None

        while True:
            flight.step(dt)

            for t_ms, name in flight.pending_events:
                emit(f"#{t_ms}|{name}")
            flight.pending_events.clear()

            seq = (seq + 1) % 65536

            # Потеря пакета: номер всё равно израсходован, поэтому на
            # земле появится разрыв — ровно то, что нужно проверить.
            if random.random() >= loss:
                emit(f"{seq}|{int(flight.t * 1000)}|{flight.state}|"
                     f"{flight.measured_alt:.2f}|{flight.pressure}|"
                     f"{flight.recovery}|{errors}")

            if flight.landed:
                if finished_at is None:
                    finished_at = flight.t
                elif flight.t - finished_at > 2.0:
                    break

            time.sleep(dt)

        if not repeat:
            break

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Имитатор борта ВРО-1")
    ap.add_argument("--port", help="порт для выдачи; без него печать в терминал")
    ap.add_argument("--rate", type=float, default=5.0,
                    help="пакетов в секунду, по умолчанию 5 как на борту")
    ap.add_argument("--loss", type=float, default=0.0,
                    help="доля теряемых пакетов, 0..1")
    ap.add_argument("--errors", type=int, default=0,
                    help="значение поля флагов ошибок")
    ap.add_argument("--repeat", action="store_true",
                    help="повторять полёт бесконечно")
    args = ap.parse_args()

    return run(args.port, args.rate, args.loss, args.errors, args.repeat)


if __name__ == "__main__":
    sys.exit(main())
