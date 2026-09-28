"""
Проверка разбора телеметрии и учёта пакетов.

Запуск:  python3 -m unittest discover ground

Железо не требуется. Именно ради этого разбор и учёт вынесены в
отдельный модуль без интерфейса и без работы с портом.
"""

import unittest

from telemetry import (
    Packet, Event, ParseError,
    parse_line, seq_gap, describe_errors, Session,
    SEQ_MODULO, RESTART_GAP,
)


class TestParse(unittest.TestCase):

    def test_packet_from_radio(self):
        pkt = parse_line("642|12840|COAST|87.42|99584|ARMED|0")
        self.assertIsInstance(pkt, Packet)
        self.assertEqual(pkt.seq, 642)
        self.assertEqual(pkt.time_ms, 12840)
        self.assertEqual(pkt.state, "COAST")
        self.assertAlmostEqual(pkt.altitude_m, 87.42)
        self.assertEqual(pkt.pressure_pa, 99584)
        self.assertEqual(pkt.recovery, "ARMED")
        self.assertEqual(pkt.error_flags, 0)
        self.assertFalse(pkt.is_critical)

    def test_negative_altitude(self):
        """Высота бывает отрицательной: барометр шумит около нуля."""
        pkt = parse_line("1|100|READY|-0.13|98450|ARMED|0")
        self.assertAlmostEqual(pkt.altitude_m, -0.13)

    def test_event(self):
        ev = parse_line("#12840|APOGEE_CONFIRMED")
        self.assertIsInstance(ev, Event)
        self.assertEqual(ev.time_ms, 12840)
        self.assertEqual(ev.name, "APOGEE_CONFIRMED")

    def test_event_with_comma(self):
        """Журнал на карте разделяет запятой, а не чертой."""
        ev = parse_line("#12840,LANDED")
        self.assertEqual(ev.name, "LANDED")

    def test_csv_header_ignored(self):
        self.assertIsNone(parse_line(
            "time_ms,flight_time_ms,packet_id,state,pressure_pa,temp_c,"
            "altitude_m,max_altitude_m,ax,ay,az,gx,gy,gz,recovery_state,error_flags"
        ))

    def test_csv_row_from_card(self):
        """Строку из журнала на карте разбираем тем же кодом."""
        row = ("12840,7640,642,COAST,99584,21.8,87.42,87.42,"
               "0.14,-0.21,9.54,1.2,-0.8,0.6,ARMED,0")
        pkt = parse_line(row)
        self.assertEqual(pkt.seq, 642)
        self.assertEqual(pkt.state, "COAST")
        self.assertAlmostEqual(pkt.altitude_m, 87.42)
        self.assertEqual(pkt.recovery, "ARMED")

    def test_blank_lines(self):
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   \r\n"))

    def test_garbage_raises(self):
        with self.assertRaises(ParseError):
            parse_line("642|12840|COAST")

    def test_corrupt_number_raises(self):
        with self.assertRaises(ParseError):
            parse_line("642|12840|COAST|вы|99584|ARMED|0")

    def test_truncated_by_interference(self):
        """Обрыв в эфире даёт огрызок строки — это не должно падать."""
        with self.assertRaises(ParseError):
            parse_line("642|128")


class TestSeqGap(unittest.TestCase):

    def test_consecutive(self):
        self.assertEqual(seq_gap(10, 11), 0)

    def test_one_lost(self):
        self.assertEqual(seq_gap(10, 12), 1)

    def test_many_lost(self):
        self.assertEqual(seq_gap(10, 20), 9)

    def test_wraparound(self):
        """
        Счётчик на борту 16-разрядный и переполняется: за 65535 идёт 0.
        Переход 65535 -> 0 потерей не является.
        """
        self.assertEqual(seq_gap(SEQ_MODULO - 1, 0), 0)
        # 65534 -> 1 означает, что не дошли 65535 и 0, то есть два пакета.
        self.assertEqual(seq_gap(SEQ_MODULO - 2, 1), 2)
        self.assertEqual(seq_gap(SEQ_MODULO - 1, 1), 1)


