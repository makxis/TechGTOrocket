"""
Проверка разбора отладочной строки, журналов CSV и команд привода.

Запуск:  python3 -m unittest discover ground
"""

import csv
import os
import tempfile
import unittest

from telemetry import Session, crc8
from vro_link import (
    parse_debug_line, angle_command, angle_actual, CsvLog, radio_row,
    debug_row, sent_row, RADIO_COLUMNS, DEBUG_COLUMNS,
)

DEBUG = "15635 READY h=-0.06 max=0.00 |a|=1.01 p=99311 rec=ARMED z=1 vb=6.79 err=0x200 rdrop=0"


def _crc(body: str) -> str:
    return "%s*%02X" % (body, crc8(body.encode()))


class TestDebugParse(unittest.TestCase):
    def test_full_line(self):
        s = parse_debug_line(DEBUG)
        self.assertEqual((s.time_ms, s.state, s.pressure_pa), (15635, "READY", 99311))
        self.assertEqual((s.altitude_m, s.vbat_v, s.error_flags, s.rdrop), (-0.06, 6.79, 0x200, 0))
        self.assertTrue(s.zero_set)

    def test_line_without_vbat_and_rdrop(self):
        s = parse_debug_line("100 INIT h=0.00 max=0.00 |a|=1.00 p=101325 rec=SAFE z=0 err=0x0")
        self.assertIsNone(s.vbat_v)
        self.assertIsNone(s.rdrop)

    def test_other_text_is_not_a_sample(self):
        self.assertIsNone(parse_debug_line("привод -> DEPLOYED, угол 120"))
        self.assertIsNone(parse_debug_line(""))
        self.assertIsNone(parse_debug_line("15635 READY h=-0.06"))


class TestServoCommands(unittest.TestCase):
    def test_angles_round_to_step(self):
        self.assertEqual(angle_command(0), b"0")
        self.assertEqual(angle_command(120), b"6")
        self.assertEqual(angle_command(180), b"9")
        self.assertEqual(angle_command(29), b"1")     # ближе к 20
        self.assertEqual(angle_command(31), b"2")     # ближе к 40

    def test_angles_are_clamped(self):
        self.assertEqual(angle_command(-50), b"0")
        self.assertEqual(angle_command(999), b"9")

    def test_actual_angle(self):
        self.assertEqual(angle_actual(115), 120)


class TestCsv(unittest.TestCase):
    def _read(self, log):
        log.close()
        with open(log.path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    def test_radio_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            log = CsvLog(d, "radio", RADIO_COLUMNS)
            s = Session()
            pkt = _crc("1|100|READY|0.00|101325|SAFE|0")
            ev = _crc("#100|LAUNCH_DETECTED")
            for line in (pkt, pkt, ev, ev,
                         pkt.replace("100", "199"),      # испорчена
                         "OK+RP:+20dBm"):
                log.write(radio_row(s, line))
            log.write(sent_row(b"d"))
            rows = self._read(log)
        self.assertEqual([r["kind"] for r in rows],
                         ["packet", "duplicate", "event", "event_dup",
                          "bad", "raw", "command"])
        self.assertEqual(rows[0]["seq"], "1")
        self.assertEqual(rows[2]["state"], "LAUNCH_DETECTED")
        self.assertEqual(rows[4]["raw"], pkt.replace("100", "199"))

    def test_debug_rows(self):
        with tempfile.TemporaryDirectory() as d:
            log = CsvLog(d, "debug", DEBUG_COLUMNS)
            log.write(debug_row(DEBUG))
            log.write(debug_row("привод -> угол 120"))
            rows = self._read(log)
        self.assertEqual(rows[0]["kind"], "debug")
        self.assertEqual(rows[0]["vbat_v"], "6.79")
        self.assertEqual(rows[0]["zero_set"], "1")
        self.assertEqual(rows[1]["kind"], "text")
        self.assertEqual(rows[1]["raw"], "привод -> угол 120")

    def test_file_is_flushed_per_row(self):
        with tempfile.TemporaryDirectory() as d:
            log = CsvLog(d, "radio", RADIO_COLUMNS)
            log.write(radio_row(Session(), _crc("1|100|READY|0.00|101325|SAFE|0")))
            # читаем до close(): строка уже на диске
            with open(log.path, encoding="utf-8-sig") as fh:
                self.assertEqual(len(fh.read().splitlines()), 2)
            log.close()


if __name__ == "__main__":
    unittest.main()
