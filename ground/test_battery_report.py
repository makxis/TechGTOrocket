"""
Проверка отчёта по разряду батареи (tools/battery_report.py).

Запуск:  python3 -m unittest discover ground
"""

import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import battery_report as br  # noqa: E402


def make_csv(path, hours=5, start_v=9.0, drop_per_hour=0.2, every_s=30, col="pc_time"):
    t0 = datetime(2026, 9, 30, 10, 0, 0)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[col, "kind", "vbat_v"])
        w.writeheader()
        n = int(hours * 3600 / every_s)
        for i in range(n):
            t = t0 + timedelta(seconds=i * every_s)
            v = start_v - drop_per_hour * (i * every_s / 3600.0)
            w.writerow({col: t.isoformat(timespec="milliseconds"), "kind": "packet",
                        "vbat_v": f"{v:.2f}"})


class TestBatteryReport(unittest.TestCase):
    def test_rate_and_forecast(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "radio_x.csv")
            make_csv(p, hours=5, start_v=9.0, drop_per_hour=0.2)
            pts = br.read_points([p])
            mins = br.per_minute(pts)
            self.assertAlmostEqual(br.slope_v_per_hour(mins), -0.2, delta=0.01)
            text = br.report(pts, 9.4)
        self.assertIn("От максимума до минимума: 4 ч 5", text)     # ~4 ч 59 мин
        self.assertIn("Скорость: -0.200", text)
        self.assertIn("Прогноз до тревоги", text)

    def test_usb_only_points_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "debug_x.csv")
            make_csv(p, hours=1, start_v=4.2, drop_per_hour=0.0)
            self.assertEqual(br.read_points([p]), [])
            self.assertIn("Нет точек", br.report([], 9.4))

    def test_own_log_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "vbat_log.csv")
            make_csv(p, hours=2, col="time")
            self.assertGreater(len(br.read_points([p])), 100)

    def test_flat_voltage_says_no_discharge(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "flat.csv")
            make_csv(p, hours=2, start_v=9.0, drop_per_hour=0.0)
            self.assertIn("Разряд по этим данным не виден", br.report(br.read_points([p]), 9.4))


if __name__ == "__main__":
    unittest.main()