class TestErrors(unittest.TestCase):

    def test_no_errors(self):
        self.assertEqual(describe_errors(0), [])

    def test_single(self):
        self.assertEqual(len(describe_errors(0x0002)), 1)

    def test_several(self):
        self.assertEqual(len(describe_errors(0x0002 | 0x0040)), 2)

    def test_critical(self):
        pkt = parse_line("1|100|READY|0|98450|ARMED|4")
        self.assertTrue(pkt.is_critical)

    def test_noncritical(self):
        """Отсутствие карты полёту не мешает, критической ошибкой не является."""
        pkt = parse_line("1|100|READY|0|98450|ARMED|8")
        self.assertFalse(pkt.is_critical)


class TestSession(unittest.TestCase):

    def setUp(self):
        self.s = Session()

    def feed_many(self, seqs):
        for n in seqs:
            self.s.feed(f"{n}|{n * 200}|COAST|{n}.0|98000|ARMED|0")

    def test_counts_received(self):
        self.feed_many([1, 2, 3])
        self.assertEqual(self.s.received, 3)
        self.assertEqual(self.s.lost, 0)

    def test_detects_loss(self):
        self.feed_many([1, 2, 5])
        self.assertEqual(self.s.received, 3)
        self.assertEqual(self.s.lost, 2)
        self.assertEqual(self.s.expected, 5)
        self.assertAlmostEqual(self.s.loss_percent, 40.0)

    def test_loss_percent_empty(self):
        self.assertEqual(self.s.loss_percent, 0.0)

    def test_tracks_max_altitude(self):
        self.feed_many([1, 2, 3])
        self.assertAlmostEqual(self.s.max_altitude, 3.0)

    def test_max_altitude_not_reduced_on_descent(self):
        self.s.feed("1|100|COAST|15.5|98000|ARMED|0")
        self.s.feed("2|300|DESCENT|9.0|98100|DEPLOYED|0")
        self.assertAlmostEqual(self.s.max_altitude, 15.5)

    def test_restart_not_counted_as_loss(self):
        """Перезапуск борта не должен выглядеть как потеря тысяч пакетов."""
        self.s.feed("5000|100|READY|0|98000|ARMED|0")
        self.s.feed("1|100|READY|0|98000|ARMED|0")
        self.assertEqual(self.s.restarts, 1)
        self.assertEqual(self.s.lost, 0)

    def test_bad_lines_do_not_stop_session(self):
        self.s.feed("1|100|READY|0|98000|ARMED|0")
        self.s.feed("мусор из эфира")
        self.s.feed("2|300|READY|0|98000|ARMED|0")
        self.assertEqual(self.s.received, 2)
        self.assertEqual(self.s.bad_lines, 1)
        self.assertEqual(self.s.lost, 0)

    def test_events_collected(self):
        self.s.feed("#100|LAUNCH_DETECTED")
        self.s.feed("#900|APOGEE_CONFIRMED")
        self.assertEqual(len(self.s.events), 2)
        self.assertEqual(self.s.events[1].name, "APOGEE_CONFIRMED")

    def test_reset(self):
        self.feed_many([1, 2, 5])
        self.s.reset()
        self.assertEqual(self.s.received, 0)
        self.assertEqual(self.s.lost, 0)
        self.assertIsNone(self.s.last_seq)

    def test_reconnect_keeps_counting(self):
        """
        Повторное подключение не должно ломать учёт: п. 45 ТЗ требует
        корректно переживать отключение и подключение заново.
        """
        self.feed_many([1, 2, 3])
        # разрыв связи на несколько пакетов
        self.feed_many([8, 9])
        self.assertEqual(self.s.received, 5)
        self.assertEqual(self.s.lost, 4)


if __name__ == "__main__":
    unittest.main()
