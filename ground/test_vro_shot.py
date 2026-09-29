# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
Проверка кодировщика PNG для снимка экрана.

Запуск:  python3 -m unittest discover ground
"""

import struct
import unittest
import zlib

from vro_shot import png_bytes


def decode(png: bytes):
    """Минимальный разбор нашего PNG: ширина, высота, RGB-данные."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(png):
        n, = struct.unpack(">I", png[pos:pos + 4])
        tag = png[pos + 4:pos + 8]
        data = png[pos + 8:pos + 8 + n]
        crc, = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF, f"CRC чанка {tag}"
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", data[:10])
            assert (depth, ctype) == (8, 2)
            size = (w, h)
        elif tag == b"IDAT":
            idat += data
        pos += 12 + n
    raw = zlib.decompress(idat)
    w, h = size
    rows = []
    for y in range(h):
        line = raw[y * (w * 3 + 1):(y + 1) * (w * 3 + 1)]
        assert line[0] == 0                 # фильтр «нет»
        rows.append(line[1:])
    return w, h, b"".join(rows)


class TestPng(unittest.TestCase):
    def test_roundtrip(self):
        rgb = bytes([255, 0, 0,   0, 255, 0,   0, 0, 255,
                     10, 20, 30,  40, 50, 60,  70, 80, 90])       # 3 x 2
        w, h, back = decode(png_bytes(3, 2, rgb))
        self.assertEqual((w, h, back), (3, 2, rgb))

    def test_bigger_picture_roundtrip(self):
        w, h = 200, 120
        rgb = bytes((x * 3 + y * 7 + c) % 256 for y in range(h) for x in range(w) for c in range(3))
        self.assertEqual(decode(png_bytes(w, h, rgb)), (w, h, rgb))

    def test_wrong_size_rejected(self):
        with self.assertRaises(ValueError):
            png_bytes(2, 2, b"\x00" * 5)


if __name__ == "__main__":
    unittest.main()
