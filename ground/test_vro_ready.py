"""
Проверка готовности к пуску и допустимых команд.

Запуск:  python3 -m unittest discover ground
"""

import unittest

from vro_ready import readiness, allowed_commands, short, OK, WARN, BAD, INFO


class TestReadiness(unittest.TestCase):
    def test_ready_to_launch(self):
        self.assertEqual(readiness("READY", "ARMED", 0, 0.2)[0], OK)

    def test_no_data_is_not_ready(self):
        self.assertEqual(readiness(None, None, 0, None)[0], BAD)
        self.assertEqual(readiness("READY", "ARMED", 0, 5.0)[0], BAD)   # данные устарели

    def test_deployed_in_ready_blocks_launch(self):
        level, text = readiness("READY", "DEPLOYED", 0, 0.2)
        self.assertEqual(level, BAD)
        self.assertIn("РАСКРЫТ", text)

    def test_recovery_not_armed_blocks_launch(self):
        self.assertEqual(readiness("READY", "SAFE", 0, 0.2)[0], BAD)
        self.assertEqual(readiness("READY", "ERROR", 0, 0.2)[0], BAD)

    def test_landed_is_not_ready(self):
        level, text = readiness("LANDED", "DEPLOYED", 0, 0.2)
        self.assertEqual(level, BAD)
        self.assertIn("READY", text)

    def test_critical_error_blocks_launch(self):
        level, text = readiness("READY", "ARMED", 0x0002, 0.2)   # отказ барометра
        self.assertEqual(level, BAD)
        self.assertIn("КРИТИЧЕСКАЯ", text)

    def test_low_battery_is_a_warning_not_ready_green(self):
        level, text = readiness("READY", "ARMED", 0x0200, 0.2)
        self.assertEqual(level, WARN)
        self.assertIn("батарея", text)

    def test_in_flight_is_info(self):
        self.assertEqual(readiness("COAST", "ARMED", 0, 0.2)[0], INFO)

    def test_init_is_warning(self):
        self.assertEqual(readiness("INIT", "SAFE", 0, 0.2)[0], WARN)


class TestShort(unittest.TestCase):
    def test_short_keeps_first_sentence(self):
        text = readiness("LANDED", "DEPLOYED", 0, 0.2)[1]
        self.assertEqual(short(text), "ПОСАДКА: пуск невозможен")
        self.assertEqual(short("ГОТОВ К ПУСКУ"), "ГОТОВ К ПУСКУ")
        self.assertLess(len(short(readiness("READY", "DEPLOYED", 0, 0.2)[1])), 45)


class TestAllowedCommands(unittest.TestCase):
    def test_ready_allows_profile_and_servo(self):
        c = allowed_commands("READY", "ARMED")
        self.assertTrue({"r", "s", "d", "z", "t", "6"} <= c)
        self.assertNotIn("R", c)

    def test_deployed_in_ready_forbids_profile_allows_return(self):
        c = allowed_commands("READY", "DEPLOYED")
        self.assertNotIn("r", c)
        self.assertIn("R", c)

    def test_landed_allows_only_return(self):
        self.assertEqual(allowed_commands("LANDED", "DEPLOYED"), {"R"})

    def test_flight_and_unknown_allow_nothing(self):
        self.assertEqual(allowed_commands("COAST", "ARMED"), set())
        self.assertEqual(allowed_commands(None, None), set())

    def test_wired_keeps_info_commands(self):
        self.assertEqual(allowed_commands("COAST", "ARMED", wired=True), set("i?"))
        self.assertIn("i", allowed_commands("READY", "ARMED", wired=True))


if __name__ == "__main__":
    unittest.main()
