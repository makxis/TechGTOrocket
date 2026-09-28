#!/bin/sh
#
# ВРО-1 / RocketBoard — заливка готовой прошивки. Linux и macOS.
#
#   ./flash.sh a_basic                    порт ищется сам
#   ./flash.sh d_full /dev/ttyACM0        порт задан явно
#
# Наборы: a_basic, b_sd, c_radio, d_full. Что означают — в FLASHING.md.
#
# Почему нельзя просто позвать avrdude.
#
# У Pro Micro на ATmega32U4 загрузчик не сидит в памяти постоянно. Чтобы
# в него попасть, плату надо сбросить, открыв её порт на скорости 1200
# бод и сразу закрыв. После этого плата на пару секунд появляется в
# системе под ДРУГИМ именем устройства — вот в него и надо писать.
# Скрипт делает это сам.

set -e

PROFILE="${1:-}"
PORT="${2:-}"

DIR="$(cd "$(dirname "$0")" && pwd)"
HEX="$DIR/bin/vro1_$PROFILE.hex"

if [ -z "$PROFILE" ] || [ ! -f "$HEX" ]; then
    echo "Укажите набор: a_basic, b_sd, c_radio, d_full или hc12_setup" >&2
    echo "Например:  ./flash.sh a_basic" >&2
    exit 1
fi

# ---- где avrdude -------------------------------------------------------

# Основной способ прошивки — мастер для Windows. Этот скрипт для тех,
# у кого Linux или macOS: avrdude берётся из системы или из Arduino.
AVRDUDE="${AVRDUDE:-$(command -v avrdude || true)}"
AVRCONF=""
if [ -z "$AVRDUDE" ]; then
    AVRDUDE=$(ls "$HOME"/.arduino15/packages/arduino/tools/avrdude/*/bin/avrdude 2>/dev/null | head -1 || true)
    CONF=$(ls "$HOME"/.arduino15/packages/arduino/tools/avrdude/*/etc/avrdude.conf 2>/dev/null | head -1 || true)
    [ -n "$CONF" ] && AVRCONF="-C $CONF"
fi
if [ -z "$AVRDUDE" ]; then
    echo "Не найден avrdude." >&2
    echo "Debian и Ubuntu:  sudo apt install avrdude" >&2
    exit 1
fi

# ---- какой порт --------------------------------------------------------

list_ports() { ls /dev/ttyACM* /dev/ttyUSB* /dev/cu.usbmodem* 2>/dev/null || true; }

if [ -z "$PORT" ]; then
    PORT=$(list_ports | head -1)
    if [ -z "$PORT" ]; then
        echo "Плата не найдена. Подключите её по USB." >&2
        exit 1
    fi
    echo "Порт определён сам: $PORT"
fi

echo "Набор:    $PROFILE"
echo "Прошивка: $HEX"

# ---- сброс в загрузчик -------------------------------------------------

BEFORE=$(list_ports)

echo "Перевод платы в режим загрузчика..."
if command -v stty >/dev/null 2>&1; then
    stty -F "$PORT" 1200 hupcl 2>/dev/null || stty -f "$PORT" 1200 hupcl 2>/dev/null || true
fi
sleep 1

# Ищем порт, которого не было до сброса: это и есть загрузчик.
BOOT=""
n=0
while [ $n -lt 20 ]; do
    NOW=$(list_ports)
    for p in $NOW; do
        echo "$BEFORE" | grep -qx "$p" || BOOT="$p"
    done
    [ -n "$BOOT" ] && break
    n=$((n + 1))
    sleep 0.25
done

if [ -z "$BOOT" ]; then
    # Некоторые платы возвращаются под тем же именем.
    BOOT="$PORT"
    echo "Порт загрузчика не изменился, пишем в $BOOT"
else
    echo "Загрузчик на порту: $BOOT"
fi

# ---- запись ------------------------------------------------------------

echo "Запись..."
"$AVRDUDE" $AVRCONF -c avr109 -p atmega32u4 -P "$BOOT" -b 57600 -D \
    -U "flash:w:$HEX:i"

echo
echo "Готово. Плата перезапустится сама."
echo "Посмотреть телеметрию (наборы a_basic и c_radio):"
echo "    picocom -b 115200 $PORT        выход: Ctrl+A, Ctrl+X"
