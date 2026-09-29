# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — напряжение батареи в вольтах и процентах (без окна).

Батарея «Крона» 9 В. Делитель на плате стоит за диодом SS14 (вход стабилизатора
L7805), поэтому показание на ~0,45 В ниже, чем на клеммах батареи (8,53 против
8,08 В, сверка мультиметром 29.09.2026). Для стабилизатора это нужное число.
Проценты считаются по нему. Процент считается линейно между двумя точками:
  100 %  напряжение заряженной батареи, которое оператор фиксирует кнопкой
         (по умолчанию 8,04 В: полностью заряженный аккумулятор, замер 30.09.2026);
    0 %  по умолчанию 7,0 В, оператор фиксирует настоящее значение кнопкой, когда
         аккумулятор разрядится (минимум пока неизвестен). Ориентир 7,0 В: ниже стабилизатор L7805 с диодом уже не держит 5 В, привод и
         радио работают ненадёжно. Это не «батарея мертва», а «летать нельзя».

Кривая разряда щелочной батареи нелинейная, поэтому процент это ориентир для
глаза, а главное число здесь вольты.
"""

from typing import Optional

FULL_DEFAULT_V = 8.04    # аккумулятор пользователя, заряженный полностью: 8,04 В за диодом
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
