#!/usr/bin/env python3
"""
Отчёт по разряду батареи из журналов станции (radio_*.csv, debug_*.csv) или
своего журнала с колонками time,vbat_v.

    python3 tools/battery_report.py ground/logs/radio_*.csv
    python3 tools/battery_report.py журнал.csv --full 9.3

Показывает: сколько прошло от максимума до минимума, скорость разряда (В/час),
прогноз до порога тревоги (7,8 В) и до нуля процентов (7,0 В), график по часам.
Напряжение за диодом (вход стабилизатора), в USB-режиме (< 5,5 В) точки отбрасываются.
Под нагрузкой (привод, передатчик) напряжение проседает, поэтому берётся медиана за минуту.
"""

import argparse
import csv
import os
import statistics
import sys
from datetime import datetime
from typing import List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ground"))
from vro_battery import percent, FULL_DEFAULT_V, EMPTY_V  # noqa: E402

USB_ONLY_V = 5.5
LOW_ALARM_V = 7.8


def read_points(paths: List[str]) -> List[Tuple[datetime, float]]:
    pts = []
    for path in paths:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                t = row.get("pc_time") or row.get("time")
                v = row.get("vbat_v")
                if not t or not v:
                    continue
                try:
                    volts = float(v)
                    stamp = datetime.fromisoformat(t)
                except ValueError:
                    continue
                if volts >= USB_ONLY_V:
                    pts.append((stamp, volts))
    pts.sort()
    return pts


def per_minute(pts: List[Tuple[datetime, float]]) -> List[Tuple[float, float]]:
    """Медиана за каждую минуту: (минуты от начала, вольты)."""
    if not pts:
        return []
    t0 = pts[0][0]
    buckets = {}
    for t, v in pts:
        buckets.setdefault(int((t - t0).total_seconds() // 60), []).append(v)
    return [(m, statistics.median(vs)) for m, vs in sorted(buckets.items())]


def slope_v_per_hour(mins: List[Tuple[float, float]]) -> float:
    """Наклон прямой по методу наименьших квадратов, В в час (отрицательный при разряде)."""
    n = len(mins)
    if n < 2:
        return 0.0
    mt = sum(m for m, _ in mins) / n
    mv = sum(v for _, v in mins) / n
    den = sum((m - mt) ** 2 for m, _ in mins)
    if den == 0:
        return 0.0
    return sum((m - mt) * (v - mv) for m, v in mins) / den * 60.0


def fmt_hours(h: float) -> str:
    return f"{int(h)} ч {int(round((h - int(h)) * 60)):02d} мин"


def report(pts, full_v: float) -> str:
    if len(pts) < 2:
        return "Нет точек напряжения (нужны строки с vbat_v выше 5,5 В)."
    mins = per_minute(pts)
    t0, t1 = pts[0][0], pts[-1][0]
    span_h = (t1 - t0).total_seconds() / 3600.0
    vmax_m, vmax = max(mins, key=lambda x: x[1])
    vmin_m, vmin = min(mins, key=lambda x: x[1])
    last = mins[-1][1]
    rate = slope_v_per_hour(mins)
    lines = [
        f"Запись: {t0:%Y-%m-%d %H:%M} … {t1:%H:%M}, {fmt_hours(span_h)}, точек {len(pts)}",
        f"Максимум: {vmax:.2f} В ({percent(vmax, full_v)} %) на {fmt_hours(vmax_m / 60)} от начала",
        f"Минимум:  {vmin:.2f} В ({percent(vmin, full_v)} %) на {fmt_hours(vmin_m / 60)} от начала",
        f"От максимума до минимума: {fmt_hours(abs(vmin_m - vmax_m) / 60)}, "
        f"просело на {vmax - vmin:.2f} В",
        f"Сейчас: {last:.2f} В ({percent(last, full_v)} %)",
        f"Скорость: {rate:+.3f} В/час",
    ]
    if rate < -1e-4:
        for name, thr in (("до тревоги 7,8 В", LOW_ALARM_V), ("до 0 % (7,0 В)", EMPTY_V)):
            left = (last - thr) / -rate
            lines.append(f"Прогноз {name}: " + (fmt_hours(left) if left > 0 else "уже ниже"))
    else:
        lines.append("Разряд по этим данным не виден (мало времени или батарею меняли).")

    # график по часам
    hourly = {}
    for m, v in mins:
        hourly.setdefault(int(m // 60), []).append(v)
    lo, hi = min(vmin, EMPTY_V), max(vmax, vmin + 0.1)
    lines.append("")
    for h, vs in sorted(hourly.items()):
        v = statistics.median(vs)
        bar = "█" * max(1, int(round((v - lo) / (hi - lo) * 40)))
        lines.append(f"{h:>3} ч {v:5.2f} В {bar}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Отчёт по разряду батареи")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--full", type=float, default=FULL_DEFAULT_V,
                    help="напряжение, принятое за 100 %% (по умолчанию %.1f)" % FULL_DEFAULT_V)
    a = ap.parse_args()
    print(report(read_points(a.files), a.full))
    return 0


if __name__ == "__main__":
    sys.exit(main())
