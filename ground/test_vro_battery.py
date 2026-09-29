"""
Проверка вольт и процентов батареи.

Запуск:  python3 -m unittest discover ground
"""

import unittest

from telemetry import parse_line, crc8, ParseError
from vro_battery import percent, valid_full, valid_empty, fmt, EMPTY_V, FULL_DEFAULT_V


class TestBattery(unittest.TestCase):
    def test_full_is_100(self):
        self.assertEqual(percent(9.4, 9.4), 100)
        self.assertEqual(percent(9.7, 9.4), 100)          # выше опорной не больше 100

    def test_empty_is_0(self):
        self.assertEqual(percent(EMPTY_V), 0)
        self.assertEqual(percent(6.0), 0)                  # ниже нуля не уходит в минус

    def test_middle(self):
        self.assertEqual(percent(8.2, 9.4, 7.0), 50)

    def test_fixed_reference_changes_percent(self):
        # зафиксировали 9,1 В как 100 %: сейчас батарея показывает 100
        self.assertEqual(percent(9.1, 9.1), 100)
        self.assertLess(percent(9.1, 9.4), 100)

    def test_unknown(self):
        self.assertIsNone(percent(None))
        self.assertIsNone(percent(0.0))
        self.assertEqual(fmt(None), "—")

    def test_bad_reference(self):
        self.assertIsNone(percent(8.0, 6.0, 7.0))

    def test_valid_full_rejects_usb_and_garbage(self):
        self.assertFalse(valid_full(4.22))                 # только USB
        self.assertFalse(valid_full(6.8))                  # разряженная
        self.assertFalse(valid_full(None))
        self.assertTrue(valid_full(9.3))

    def test_user_accumulator_default(self):
        # заряженный аккумулятор пользователя показывает 8,04 В: это 100 %
        self.assertEqual(percent(8.04), 100)
        self.assertEqual(FULL_DEFAULT_V, 8.04)

    def test_valid_empty(self):
        self.assertTrue(valid_empty(6.6, 8.04))
        self.assertFalse(valid_empty(4.2, 8.04))           # только USB
        self.assertFalse(valid_empty(7.95, 8.04))          # почти полная
        self.assertFalse(valid_empty(None, 8.04))

    def test_empty_reference_shifts_percent(self):
        self.assertEqual(percent(7.5, 8.04, 7.0), 48)
        self.assertEqual(percent(7.5, 8.04, 6.5), 65)      # ниже минимум, тот же вольтаж выше %

    def test_fmt(self):
        self.assertEqual(fmt(9.31, 9.4), "9.31 В · 96 %")


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
