#!/bin/sh
#
# ВРО-1 / RocketBoard — сборка и заливка
#
#   ./build.sh                 собрать набор по умолчанию (A)
#   ./build.sh b_sd            собрать набор B
#   ./build.sh d_full upload   собрать набор D и залить в плату
#   ./build.sh all             собрать все наборы и показать расход памяти
#   ./build.sh release         пересобрать готовые прошивки в release/bin
#
# Зачем этот скрипт, а не один arduino-cli: версия 1.5.1 игнорирует
# build_properties внутри профиля sketch.yaml. Флаги доходят до
# компилятора только через --build-property в командной строке, а
# настройки SdFat обязаны быть глобальными — библиотека собирается
# своими единицами трансляции. Держать эти флаги в скрипте надёжнее,
# чем требовать от человека помнить их наизусть.
#
# Проверено на arduino-cli 1.5.1.

set -e

ARDUINO_CLI="${ARDUINO_CLI:-arduino-cli}"
PORT="${PORT:-/dev/ttyACM0}"
SKETCH_DIR="$(cd "$(dirname "$0")" && pwd)"

# Урезание SdFat под FAT32. Что и почему выброшено — в sketch.yaml.
SDFAT_FLAGS="-DSDFAT_FILE_TYPE=1 \
-DUSE_LONG_FILE_NAMES=0 \
-DMAINTAIN_FREE_CLUSTER_COUNT=0 \
-DUSE_FAT_FILE_FLAG_CONTIGUOUS=0 \
-DCHECK_FLASH_PROGRAMMING=0 \
-DENABLE_DEDICATED_SPI=0 \
-DUSE_SEPARATE_FAT_CACHE=0 \
-DUSE_MULTI_SECTOR_IO=0 \
-DINCLUDE_SDIOS=0"

# Номер набора и нужны ли флаги SdFat.
flags_for() {
    case "$1" in
        a_basic) echo "-DHW_PROFILE=1" ;;
        b_sd)    echo "-DHW_PROFILE=2 $SDFAT_FLAGS" ;;
        c_radio) echo "-DHW_PROFILE=3" ;;
        d_full)  echo "-DHW_PROFILE=4 $SDFAT_FLAGS" ;;
        *)
            echo "Неизвестный набор: $1" >&2
            echo "Допустимы: a_basic, b_sd, c_radio, d_full" >&2
            exit 1
            ;;
    esac
}

build() {
    "$ARDUINO_CLI" compile \
        --profile "$1" \
        --build-property "compiler.cpp.extra_flags=$(flags_for "$1")" \
        "$SKETCH_DIR"
}

upload() {
    "$ARDUINO_CLI" upload \
        --profile "$1" \
        -p "$PORT" \
        "$SKETCH_DIR"
}

PROFILE="${1:-a_basic}"

if [ "$PROFILE" = "release" ]; then
    OUT="$SKETCH_DIR/../release/bin"
    TMP="$(mktemp -d)"
    for p in a_basic b_sd c_radio d_full; do
        echo "=== $p ==="
        "$ARDUINO_CLI" compile \
            --profile "$p" \
            --build-property "compiler.cpp.extra_flags=$(flags_for "$p")" \
            --output-dir "$TMP/$p" \
            "$SKETCH_DIR" 2>&1 | grep -E "Скетч использует|Sketch uses|слишком большой|too big" || true
        cp "$TMP/$p/firmware.ino.hex" "$OUT/vro1_$p.hex"
    done
    # Скетч настройки радио — нужен мастеру прошивки, чтобы ставить
    # мощность HC-12 без arduino-cli.
    echo "=== hc12_setup ==="
    "$ARDUINO_CLI" compile --fqbn arduino:avr:leonardo \
        --output-dir "$TMP/hc12_setup" \
        "$SKETCH_DIR/../tools/hc12_setup" 2>&1 | grep -E "Скетч использует|Sketch uses" || true
    cp "$TMP/hc12_setup/hc12_setup.ino.hex" "$OUT/vro1_hc12_setup.hex"
    rm -rf "$TMP"
    (cd "$OUT" && sha256sum vro1_*.hex > SHA256SUMS.txt && cat SHA256SUMS.txt)
    exit 0
fi

if [ "$PROFILE" = "all" ]; then
    for p in a_basic b_sd c_radio d_full; do
        echo "=== $p ==="
        build "$p" 2>&1 | grep -E "Скетч использует|Глобальные пере|слишком большой" || true
        echo
    done
    exit 0
fi

build "$PROFILE"

if [ "$2" = "upload" ]; then
    # После сборки плата уходит в загрузчик и переподключается под другим
    # номером устройства — это особенность USB CDC у ATmega32U4.
    upload "$PROFILE"
fi
