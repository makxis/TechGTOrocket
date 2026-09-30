# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
Проверка вольт и процентов батареи.

Запуск:  python3 -m unittest discover ground
"""

import unittest

from telemetry import parse_line, crc8, ParseError
from vro_battery import (percent, valid_full, valid_empty, fmt, voltage_at_percent,
                         EMPTY_V, FULL_DEFAULT_V)


class TestBattery(unittest.TestCase):
    def test_full_is_100_and_empty_is_0(self):
        self.assertEqual(percent(FULL_DEFAULT_V), 100)
        self.assertEqual(percent(9.7), 100)                 # выше опоры не больше 100
        self.assertEqual(percent(EMPTY_V), 0)
        self.assertEqual(percent(5.9), 0)                   # ниже нуля не уходит в минус

    def test_defaults_match_measured_curve(self):
        self.assertAlmostEqual(FULL_DEFAULT_V, 8.07)
        self.assertAlmostEqual(EMPTY_V, 6.43)

    def test_curve_points(self):
        # значения посчитаны по многочлену пользователя (замер 30.09.2026)
        for v, want in ((7.9, 95), (7.8, 92), (7.5, 81), (7.2, 67), (7.0, 53),
                        (6.9, 45), (6.8, 34), (6.7, 24), (6.6, 14), (6.5, 5)):
            self.assertAlmostEqual(percent(v), want, delta=1, msg=f"{v} В")

    def test_monotonic(self):
        prev = 101
        for k in range(0, 200):
            v = 8.1 - k * 0.01
            p = percent(v)
            self.assertLessEqual(p, prev)
            prev = p

    def test_old_alarm_threshold_was_far_too_early(self):
        self.assertGreater(percent(7.8), 90)                # 7,8 В это всего 8 % разряда

    def test_voltage_at_percent_is_inverse(self):
        for pct in (10, 20, 50, 80):
            self.assertAlmostEqual(percent(voltage_at_percent(pct)), pct, delta=1)
        self.assertAlmostEqual(voltage_at_percent(20), 6.66, delta=0.02)

    def test_custom_references_rescale_the_same_shape(self):
        # зафиксировали 8,5 В как 100 % и 6,0 В как 0 %: форма кривой та же
        mid_default = percent(7.0)                          # опоры по умолчанию
        v_same_shape = 6.0 + (7.0 - 6.43) / (8.07 - 6.43) * (8.5 - 6.0)
        self.assertAlmostEqual(percent(v_same_shape, 8.5, 6.0), mid_default, delta=1)

    def test_unknown(self):
        self.assertIsNone(percent(None))
        self.assertIsNone(percent(0.0))
        self.assertEqual(fmt(None), "—")

    def test_bad_reference(self):
        self.assertIsNone(percent(8.0, 6.0, 7.0))

    def test_valid_full_rejects_usb_and_garbage(self):
        self.assertFalse(valid_full(4.22))                  # только USB
        self.assertFalse(valid_full(6.8))                   # разряженная
        self.assertFalse(valid_full(None))
        self.assertTrue(valid_full(8.07))

    def test_valid_empty(self):
        self.assertTrue(valid_empty(6.43, 8.07))
        self.assertFalse(valid_empty(4.2, 8.07))            # только USB
        self.assertFalse(valid_empty(7.95, 8.07))           # почти полная
        self.assertFalse(valid_empty(None, 8.07))

    def test_fmt(self):
        self.assertEqual(fmt(7.91), "7.91 В · 96 %")


class TestBatteryField(unittest.TestCase):
    def test_eighth_field_is_voltage(self):
        p = parse_line("5|1000|READY|0.10|99000|ARMED|0|911")
        self.assertAlmostEqual(p.vbat_v, 9.11)

    def test_zero_means_not_measured(self):
        self.assertIsNone(parse_line("5|1000|READY|0.10|99000|ARMED|0|0").vbat_v)

    def test_seven_fields_still_valid(self):
        self.assertIsNone(parse_line("5|1000|READY|0.10|99000|ARMED|0").vbat_v)

    def test_eight_fields_with_crc(self):
        body = "5|1000|READY|0.10|99000|ARMED|512|887"
        line = "%s*%02X" % (body, crc8(body.encode()))
        self.assertAlmostEqual(parse_line(line).vbat_v, 8.87)

    def test_nine_fields_rejected(self):
        with self.assertRaises(ParseError):
            parse_line("5|1000|READY|0.10|99000|ARMED|0|911|1")


if __name__ == "__main__":
    unittest.main()
