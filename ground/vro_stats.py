# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — статистика полёта по принятой телеметрии (без окна).

В радиопакете есть только высота, давление и состояния, ускорения нет.
Поэтому скорости и ускорение здесь считаются по высоте с барометра:
скорость как наклон высоты за последнюю секунду, ускорение как наклон
скорости. Это оценка: барометр даёт доли метра и 5 отсчётов в секунду,
так что пики ускорения по нему занижены и «мягче» настоящих. Настоящее
ускорение видно только по проводу (|a| в режиме отладки).
"""

import csv
from collections import deque
from typing import Deque, List, Optional, Tuple

G = 9.80665

# Состояния, в которых ракета в воздухе.
IN_FLIGHT = ("BOOST", "COAST", "APOGEE", "DESCENT", "RECOVERY")

SPEED_WIN_S = 1.0     # окно расчёта скорости и ускорения
DEPLOY_SETTLE_S = 1.0  # после раскрытия рывок купола не считаем скоростью спуска


def _slope(points: "Deque[Tuple[float, float]]") -> Optional[float]:
    """Наклон прямой по методу наименьших квадратов, единиц значения в секунду."""
    n = len(points)
    if n < 3:
        return None
    mt = sum(p[0] for p in points) / n
    mv = sum(p[1] for p in points) / n
    den = sum((p[0] - mt) ** 2 for p in points)
    if den <= 1e-9:
        return None
    return sum((p[0] - mt) * (p[1] - mv) for p in points) / den


class FlightStats:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._h_win: Deque[Tuple[float, float]] = deque()   # (t, высота)
        self._v_win: Deque[Tuple[float, float]] = deque()   # (t, скорость)
        self._recent: Deque[Tuple[float, float]] = deque(maxlen=600)
        self.last_t: Optional[float] = None
        self.h_now: Optional[float] = None
        self.v_now: Optional[float] = None
        self.state: Optional[str] = None

        self.max_alt: Optional[float] = None
        self.max_alt_t: Optional[float] = None
        self.v_up: Optional[float] = None          # макс. скорость вверх, м/с
        self.v_down: Optional[float] = None        # макс. скорость вниз (отрицательная)
        self.a_max: Optional[float] = None         # макс. ускорение, g
        self.a_min: Optional[float] = None         # макс. замедление, g

        self.t_launch: Optional[float] = None
        self.t_deploy: Optional[float] = None
        self.h_deploy: Optional[float] = None
        self.t_land: Optional[float] = None
        self._launch_fb: Optional[float] = None    # запасной старт по состоянию
        self._deploy_fb: Optional[float] = None

        self._desc_sum = 0.0
        self._desc_n = 0
        self.packets = 0

    # ------------------------------------------------------------

    @property
    def launched(self) -> bool:
        return self._start() is not None

    def _start(self) -> Optional[float]:
        return self.t_launch if self.t_launch is not None else self._launch_fb

    def _deploy(self) -> Optional[float]:
        return self.t_deploy if self.t_deploy is not None else self._deploy_fb

    # ------------------------------------------------------------

    def add_packet(self, t: float, h: float, state: str, recovery: str) -> None:
        # Борт перезапустился: время пошло с нуля, начинаем новый учёт.
        if self.last_t is not None and t < self.last_t - 5.0:
            self.reset()

        self.last_t, self.h_now, self.state = t, h, state
        self.packets += 1
        self._recent.append((t, h))

        flying = state in IN_FLIGHT
        if flying and self._launch_fb is None:
            self._launch_fb = t

        # Апогей считаем только с момента старта, шум на площадке не в счёт.
        if (flying or state == "LANDED") and (self.max_alt is None or h > self.max_alt):
            self.max_alt, self.max_alt_t = h, t

        if recovery == "DEPLOYED" and self._deploy_fb is None and self._launch_fb is not None:
            self._deploy_fb = t
        if state == "LANDED" and self.t_land is None and self._launch_fb is not None:
            self.t_land = t

        # Скорость и ускорение по окну последней секунды.
        self._h_win.append((t, h))
        while self._h_win and t - self._h_win[0][0] > SPEED_WIN_S:
            self._h_win.popleft()
        v = _slope(self._h_win)
        self.v_now = v
        if v is None:
            return
        self._v_win.append((t, v))
        while self._v_win and t - self._v_win[0][0] > SPEED_WIN_S:
            self._v_win.popleft()
        a = _slope(self._v_win)

        if flying:
            self.v_up = v if self.v_up is None else max(self.v_up, v)
            self.v_down = v if self.v_down is None else min(self.v_down, v)
            if a is not None:
                ag = a / G
                self.a_max = ag if self.a_max is None else max(self.a_max, ag)
                self.a_min = ag if self.a_min is None else min(self.a_min, ag)

        d = self._deploy()
        if (state in ("DESCENT", "RECOVERY") and d is not None
                and t > d + DEPLOY_SETTLE_S and v < 0):
            self._desc_sum += -v
            self._desc_n += 1

    def add_event(self, t: float, name: str) -> None:
        """Событие с борта. Его время точнее, чем время пакета с новым состоянием."""
        if name == "LAUNCH_DETECTED":
            self.t_launch = t
        elif name == "RECOVERY_DEPLOY":
            self.t_deploy = t
            self.h_deploy = self._alt_at(t)
        elif name == "LANDED":
            self.t_land = t

    def _alt_at(self, t: float) -> Optional[float]:
        if not self._recent:
            return None
        return min(self._recent, key=lambda p: abs(p[0] - t))[1]

    # ------------------------------------------------------------

    @property
    def deploy_altitude(self) -> Optional[float]:
        if self.h_deploy is not None:
            return self.h_deploy
        d = self._deploy()
        return self._alt_at(d) if d is not None else None

    @property
    def time_to_apogee(self) -> Optional[float]:
        s = self._start()
        if s is None or self.max_alt_t is None:
            return None
        return max(self.max_alt_t - s, 0.0)

    @property
    def flight_time(self) -> Optional[float]:
        s = self._start()
        if s is None:
            return None
        end = self.t_land if self.t_land is not None else self.last_t
        return None if end is None else max(end - s, 0.0)

    @property
    def descent_time(self) -> Optional[float]:
        d = self._deploy()
        if d is None or self.t_land is None:
            return None
        return max(self.t_land - d, 0.0)

    @property
    def descent_speed_avg(self) -> Optional[float]:
        """Средняя скорость спуска на парашюте, м/с (положительная)."""
        return self._desc_sum / self._desc_n if self._desc_n else None

    @property
    def deploy_time_from_launch(self) -> Optional[float]:
        s, d = self._start(), self._deploy()
        return None if s is None or d is None else max(d - s, 0.0)

    # ------------------------------------------------------------

    def summary(self) -> List[Tuple[str, str, str]]:
        """(ключ, подпись, значение) для экрана и файла сводки. Нет данных: «—»."""
        def f(v: Optional[float], fmt: str) -> str:
            return "—" if v is None else format(v, fmt)

        return [
            ("max_alt_m", "Апогей, м", f(self.max_alt, ".1f")),
            ("time_to_apogee_s", "Время до апогея, с", f(self.time_to_apogee, ".1f")),
            ("v_up_ms", "Макс. скорость вверх, м/с", f(self.v_up, ".1f")),
            ("v_down_ms", "Макс. скорость вниз, м/с", f(self.v_down, ".1f")),
            ("descent_speed_avg_ms", "Средняя скорость спуска на парашюте, м/с",
             f(self.descent_speed_avg, ".1f")),
            ("a_max_g", "Макс. ускорение (оценка по барометру), g", f(self.a_max, ".2f")),
            ("a_min_g", "Макс. замедление (оценка по барометру), g", f(self.a_min, ".2f")),
            ("deploy_after_launch_s", "Раскрытие парашюта: время от старта, с",
             f(self.deploy_time_from_launch, ".1f")),
            ("deploy_alt_m", "Раскрытие парашюта: высота, м", f(self.deploy_altitude, ".1f")),
            ("descent_time_s", "Время снижения на парашюте, с", f(self.descent_time, ".1f")),
            ("flight_time_s", "Время полёта, с", f(self.flight_time, ".1f")),
            ("packets", "Принято пакетов за полёт", str(self.packets)),
        ]

    def write_csv(self, path: str) -> None:
        """Сводка в CSV: параметр, значение. utf-8 с BOM, как остальные журналы."""
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["key", "parameter", "value"])
            for row in self.summary():
                w.writerow(row)
