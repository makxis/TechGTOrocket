# Сборка и заливка

Всё, что нужно, чтобы с нуля собрать прошивку из исходников, залить её
в плату и посмотреть, что плата выдаёт.

**Нужно только прошить готовую прошивку?** Этот документ не нужен:
запустите `ПРОШИТЬ ПЛАТУ.bat` (Windows), см. `release/FLASHING.md`.

Сборка из исходников описана для Linux; для Windows отличия указаны
отдельно.

## 1. Что понадобится

| Что | Зачем |
|---|---|
| Плата RocketBoard с Pro Micro (ATmega32U4) | борт |
| Кабель USB, **с линиями данных** | заливка и отладочный вывод |
| Компьютер с Linux, Windows или macOS | сборка |
| `arduino-cli` | компилятор и заливка |
| `picocom` (Linux) или любой терминал порта | смотреть вывод платы |
| Python 3.8+ и `pyserial` | наземная программа, см. `ground/README.md` |

Arduino IDE не нужна. Проект собирается из командной строки скриптом
`firmware/build.sh`, потому что ему нужны флаги сборки, которые IDE
передать не умеет (подробности в п. 11 раздела 6 HANDOFF.md).

## 2. Установка, один раз

### Linux

```sh
# arduino-cli в ~/.local/bin (или любой каталог из PATH)
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
  | BINDIR=~/.local/bin sh
export PATH=~/.local/bin:$PATH        # добавить в ~/.bashrc

arduino-cli config init --overwrite
arduino-cli core update-index
arduino-cli core install arduino:avr@1.8.8

sudo apt install picocom python3-serial python3-tk
sudo usermod -aG dialout $USER        # доступ к порту без sudo, перелогиниться
```

### Windows и macOS

`arduino-cli` 1.5.1 скачать с https://github.com/arduino/arduino-cli/releases/tag/v1.5.1
(`arduino-cli_1.5.1_Windows_64bit.zip`) и положить в `PATH`, дальше те же
`config init` и `core install`.
Скрипт `build.sh` написан для `sh`: на Windows запускать из Git Bash или
WSL, либо выполнить `arduino-cli compile` вручную с флагами из скрипта.
Порт на Windows называется `COM3`, `COM4` и т. п., на macOS
`/dev/cu.usbmodem…`.

### Версии, на которых всё проверено

| Что | Версия |
|---|---|
| `arduino-cli` | 1.5.1 |
| ядро `arduino:avr` | 1.8.8 (с `avr-gcc` и `avrdude` 8.0.0) |
| `Servo` | 1.3.0 |
| `SdFat` | 2.3.0 |

**Библиотеки ставить не нужно.** `Servo` 1.3.0 и `SdFat` 2.3.0 лежат в
`firmware/libraries/` (только исходники и лицензии), и профили в
`firmware/sketch.yaml` ссылаются именно на них (`dir: libraries/...`).
Даже если в системе стоят другие версии, сборка возьмёт эти — проверено:
при скрытых системных копиях результат совпадает байт в байт.
Версия ядра `arduino:avr` 1.8.8 тоже закреплена в профилях: если
установлена другая, `arduino-cli` сам докачает нужную.

Если `arduino-cli` лежит не в `PATH`, путь к нему можно передать
скрипту: `ARDUINO_CLI=/путь/к/arduino-cli ./build.sh`.

## 3. Параметры платы

```
FQBN:  arduino:avr:leonardo
Порт:  /dev/ttyACM0 (Linux)
```

Плата перечисляется как Arduino Leonardo (`2341:8036`). FQBN
`SparkFun:avr:promicro` брать **не нужно**: при нём не совпадёт
процедура сброса в загрузчик.

Проверить, что плата видна:

```sh
arduino-cli board list
```

Если подключено несколько плат, номера `ttyACM0/1` могут меняться
местами после каждой заливки. Надёжнее ссылаться на USB-разъём, к
которому воткнута плата:

```sh
ls -l /dev/serial/by-path/
```

## 4. Сборка и заливка прошивки

