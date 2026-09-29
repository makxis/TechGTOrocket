# SPDX-License-Identifier: MIT
# Copyright (c) 2026 makxis
"""
ВРО-1 / RocketBoard — снимок экрана окна программы в PNG (без окна).

Без сторонних библиотек: на Windows окно снимается системным вызовом
(BitBlt через ctypes), PNG кодируется здесь же. Так работает и в собранном
.exe, ничего доустанавливать не нужно. На Linux используется утилита
`import` (ImageMagick) или Pillow, если они есть.
"""

import os
import struct
import subprocess
import sys
import zlib
from typing import Tuple


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Закодировать RGB (3 байта на пиксель, строки сверху вниз) в PNG."""
    if len(rgb) != width * height * 3:
        raise ValueError("размер данных не совпадает с размерами картинки")
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def _grab_windows(x: int, y: int, w: int, h: int) -> bytes:
    """Прямоугольник экрана в RGB. Только Windows."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    vp = ctypes.c_void_p

    user32.GetDC.argtypes = [vp]
    user32.GetDC.restype = vp
    user32.ReleaseDC.argtypes = [vp, vp]
    gdi32.CreateCompatibleDC.argtypes = [vp]
    gdi32.CreateCompatibleDC.restype = vp
    gdi32.CreateCompatibleBitmap.argtypes = [vp, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = vp
    gdi32.SelectObject.argtypes = [vp, vp]
    gdi32.SelectObject.restype = vp
    gdi32.BitBlt.argtypes = [vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             vp, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [vp, vp, wintypes.UINT, wintypes.UINT, vp, vp, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [vp]
    gdi32.DeleteDC.argtypes = [vp]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT):
            raise OSError("BitBlt не удался")
        info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0):
            raise OSError("GetDIBits не удался")
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)

    bgra = buf.raw
    rgb = bytearray(w * h * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    return bytes(rgb)


def window_rect(root) -> Tuple[int, int, int, int]:
    """Область окна на экране: x, y, ширина, высота."""
    root.update_idletasks()
    return root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()


def save_window_png(root, path: str) -> str:
    """Снять окно программы и сохранить в path. Возвращает path."""
    x, y, w, h = window_rect(root)
    if w < 10 or h < 10:
        raise RuntimeError("окно свёрнуто или слишком маленькое")

    if sys.platform.startswith("win"):
        data = png_bytes(w, h, _grab_windows(x, y, w, h))
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    # Linux, macOS: сначала утилита import (ImageMagick), потом Pillow.
    try:
        subprocess.run(["import", "-window", hex(root.winfo_id()), path],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15)
        if os.path.getsize(path) > 0:
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from PIL import ImageGrab                      # type: ignore
        ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(path)
        return path
    except Exception as exc:
        raise RuntimeError("нет способа снять экран (нужен ImageMagick или Pillow): "
                           f"{exc}") from exc
