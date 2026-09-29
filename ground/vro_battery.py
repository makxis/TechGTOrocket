"""
ВРО-1 / RocketBoard — напряжение батареи в вольтах и процентах (без окна).

Батарея «Крона» 9 В. Процент считается линейно между двумя точками:
  100 %  напряжение свежей батареи, которое оператор фиксирует кнопкой
         (по умолчанию 9,4 В);
    0 %  7,0 В: ниже стабилизатор L7805 с диодом уже не держит 5 В, привод и
         радио работают ненадёжно. Это не «батарея мертва», а «летать нельзя».

Кривая разряда щелочной батареи нелинейная, поэтому процент это ориентир для
глаза, а главное число здесь вольты.
"""

from typing import Optional

FULL_DEFAULT_V = 9.4
EMPTY_V = 7.0
FULL_MIN_V = 7.6       # разумные границы для «зафиксировать 100 %»
FULL_MAX_V = 10.5


def percent(volts: Optional[float], full_v: float = FULL_DEFAULT_V,
            empty_v: float = EMPTY_V) -> Optional[int]:
    """Процент заряда 0..100 или None, если напряжение неизвестно."""
    if volts is None or volts <= 0:
        return None
    if full_v <= empty_v:
        return None
    frac = (volts - empty_v) / (full_v - empty_v)
    return int(round(max(0.0, min(1.0, frac)) * 100))


def valid_full(volts: Optional[float]) -> bool:
    """Можно ли принять это напряжение за «100 %» (не USB и не разряженная)."""
    return volts is not None and FULL_MIN_V <= volts <= FULL_MAX_V


def fmt(volts: Optional[float], full_v: float = FULL_DEFAULT_V) -> str:
    """«9,31 В · 98 %» либо «—»."""
    p = percent(volts, full_v)
    if volts is None or volts <= 0 or p is None:
        return "—"
    return f"{volts:.2f} В · {p} %"