```sh
cd firmware
./build.sh all                    # собрать все наборы и увидеть расход памяти
./build.sh release                # пересобрать release/bin
./build.sh c_radio                # собрать один набор
./build.sh c_radio upload         # собрать и залить в /dev/ttyACM0
PORT=/dev/ttyACM1 ./build.sh c_radio upload   # в другой порт
```

Наборы (`a_basic`, `b_sd`, `c_radio`, `d_full`) описаны в README.md.
Какой набор собирать — определяется тем, что физически стоит на плате.

Признак неисправной сборки: все наборы дают одинаковый размер. Значит,
флаги до компилятора не дошли.

### Готовые прошивки без сборки

Файлы `.hex` для всех наборов лежат в `release/bin/`, как их залить без
`arduino-cli` — в `release/FLASHING.md`. После изменений в коде их надо
пересобрать (раздел 7).

## 5. Диагностические скетчи

Скетчи из `tools/` собираются напрямую, без `build.sh`:

```sh
cd tools
arduino-cli compile --fqbn arduino:avr:leonardo hw_probe
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:leonardo hw_probe
```

Каталог скетча и имя `.ino` внутри должны совпадать, так требует
`arduino-cli`.

| Скетч | Что делает |
|---|---|
| `hw_probe` | скан шины I2C, чтение идентификаторов |
| `imu_id` | полный дамп регистров инерциального модуля |
| `imu_live` | живые показания ICM-20948 |
| `hc12_setup` | настройка HC-12 и мост USB ↔ радио, см. RADIO.md |

## 6. Чтение последовательного порта

Интерактивно, с возможностью подавать команды:

```sh
picocom -b 115200 /dev/ttyACM0     # выход: Ctrl+A, Ctrl+X
```

Неинтерактивно, например чтобы сохранить лог:

```sh
stty -F /dev/ttyACM0 115200 raw -echo
timeout 15 cat /dev/ttyACM0 | tee board.log
```

Подать одну команду без терминала (например, `r` — прогон профиля):

```sh
printf r > /dev/ttyACM0
```

На Windows подойдёт PuTTY или монитор порта Arduino IDE, скорость 115200.

## 7. Пересборка готовых прошивок в release/bin

```sh
cd firmware
./build.sh release
```

Скрипт соберёт все четыре набора, положит `vro1_<набор>.hex` в
`release/bin/` и обновит `SHA256SUMS.txt`.

У набора, код которого не менялся, `.hex` получается байт в байт тем же.

## 8. Особенности ATmega32U4, про которые легко забыть

1. **Порт пропадает при заливке.** У Leonardo `Serial` — это USB CDC, а не
   аппаратный UART. При заливке `arduino-cli` дёргает порт на 1200 бод,
   плата уходит в загрузчик и переподключается, иногда под другим номером.
   Между заливкой и чтением порта нужна пауза секунд в шесть-восемь,
   иначе можно прицепиться к порту загрузчика и получить пустой лог.

2. **`while (!Serial)` повесит прошивку.** Без подключённого компьютера
   условие не выполнится никогда, и ракета не взлетит. Это противоречит
   п. 2 ТЗ (работа без постоянного подключения к компьютеру).

3. **Аппаратный UART отдельно.** `Serial` — это USB, а `Serial1` —
   настоящий UART на `D0`/`D1`. HC-12 висит на `Serial1` и отладочному
   выводу по USB не мешает.

4. **`Serial1` не мешает заливке.** Радиомодуль можно не отключать.

## 9. Проверка после заливки

```sh
arduino-cli board list
picocom -b 115200 /dev/ttyACM0
```

Через несколько секунд после старта плата печатает строки вида

```
9635 READY h=-0.13 max=0.00 |a|=1.00 p=99465 rec=ARMED err=0x0 rdrop=0
```

Прошивка жива, если состояние `READY`, `err=0x0`, высота около нуля, а
модуль ускорения около 1,00 g. Дальше — `?` для справки, `r` для прогона
тестового профиля. Отладочный вывод есть только в наборах A и C.
