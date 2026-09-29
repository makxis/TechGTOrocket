# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — расчёты для графика высоты (без окна).

Вынесено из vro_station.py, чтобы проверяться тестами: история без потерь
и без скачков времени, окно просмотра, прореживание под ширину графика,
«красивые» деления осей и плавная подстройка масштаба.
"""

import bisect
import math
import time
from typing import List, Optional, Tuple

# Окна просмотра: подпись и длительность в секундах (None = вся история).
WINDOWS: List[Tuple[str, Optional[float]]] = [
    ("1 мин", 60.0), ("5 мин", 300.0), ("10 мин", 600.0),
    ("30 мин", 1800.0), ("Всё", None),
]
DEFAULT_WINDOW_S = 300.0

# Сколько точек хранить. 5 пакетов/с * 2 часа = 36 000: копейки по памяти.
HISTORY_CAP = 40000

# Минимальный размах шкалы высоты, м. Иначе шум датчика в покое (доли
# метра) растягивается на весь график и он «дрожит».
MIN_SPAN_M = 2.0


class History:
    """
    Точки (время, значение) с непрерывной осью времени.

    Часы борта при перезапуске идут с нуля. Чтобы график не «складывался»,
    при скачке назад ось сдвигается, и время продолжает расти.
    """

    def __init__(self, cap: int = HISTORY_CAP) -> None:
        self.cap = cap
        self.clear()

    def clear(self) -> None:
        self.t: List[float] = []
        self.v: List[float] = []
        self._offset = 0.0
        self._last_raw: Optional[float] = None
        self.last_wall = 0.0

    def add(self, t_raw: float, value: float, wall: Optional[float] = None) -> None:
        if self._last_raw is not None and t_raw < self._last_raw - 1.0:
            # Борт перезапустился: продолжаем ось с того места, где стояли.
            self._offset += (self.t[-1] - (t_raw + self._offset)) + 0.2 if self.t else 0.0
        self._last_raw = t_raw
        self.t.append(t_raw + self._offset)
        self.v.append(value)
        self.last_wall = time.time() if wall is None else wall
        if len(self.t) > self.cap:
            drop = self.cap // 10
            del self.t[:drop]
            del self.v[:drop]

    @property
    def last_x(self) -> Optional[float]:
        return self.t[-1] if self.t else None

    def visible(self, left: float) -> Tuple[List[float], List[float]]:
        """Точки правее left (плюс одна левее, чтобы линия входила в окно)."""
        i = bisect.bisect_left(self.t, left)
        if i > 0:
            i -= 1
        return self.t[i:], self.v[i:]


def decimate(ts: List[float], vs: List[float], buckets: int) -> Tuple[List[float], List[float]]:
    """
    Прореживание под ширину графика: в каждой корзине оставляем минимум и
    максимум (порядок сохраняется), чтобы пики не терялись.
    """
    n = len(ts)
    if buckets <= 0 or n <= buckets * 2:
        return ts, vs
    out_t: List[float] = []
    out_v: List[float] = []
    for b in range(buckets):
        lo = b * n // buckets
        hi = (b + 1) * n // buckets
        if hi <= lo:
            continue
        seg = vs[lo:hi]
        i_min = lo + seg.index(min(seg))
        i_max = lo + seg.index(max(seg))
        for i in sorted({i_min, i_max}):
            out_t.append(ts[i])
            out_v.append(vs[i])
    return out_t, out_v


def nice_step(span: float, target_ticks: int = 5) -> float:
    """Шаг делений 1, 2 или 5 умножить на степень десяти."""
    if span <= 0:
        return 1.0
    raw = span / max(target_ticks, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def y_range(values: List[float], min_span: float = MIN_SPAN_M,
            pad: float = 0.08) -> Tuple[float, float]:
    """Границы шкалы по видимым значениям, с запасом и минимальным размахом."""
    if not values:
        return 0.0, min_span
    lo, hi = min(values), max(values)
    span = max(hi - lo, min_span)
    mid = (hi + lo) / 2.0
    lo, hi = mid - span / 2.0, mid + span / 2.0
    return lo - span * pad, hi + span * pad


def ease(current: float, target: float, k: float = 0.2) -> float:
    """Шаг плавного приближения к цели. Возле цели прилипает к ней."""
    d = target - current
    if abs(d) < 0.005:
        return target
    return current + d * k


def time_step(window_s: float) -> float:
    """Шаг вертикальных линий времени, с."""
    table = {60.0: 10.0, 300.0: 60.0, 600.0: 120.0, 1800.0: 300.0}
    return table.get(window_s) or nice_step(window_s, 6)


def fmt_ago(seconds: float) -> str:
    """Подпись оси времени: «сейчас», «−45 с», «−2:00»."""
    s = int(round(seconds))
    if s <= 0:
        return "сейчас"
    if s < 60:
        return f"−{s} с"
    return f"−{s // 60}:{s % 60:02d}"
