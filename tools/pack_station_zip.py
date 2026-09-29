#!/usr/bin/env python3
"""
Собрать release/vro1-station-build.zip: всё, что нужно для сборки
VRO1-Station.exe на Windows (исходники станции, pyserial, готовые прошивки,
avrdude, build_exe.bat).

    python3 tools/pack_station_zip.py

Запускать после любых правок ground/*.py или пересборки release/bin.
Файлы .py берутся списком по шаблону, чтобы новый модуль не забыть.
"""

import glob
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "release", "vro1-station-build.zip")

HOWTO = """Сборка VRO1-Station.exe (нужна Windows)

1. Установить Python 3 с https://www.python.org/downloads/ ,
   при установке отметить "Add python.exe to PATH".
2. Распаковать этот архив целиком (правая кнопка - "Извлечь все...").
3. Запустить build_exe.bat (нужен интернет, скачивается PyInstaller).
4. Готовый файл: dist\\VRO1-Station.exe. Копировать можно куда угодно.
   Журналы CSV он пишет в папку logs рядом с собой. Готовые прошивки и
   avrdude зашиты внутрь, поэтому из него можно прошивать плату.

Если антивирус ругается на свежесобранный .exe, добавьте его в исключения.
Без сборки программа запускается так: python vro_station.py
"""


def main() -> int:
    ground = os.path.join(ROOT, "ground")
    # Всё, что не тесты: модули станции. Тесты в сборку не нужны.
    sources = [p for p in glob.glob(os.path.join(ground, "*.py"))
               if not os.path.basename(p).startswith("test_")
               and os.path.basename(p) != "fake_rocket.py"]
    needed = {"vro_station.py", "vro_link.py", "vro_flash.py", "vro_graph.py",
              "telemetry.py", "rocket_ground.py"}
    missing = needed - {os.path.basename(p) for p in sources}
    if missing:
        print("Нет файлов:", ", ".join(sorted(missing)))
        return 1

    tmp = tempfile.mkdtemp()
    try:
        d = os.path.join(tmp, "vro1-station-build")
        os.makedirs(d)
        for p in sources:
            shutil.copy(p, d)
        shutil.copy(os.path.join(ground, "build_exe.bat"), d)
        shutil.copytree(os.path.join(ground, "vendor"), os.path.join(d, "vendor"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(os.path.join(ROOT, "release", "bin"), os.path.join(d, "firmware"),
                        ignore=shutil.ignore_patterns("SHA256SUMS.txt"))
        shutil.copytree(os.path.join(ROOT, "release", "avrdude"), os.path.join(d, "avrdude"))
        with open(os.path.join(d, "КАК СОБРАТЬ.txt"), "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(HOWTO)

        if os.path.exists(OUT):
            os.remove(OUT)
        with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(tmp):
                for f in files:
                    full = os.path.join(root, f)
                    zf.write(full, os.path.relpath(full, tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{OUT}: {os.path.getsize(OUT) // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
