# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
Проверка статистики полёта на синтетическом профиле с известными ответами.

Запуск:  python3 -m unittest discover ground
"""

import csv
import os
import tempfile
import unittest

from vro_stats import FlightStats, G


def synthetic_flight(stats: FlightStats, dt: float = 0.2):
    """
    Готовность 5 с, потом:
      разгон 2 с с постоянным ускорением 20 м/с2 (h = 10 t^2, v = 20 t),
      подъём по инерции до апогея с замедлением g,
      падение, раскрытие через 1 с после апогея, спуск 5 м/с до земли.
    Возвращает словарь ожидаемых значений.
    """
    t0 = 5.0
    a = 20.0
    t_boost = 2.0
    h_boost = 0.5 * a * t_boost ** 2            # 40 м
    v_boost = a * t_boost                        # 40 м/с
    t_coast = v_boost / G                         # время до апогея после разгона
    h_apogee = h_boost + v_boost * t_coast - 0.5 * G * t_coast ** 2
    t_apogee = t0 + t_boost + t_coast
    t_deploy = t_apogee + 1.0
    # до раскрытия свободное падение, затем 5 м/с
    h_deploy = h_apogee - 0.5 * G * 1.0
    v_deploy = -G * 1.0

    t = 0.0
    landed_t = None
    while t < 80.0:
        if t < t0:
            h, state, rec = 0.0, "READY", "ARMED"
        elif t < t0 + t_boost:
            tt = t - t0
            h, state, rec = 0.5 * a * tt ** 2, "BOOST", "ARMED"
        elif t < t_apogee:
            tt = t - t0 - t_boost
            h, state, rec = h_boost + v_boost * tt - 0.5 * G * tt ** 2, "COAST", "ARMED"
        elif t < t_deploy:
            tt = t - t_apogee
            h, state, rec = h_apogee - 0.5 * G * tt ** 2, "DESCENT", "ARMED"
        else:
            h = h_deploy - 5.0 * (t - t_deploy)
            state, rec = "DESCENT", "DEPLOYED"
            if h <= 0.0:
                h, state = 0.0, "LANDED"
                if landed_t is None:
                    landed_t = t
        stats.add_packet(t, h, state, rec)
        t += dt

    stats.add_event(t0, "LAUNCH_DETECTED")
    stats.add_event(t_deploy, "RECOVERY_DEPLOY")
    stats.add_event(landed_t, "LANDED")
    return {"h_apogee": h_apogee, "t_apogee": t_apogee, "t0": t0,
            "t_deploy": t_deploy, "h_deploy": h_deploy, "landed": landed_t,
            "v_boost": v_boost, "a": a}


class TestFlightStats(unittest.TestCase):
    def setUp(self):
        self.s = FlightStats()
        self.exp = synthetic_flight(self.s)

    def test_apogee_altitude_and_time(self):
        self.assertAlmostEqual(self.s.max_alt, self.exp["h_apogee"], delta=1.0)
        self.assertAlmostEqual(self.s.time_to_apogee,
                               self.exp["t_apogee"] - self.exp["t0"], delta=0.4)

    def test_max_speed_up(self):
        # скорость по окну секунды слегка сглаживает пик
        self.assertAlmostEqual(self.s.v_up, self.exp["v_boost"], delta=6.0)

    def test_max_acceleration_estimate(self):
        expected_g = self.exp["a"] / G
        self.assertAlmostEqual(self.s.a_max, expected_g, delta=expected_g * 0.3)
        self.assertLess(self.s.a_min, 0.0)      # замедление после разгона

    def test_deploy(self):
        self.assertAlmostEqual(self.s.deploy_altitude, self.exp["h_deploy"], delta=1.0)
        self.assertAlmostEqual(self.s.deploy_time_from_launch,
                               self.exp["t_deploy"] - self.exp["t0"], delta=0.01)

    def test_descent_speed_on_parachute(self):
        self.assertAlmostEqual(self.s.descent_speed_avg, 5.0, delta=0.5)

    def test_times(self):
        self.assertAlmostEqual(self.s.flight_time,
                               self.exp["landed"] - self.exp["t0"], delta=0.01)
        self.assertAlmostEqual(self.s.descent_time,
                               self.exp["landed"] - self.exp["t_deploy"], delta=0.01)

    def test_summary_has_all_rows_and_no_dashes_after_flight(self):
        rows = self.s.summary()
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(v != "—" for _, _, v in rows))

    def test_csv_written(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "summary.csv")
            self.s.write_csv(path)
            with open(path, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["key"], "max_alt_m")
        self.assertEqual(len(rows), 12)


class TestEdgeCases(unittest.TestCase):
    def test_nothing_before_flight(self):
        s = FlightStats()
        for i in range(50):
            s.add_packet(i * 0.2, 0.1 * (i % 2), "READY", "ARMED")
        self.assertFalse(s.launched)
        self.assertIsNone(s.max_alt)          # шум на площадке не апогей
        self.assertTrue(all(v == "—" for k, _, v in s.summary() if k != "packets"))

    def test_board_restart_resets(self):
        s = FlightStats()
        synthetic_flight(s)
        s.add_packet(100.0, 0.0, "READY", "ARMED")
        s.add_packet(0.2, 0.0, "READY", "ARMED")     # время пошло с нуля
        self.assertFalse(s.launched)
        self.assertEqual(s.packets, 1)

    def test_fallback_without_events(self):
        s = FlightStats()
        synthetic_flight(s)
        s2 = FlightStats()
        # те же пакеты, но события с борта потерялись
        t = 0.0
        for i in range(400):
            h = 0.0 if i < 25 else 50.0 * (1 - abs(i - 100) / 75.0)
            state = "READY" if i < 25 else ("COAST" if i < 100 else "DESCENT")
            rec = "DEPLOYED" if i > 110 else "ARMED"
            s2.add_packet(i * 0.2, max(h, 0.0), state, rec)
        self.assertTrue(s2.launched)
        self.assertIsNotNone(s2.deploy_time_from_launch)


if __name__ == "__main__":
    unittest.main()
