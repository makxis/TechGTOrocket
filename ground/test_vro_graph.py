# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
Проверка расчётов графика: история, окно, прореживание, шкалы, плавность.

Запуск:  python3 -m unittest discover ground
"""

import unittest

from vro_graph import (
    History, decimate, nice_step, y_range, ease, time_step, fmt_ago,
    HISTORY_CAP, MIN_SPAN_M,
)


class TestHistory(unittest.TestCase):
    def test_keeps_more_than_old_limit(self):
        h = History()
        for i in range(5000):                 # старый предел был 900 точек
            h.add(i * 0.2, float(i))
        self.assertEqual(len(h.t), 5000)

    def test_cap_drops_oldest_only(self):
        h = History(cap=100)
        for i in range(101):
            h.add(float(i), float(i))
        self.assertEqual(len(h.t), 91)
        self.assertEqual(h.t[-1], 100.0)
        self.assertEqual(h.t[0], 10.0)

    def test_time_keeps_growing_after_board_restart(self):
        h = History()
        for t in (10.0, 10.2, 10.4):
            h.add(t, 0.0)
        h.add(0.1, 0.0)                       # борт перезапустился
        h.add(0.3, 0.0)
        self.assertTrue(all(b > a for a, b in zip(h.t, h.t[1:])))

    def test_clear(self):
        h = History()
        h.add(1.0, 1.0)
        h.clear()
        self.assertEqual((h.t, h.v, h.last_x), ([], [], None))

    def test_visible_window_includes_one_point_before(self):
        h = History()
        for i in range(10):
            h.add(float(i), float(i))
        ts, _ = h.visible(5.0)
        self.assertEqual(ts, [4.0, 5.0, 6.0, 7.0, 8.0, 9.0])


class TestDecimate(unittest.TestCase):
    def test_small_input_untouched(self):
        ts, vs = decimate([1, 2, 3], [1, 2, 3], 10)
        self.assertEqual((ts, vs), ([1, 2, 3], [1, 2, 3]))

    def test_peaks_survive(self):
        n = 10000
        ts = [float(i) for i in range(n)]
        vs = [0.0] * n
        vs[5555] = 100.0
        vs[7777] = -50.0
        dt, dv = decimate(ts, vs, 200)
        self.assertLessEqual(len(dt), 400)
        self.assertIn(100.0, dv)
        self.assertIn(-50.0, dv)
        self.assertEqual(dt, sorted(dt))


class TestScales(unittest.TestCase):
    def test_nice_step(self):
        self.assertEqual(nice_step(10, 5), 2)
        self.assertEqual(nice_step(100, 5), 20)
        self.assertEqual(nice_step(0.7, 5), 0.2)
        self.assertEqual(nice_step(0, 5), 1.0)

    def test_y_range_has_minimum_span(self):
        lo, hi = y_range([0.0, 0.1, -0.1])
        self.assertGreaterEqual(hi - lo, MIN_SPAN_M)

    def test_y_range_covers_data(self):
        lo, hi = y_range([0.0, 50.0])
        self.assertLess(lo, 0.0)
        self.assertGreater(hi, 50.0)

    def test_empty(self):
        self.assertEqual(y_range([]), (0.0, MIN_SPAN_M))

    def test_ease_converges_without_overshoot(self):
        x = 0.0
        for _ in range(60):
            x = ease(x, 10.0)
            self.assertLessEqual(x, 10.0)
        self.assertEqual(x, 10.0)

    def test_time_step_and_labels(self):
        self.assertEqual(time_step(300.0), 60.0)
        self.assertEqual(fmt_ago(0), "сейчас")
        self.assertEqual(fmt_ago(45), "−45 с")
        self.assertEqual(fmt_ago(125), "−2:05")


if __name__ == "__main__":
    unittest.main()
