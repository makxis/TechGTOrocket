# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — напряжение батареи в вольтах и процентах (без окна).

Делитель на плате стоит за диодом SS14 (вход стабилизатора L7805), поэтому показание
на ~0,45 В ниже, чем на клеммах батареи (8,53 против 8,08 В, сверка мультиметром
29.09.2026). Для стабилизатора это нужное число. Проценты считаются по нему.

Проценты не линейные, а по **реальной кривой разряда** аккумулятора пользователя
(замер 30.09.2026, точка лога каждые 10 с, ошибка аппроксимации 0,28 %):

    U(x) = -6.137e-11 x^3 + 4.066e-7 x^2 - 1.1923e-3 x + 8.0666,   U(0) = 8,07 В,
    U = 6,43 В при x = 3144 (минимум, считается за 0 %).

Процент это доля оставшегося времени разряда. Сырые данные (3316 точек по 10 с,
docs/data/battery_discharge_20260930.tsv): быстрое падение в начале (8,07 → 7,56 В за
первые 16 % времени, −0,38 В/ч), длинное плато (−0,07 В/ч), затем снова −0,17 В/ч до
6,43 В (около 8,5 ч). Ниже 6,43 В данных нет: дальше напряжение растёт (нагрузка
пропала, плата, видимо, выключилась), а многочлен за x = 3144 уходит в минус и
использовать его там нельзя. Опоры «100 %» и «0 %» можно поменять кнопками станции
(форма кривой при этом масштабируется по напряжению).

Оговорка: кривая снята при одной нагрузке (плата и радио); под приводом напряжение
проседает ниже, так что «0 %» это предел работы платы, а не привода.
"""

import bisect
from typing import Optional

FULL_DEFAULT_V = 8.07    # начало кривой разряда: заряженный аккумулятор пользователя, за диодом
EMPTY_V = 6.43         # минимум кривой: напряжение, при котором аккумулятор считается разряженным
FULL_MIN_V = 7.6       # разумные границы для «зафиксировать 100 %»
FULL_MAX_V = 10.5


# --- кривая разряда -------------------------------------------------------

_A3, _A2, _A1, _A0 = (-0.00000000006137171214, 0.00000040664077509737,
                      -0.00119231285183118985, 8.06660916964889906922)
_X_END = 3144.50099368              # где кривая доходит до 6,43 В


def _model_v(x: float) -> float:
    return ((_A3 * x + _A2) * x + _A1) * x + _A0


def _build_table():
    """(u, g): u доля напряжения между 0 % и 100 %, g доля оставшегося времени."""
    v_full, v_empty = _model_v(0.0), _model_v(_X_END)
    us, gs = [], []
    n = 800
    for k in range(n, -1, -1):                      # от конца кривой к началу: u растёт
        x = _X_END * k / n
        us.append((_model_v(x) - v_empty) / (v_full - v_empty))
        gs.append(1.0 - x / _X_END)
    return us, gs


_U, _G = _build_table()


def _interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def percent(volts: Optional[float], full_v: float = FULL_DEFAULT_V,
            empty_v: float = EMPTY_V) -> Optional[int]:
    """Процент заряда 0..100 по кривой разряда или None, если напряжение неизвестно."""
    if volts is None or volts <= 0 or full_v <= empty_v:
        return None
    u = (volts - empty_v) / (full_v - empty_v)
    if u >= 1.0:
        return 100
    if u <= 0.0:
        return 0
    return int(round(_interp(_U, _G, u) * 100))


def voltage_at_percent(pct: float, full_v: float = FULL_DEFAULT_V,
                       empty_v: float = EMPTY_V) -> float:
    """Напряжение, при котором остаётся pct процентов (обратная функция)."""
    g = max(0.0, min(1.0, pct / 100.0))
    return empty_v + _interp(_G, _U, g) * (full_v - empty_v)


def valid_empty(volts: Optional[float], full_v: float) -> bool:
    """Можно ли принять это напряжение за «0 %»: выше USB-уровня и заметно ниже «100 %»."""
    return volts is not None and 5.5 <= volts <= full_v - 0.2


def valid_full(volts: Optional[float]) -> bool:
    """Можно ли принять это напряжение за «100 %» (не USB и не разряженная)."""
    return volts is not None and FULL_MIN_V <= volts <= FULL_MAX_V


def fmt(volts: Optional[float], full_v: float = FULL_DEFAULT_V,
        empty_v: float = EMPTY_V) -> str:
    """«8,04 В · 100 %» либо «—»."""
    p = percent(volts, full_v, empty_v)
    if volts is None or volts <= 0 or p is None:
        return "—"
    return f"{volts:.2f} В · {p} %"
