"""
Проверка палитр тем и описаний прошивок.

Запуск:  python3 -m unittest discover ground
"""

import os
import tempfile
import unittest

import vro_flash
from vro_station import (PALETTES, load_theme, save_theme, settings_path_for,
                         save_settings, load_battery_full)


class TestPalettes(unittest.TestCase):
    def test_same_roles_in_both_themes(self):
        self.assertEqual(set(PALETTES["dark"]), set(PALETTES["light"]))

    def test_dark_values_are_unique(self):
        # Перекраска ищет цвет тёмной темы и подставляет цвет светлой:
        # одинаковые значения у разных ролей перепутали бы их.
        vals = [v.lower() for v in PALETTES["dark"].values()]
        self.assertEqual(len(vals), len(set(vals)))

    def test_light_values_are_unique(self):
        # Обратная перекраска (светлая -> тёмная) по тому же принципу.
        vals = [v.lower() for v in PALETTES["light"].values()]
        self.assertEqual(len(vals), len(set(vals)))


class TestThemeSettings(unittest.TestCase):
    def test_roundtrip_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            path = settings_path_for(os.path.join(d, "logs"))
            self.assertEqual(load_theme(path), "dark")          # файла нет
            save_theme(path, "light")
            self.assertEqual(load_theme(path), "light")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{не json")
            self.assertEqual(load_theme(path), "dark")          # испорчен

    def test_unknown_theme_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            save_theme(path, "розовая")
            self.assertEqual(load_theme(path), "dark")


class TestSettingsMerge(unittest.TestCase):
    def test_theme_and_battery_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            save_settings(path, battery_full_v=9.31)
            save_theme(path, "light")
            self.assertEqual(load_theme(path), "light")
            self.assertAlmostEqual(load_battery_full(path), 9.31)

    def test_battery_default_and_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            self.assertAlmostEqual(load_battery_full(path), 8.04)         # файла нет
            save_settings(path, battery_full_v=4.2)                       # не батарея
            self.assertAlmostEqual(load_battery_full(path), 8.04)
            save_settings(path, battery_full_v="мусор")
            self.assertAlmostEqual(load_battery_full(path), 8.04)


class TestFirmwareInfo(unittest.TestCase):
    def test_every_listed_firmware_has_description(self):
        for key, _text in vro_flash.FIRMWARES:
            self.assertIn(key, vro_flash.FIRMWARE_INFO)
            for field in ("title", "has", "use", "ctrl"):
                self.assertTrue(vro_flash.FIRMWARE_INFO[key][field])

    def test_hex_size_counts_data_records(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.hex")
            with open(path, "w") as fh:
                fh.write(":10000000000102030405060708090A0B0C0D0E0F78\n"
                         ":04001000AABBCCDD1E\n"
                         ":00000001FF\n")
            self.assertEqual(vro_flash.hex_size_bytes(path), 20)

    def test_describe_shows_size_and_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "vro1_c_radio.hex")
            with open(path, "w") as fh:
                fh.write(":04000000AABBCCDDEE\n:00000001FF\n")
            rows = dict((h, t) for h, t in vro_flash.describe("c_radio", path))
        self.assertIn("4 байт из 28672", rows["Размер"])
        self.assertEqual(rows["Файл"], "vro1_c_radio.hex")
        self.assertIn("Что внутри", rows)


if __name__ == "__main__":
    unittest.main()
