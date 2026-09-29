"""
ВРО-1 / RocketBoard — заливка прошивки в плату из наземной программы.

Делает то же, что мастер для Windows и release/flash.sh: переводит плату
в загрузчик открытием порта на 1200 бод, находит порт загрузчика (у
ATmega32U4 он получает другое имя) и пишет прошивку через avrdude.

Без окна:

    python3 vro_flash.py --list
    python3 vro_flash.py --firmware c_radio --port /dev/ttyACM0
    python3 vro_flash.py --hex мой.hex --port COM3
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
from typing import Callable, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
# В собранном .exe ресурсы распаковываются во временную папку.
BASE = getattr(sys, "_MEIPASS", HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

# Готовые прошивки: ключ файла vro1_<ключ>.hex и что это за набор.
FIRMWARES: List[Tuple[str, str]] = [
    ("a_basic",    "А: датчики + привод"),
    ("b_sd",       "Б: датчики + привод + карта памяти"),
    ("c_radio",    "В: датчики + привод + радио HC-12"),
    ("d_full",     "Г: полный набор (карта + радио)"),
    ("hc12_setup", "Настройка HC-12 (мощность, проверка канала)"),
]

# Пояснения для экрана: что внутри, когда брать, как управлять. Размер
# считается по самому файлу (hex_size_bytes), а не записан здесь.
FLASH_TOTAL_BYTES = 28672     # флеш ATmega32U4 за вычетом загрузчика

FIRMWARE_INFO = {
    "a_basic": {
        "title": "А: базовая (датчики + привод)",
        "has": "Инерциальный модуль ICM-20948, барометр BMP280, сервопривод парашюта. "
               "Без радио и без карты памяти.",
        "use": "Проверка платы и датчиков на столе, подбор углов привода, отладка по USB. "
               "Для полёта не подходит: данные видны только по проводу и нигде не сохраняются.",
        "ctrl": "USB, 115200: отладочная строка 5 раз в секунду и сервисные команды "
                "(привод SAFE/DEPLOY, углы, прогон профиля, обнуление высоты).",
    },
    "b_sd": {
        "title": "Б: с картой памяти (датчики + привод + microSD)",
        "has": "То же, что А, плюс запись полёта на карту microSD (журнал CSV, п. 19 ТЗ). "
               "Без радио.",
        "use": "Полёт, когда нужен только журнал на карте, а телеметрия по радио не нужна. "
               "Карта FAT32 обязательна.",
        "ctrl": "Сервисного режима нет, отладочного вывода нет: плата работает сама. "
                "После полёта журнал читается с карты.",
    },
    "c_radio": {
        "title": "В: с радио (датчики + привод + HC-12)",
        "has": "То же, что А, плюс передача телеметрии по радио HC-12 (5 пакетов в секунду, "
               "контрольная сумма, события повторяются).",
        "use": "Стенд и наземная отладка: телеметрия в станцию, отладка без провода от "
               "батареи (кнопки привода и «Обнулить высоту» по радио). Основная сборка "
               "для проверок радиоканала.",
        "ctrl": "USB, 115200: как у А. По радио: команды из режима «Отладка (радио)», "
                "работают только в состоянии READY. Карты памяти нет.",
    },
    "d_full": {
        "title": "Г: полная полётная (карта + радио)",
        "has": "Датчики, привод, запись на карту и радиотелеметрия HC-12 одновременно. "
               "Сервисного режима и отладочного вывода нет.",
        "use": "Боевой полёт: журнал на карте и телеметрия на землю. Перед пуском "
               "проверьте карту и радио в режиме «Боевой». Отладка привода и радиокоманд "
               "здесь недоступны, для них берите В.",
        "ctrl": "Управления нет: плата после включения сама ждёт старта. Память "
                "контроллера почти заполнена, запас минимальный.",
    },
    "hc12_setup": {
        "title": "Настройка радиомодуля HC-12 (не полётная)",
        "has": "Служебная программа: связывает USB с радиомодулем и позволяет задать "
               "мощность (клавиши 1..8) и посмотреть параметры (клавиша ~). "
               "Датчики и привод не используются.",
        "use": "Один раз при сборке или при смене модуля. Мощность P8 (+20 дБм) полётная. "
               "Модуль запоминает настройку сам. После настройки залейте В или Г.",
        "ctrl": "USB, 115200: клавиши 1..8 мощность, ~ параметры, p тестовые пакеты 5 раз "
                "в секунду. Всё остальное уходит в эфир как есть.",
    },
}


def hex_size_bytes(path: str) -> int:
    """Сколько байт программы в файле Intel HEX (сумма записей данных)."""
    total = 0
    try:
        with open(path, encoding="ascii", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if len(line) >= 11 and line[0] == ":" and line[7:9] == "00":
                    total += int(line[1:3], 16)
    except OSError:
        return 0
    return total


def describe(key: str, path: str) -> List[Tuple[str, str]]:
    """Пояснение к прошивке для экрана: список (заголовок, текст)."""
    size = hex_size_bytes(path)
    name = os.path.basename(path)
    rows: List[Tuple[str, str]] = []
    info = FIRMWARE_INFO.get(key)
    if info:
        rows += [("", info["title"]),
                 ("Что внутри", info["has"]),
                 ("Когда брать", info["use"]),
                 ("Управление и вывод", info["ctrl"])]
    else:
        rows.append(("", "Свой файл прошивки"))
    if size:
        pct = 100.0 * size / FLASH_TOTAL_BYTES
        rows.append(("Размер", f"{size} байт из {FLASH_TOTAL_BYTES} ({pct:.0f}% памяти программ)"))
    rows.append(("Файл", name))
    return rows


Log = Callable[[str], None]


def firmware_dir() -> Optional[str]:
    """Папка с готовыми .hex: рядом с программой или в release/bin проекта."""
    for d in (os.path.join(BASE, "firmware"),
              os.path.join(HERE, "firmware"),
              os.path.join(HERE, "..", "release", "bin")):
        if os.path.isdir(d) and glob.glob(os.path.join(d, "vro1_*.hex")):
            return os.path.abspath(d)
    return None


def available_firmwares() -> List[Tuple[str, str, str]]:
    """(ключ, описание, путь к hex) для тех прошивок, что реально лежат."""
    d = firmware_dir()
    result = []
    if d is None:
        return result
    for key, text in FIRMWARES:
        path = os.path.join(d, f"vro1_{key}.hex")
        if os.path.isfile(path):
            result.append((key, text, path))
    return result


def find_avrdude() -> Optional[Tuple[str, Optional[str], str]]:
    """
    (avrdude, файл настроек или None, рабочая папка) либо None.

    На Windows берётся avrdude.exe из проекта. Настройки передаются
    относительным путём от папки bin: так в командную строку не попадают
    русские буквы из пути к флешке или профилю пользователя.
    """
    roots = (os.path.join(BASE, "avrdude", "windows"),
             os.path.join(HERE, "avrdude", "windows"),
             os.path.join(HERE, "..", "release", "avrdude", "windows"))
    for root in roots if sys.platform.startswith("win") else ():
        exe = os.path.join(root, "bin", "avrdude.exe")
        if os.path.isfile(exe):
            return exe, os.path.join("..", "etc", "avrdude.conf"), os.path.dirname(exe)

    exe = shutil.which("avrdude")
    if exe:
        return exe, None, os.getcwd()

    # Как в flash.sh: avrdude из Arduino IDE.
    pats = os.path.expanduser("~/.arduino15/packages/arduino/tools/avrdude/*/bin/avrdude")
    found = sorted(glob.glob(pats))
    if found:
        conf = os.path.join(os.path.dirname(os.path.dirname(found[-1])), "etc", "avrdude.conf")
        return found[-1], conf if os.path.isfile(conf) else None, os.getcwd()
    return None


def _ports() -> List[str]:
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports()]


def _touch_1200(port: str) -> None:
    """Открыть порт на 1200 бод и закрыть: плата уходит в загрузчик."""
    import serial
    s = serial.Serial(port, 1200)
    time.sleep(0.15)
    s.close()


def _avrdude_port(port: str) -> str:
    # COM10 и выше avrdude на Windows принимает только в виде \\.\COM10.
    if sys.platform.startswith("win") and port.upper().startswith("COM"):
        return "\\\\.\\" + port
    return port


def flash(hex_path: str, port: str, log: Log = print) -> bool:
    """Записать hex в плату на порту port. True при успехе."""
    if not os.path.isfile(hex_path):
        log(f"Файл прошивки не найден: {hex_path}")
        return False

    tool = find_avrdude()
    if tool is None:
        log("Не найден avrdude. Debian и Ubuntu: sudo apt install avrdude")
        return False
    exe, conf, cwd = tool

    # Имя файла без русских букв: копия рядом с avrdude, если можно.
    hex_arg = hex_path
    tmp_copy = None
    if conf is not None:
        try:
            tmp_copy = os.path.join(cwd, "vro_fw.hex")
            shutil.copyfile(hex_path, tmp_copy)
            hex_arg = "vro_fw.hex"
        except OSError:
            tmp_copy = None

    try:
        log(f"Прошивка: {os.path.basename(hex_path)}")
        before = set(_ports())

        log(f"Перевод платы в загрузчик через {port}...")
        try:
            _touch_1200(port)
        except Exception as exc:
            log(f"Не удалось открыть порт {port}: {exc}")
            return False
        time.sleep(1.0)

        boot = port
        for _ in range(20):
            new = [p for p in _ports() if p not in before]
            if new:
                boot = new[0]
                break
            time.sleep(0.25)
        log(f"Порт загрузчика: {boot}")

        cmd = [exe] + (["-C", conf] if conf else []) + [
            "-p", "atmega32u4", "-c", "avr109", "-P", _avrdude_port(boot),
            "-b", "57600", "-D", "-U", f"flash:w:{hex_arg}:i"]

        flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW
        for attempt in range(1, 4):
            log(f"Запись (попытка {attempt})...")
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, creationflags=flags)
            text = b""
            buf = b""
            while True:
                chunk = proc.stdout.read(1)
                if not chunk:
                    break
                text += chunk
                if chunk in (b"\r", b"\n"):
                    line = buf.decode("utf-8", errors="replace").strip()
                    if line:
                        log(line)
                    buf = b""
                else:
                    buf += chunk
            if buf.strip():
                log(buf.decode("utf-8", errors="replace").strip())
            if proc.wait() == 0:
                log("Готово. Плата перезапустится сама, подождите 3 секунды.")
                return True
            if attempt < 3:
                log("Загрузчик не ответил, пробуем ещё раз...")
                time.sleep(1.0)
                # Порт мог смениться: ищем ещё раз.
                new = [p for p in _ports() if p not in before]
                if new:
                    boot = new[0]
                    cmd[cmd.index("-P") + 1] = _avrdude_port(boot)
        log("Прошивка не записана. Проверьте кабель (нужен с передачей данных) "
            "и попробуйте ещё раз.")
        return False
    finally:
        if tmp_copy:
            try:
                os.remove(tmp_copy)
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Заливка прошивки ВРО-1")
    ap.add_argument("--list", action="store_true", help="показать прошивки и выйти")
    ap.add_argument("--firmware", help="ключ: a_basic, b_sd, c_radio, d_full, hc12_setup")
    ap.add_argument("--hex", help="свой файл .hex")
    ap.add_argument("--port")
    a = ap.parse_args()

    fws = available_firmwares()
    if a.list:
        for key, text, path in fws:
            print(f"{key:<12} {text}   [{path}]")
        tool = find_avrdude()
        print("avrdude:", tool[0] if tool else "не найден")
        return 0

    if not a.port or not (a.firmware or a.hex):
        print("Нужны --port и --firmware (или --hex). Список: --list")
        return 2
    path = a.hex
    if a.firmware:
        match = [p for k, _, p in fws if k == a.firmware]
        if not match:
            print(f"Нет прошивки {a.firmware!r}. Список: --list")
            return 2
        path = match[0]
    return 0 if flash(path, a.port) else 1


if __name__ == "__main__":
    sys.exit(main())
