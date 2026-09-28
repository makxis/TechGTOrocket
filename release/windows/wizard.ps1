#
# ВРО-1 / RocketBoard — мастер прошивки платы для Windows.
#
# Запускается двойным щелчком по «ПРОШИТЬ ПЛАТУ.bat» в корне проекта.
# Ничего не устанавливает и не требует интернета: avrdude и готовые
# прошивки лежат рядом, в release\avrdude\windows и release\bin.
#
# Рассчитан на Windows PowerShell 5.1 (есть в Windows 10 и 11 из коробки).
#
# Как устроена заливка. У ATmega32U4 загрузчик не сидит в памяти
# постоянно. Чтобы в него попасть, порт платы открывают на скорости
# 1200 бод и сразу закрывают. Плата перезагружается и на 8 секунд
# появляется в системе под ДРУГИМ номером COM-порта — туда и пишем.
#

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
try { $Host.UI.RawUI.WindowTitle = 'ВРО-1: прошивка платы' } catch { }

$Release  = Split-Path -Parent $PSScriptRoot
$BinDir   = Join-Path $Release 'bin'
$Avrdude  = Join-Path $Release 'avrdude\windows\bin\avrdude.exe'
# Путь к настройкам avrdude передаётся относительным от папки bin: так
# в командную строку avrdude не попадают русские буквы из пути к флешке.
$AvrConf  = '..\avrdude\windows\etc\avrdude.conf'
$LogFile  = Join-Path $env:TEMP 'vro1_flash_log.txt'

# Файлы из скачанного архива Windows помечает как «из интернета» и может
# спрашивать подтверждение на каждый. Снимаем пометку со своих файлов.
try { Get-ChildItem -Path $Release -Recurse -File | Unblock-File -ErrorAction SilentlyContinue } catch { }

$SetNames = @{
    'a_basic' = 'A — датчики и сервопривод'
    'b_sd'    = 'B — датчики, сервопривод и карта памяти MicroSD'
    'c_radio' = 'C — датчики, сервопривод и радиомодуль HC-12'
    'd_full'  = 'D — всё вместе: датчики, сервопривод, MicroSD и HC-12'
}

# ------------------------------------------------------------------ #
#  Вывод на экран                                                     #
# ------------------------------------------------------------------ #

function Say($text)   { Write-Host $text }
function Good($text)  { Write-Host $text -ForegroundColor Green }
function Bad($text)   { Write-Host $text -ForegroundColor Red }
function Warn($text)  { Write-Host $text -ForegroundColor Yellow }

function Step($n, $text) {
    Write-Host ''
    Write-Host ("=== ШАГ {0}. {1} ===" -f $n, $text) -ForegroundColor Cyan
}

function Pause-Enter($text) {
    if (-not $text) { $text = 'Нажмите Enter, чтобы продолжить' }
    [void](Read-Host ("  >> " + $text))
}

# Вопрос с вариантами. Возвращает номер выбранного варианта.
function Ask($question, [string[]]$options) {
    Write-Host ''
    Write-Host $question -ForegroundColor White
    for ($i = 0; $i -lt $options.Count; $i++) {
        Write-Host ("   {0} — {1}" -f ($i + 1), $options[$i])
    }
    while ($true) {
        $a = (Read-Host '  >> Введите номер и нажмите Enter').Trim()
        $n = 0
        if ([int]::TryParse($a, [ref]$n) -and $n -ge 1 -and $n -le $options.Count) {
            return $n
        }
        Warn ("  Нужно ввести число от 1 до {0}." -f $options.Count)
    }
}

function AskYesNo($question) {
    return ((Ask $question @('да', 'нет')) -eq 1)
}

# ------------------------------------------------------------------ #
#  Поиск платы                                                         #
# ------------------------------------------------------------------ #

# Все COM-порты, похожие на нашу плату. Для каждого: номер порта и
# признак «плата сейчас в загрузчике».
#   2341:8036 / 2341:0036  Arduino Leonardo, программа / загрузчик
#   1B4F:9206 / 1B4F:9205  SparkFun Pro Micro 5 В
#   1B4F:9204 / 1B4F:9203  SparkFun Pro Micro 3,3 В
function Get-Boards {
    $devs = $null
    try   { $devs = Get-WmiObject Win32_PnPEntity -ErrorAction Stop }
    catch { try { $devs = Get-CimInstance Win32_PnPEntity } catch { } }

    $result = @()
    foreach ($d in $devs) {
        $name = [string]$d.Name
        $id   = [string]$d.PNPDeviceID
        if ($name -notmatch '\((COM\d+)\)') { continue }
        $com = $Matches[1]
        if ($id -notmatch 'VID_(2341|1B4F|2A03)') { continue }
        $boot = ($id -match 'PID_(0036|9205|9203)')
        $result += New-Object psobject -Property @{ Com = $com; Boot = $boot; Name = $name }
    }
    return $result
}

# Устройства нашей платы или переходника FT232RL, которые Windows видит,
# но не может запустить: чаще всего это значит, что нет драйвера.
function Get-DevicesWithoutDriver {
    $devs = $null
    try   { $devs = Get-WmiObject Win32_PnPEntity -ErrorAction Stop }
    catch { try { $devs = Get-CimInstance Win32_PnPEntity } catch { } }
    return @($devs | Where-Object {
        ([string]$_.PNPDeviceID -match 'VID_(2341|1B4F|2A03|0403)') -and
        ($_.ConfigManagerErrorCode -ne 0)
    })
}

# Установка драйверов из release\windows\drivers. Нужна на Windows 7 и 8.1
# для платы и на любой Windows без интернета — для переходника FT232RL.
# Windows попросит разрешения администратора.
function Install-Drivers {
    $drv = Join-Path $PSScriptRoot 'drivers'
    if ([Environment]::Is64BitOperatingSystem) { $dp = Join-Path $drv 'dpinst-amd64.exe' }
    else                                        { $dp = Join-Path $drv 'dpinst-x86.exe' }
    Say '  Сейчас Windows спросит разрешения на установку драйверов — ответьте «Да».'
    Say '  В окне установки нажимайте «Далее» и «Готово».'
    foreach ($p in @($drv, (Join-Path $drv 'ftdi'))) {
        try {
            $proc = Start-Process -FilePath $dp -ArgumentList @('/PATH', ('"' + $p + '"'), '/SE') `
                                  -Verb RunAs -Wait -PassThru
        } catch {
            Bad '  Установка отменена или запрещена. Нужны права администратора —'
            Bad '  попросите руководителя или учителя информатики.'
            return
        }
    }
    Good '  Драйверы установлены. Отключите плату и подключите её снова.'
}

# Дождаться подключения платы и выбрать порт. Возвращает имя порта.
function Find-Board {
    while ($true) {
        $boards = @(Get-Boards | Where-Object { -not $_.Boot })
        if ($boards.Count -eq 1) {
            Good ("  Плата найдена: {0}" -f $boards[0].Com)
            return $boards[0].Com
        }
        if ($boards.Count -gt 1) {
            $opts = @($boards | ForEach-Object { $_.Name })
            $n = Ask 'Подключено несколько плат. Какую прошивать?' $opts
            return $boards[$n - 1].Com
        }
        Bad '  Плата не найдена.'
        if ((Get-DevicesWithoutDriver).Count -gt 0) {
            Warn '  Windows видит плату, но для неё нет драйвера.'
            if (AskYesNo 'Установить драйвер сейчас?') { Install-Drivers }
            Pause-Enter 'Нажмите Enter, чтобы поискать плату ещё раз'
            continue
        }
        Say '  Проверьте:'
        Say '   - плата подключена кабелем USB к компьютеру;'
        Say '   - кабель не «только для зарядки» — возьмите другой, если есть сомнения;'
        Say '   - на плате горит или мигает светодиод.'
        Pause-Enter 'Подключите плату и нажмите Enter, чтобы поискать ещё раз'
    }
}

# ------------------------------------------------------------------ #
#  Работа с портом                                                     #
# ------------------------------------------------------------------ #

function Reset-ToBootloader($com) {
    try {
        $p = New-Object System.IO.Ports.SerialPort $com, 1200
        $p.Open()
        $p.DtrEnable = $false
        Start-Sleep -Milliseconds 100
        $p.Close()
    } catch { }
}

function Wait-BootPort([string[]]$before, [int]$seconds) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $seconds) {
        $now = @(Get-Boards)
        $b = @($now | Where-Object { $_.Boot })
        if ($b.Count -gt 0) { return $b[0].Com }
        $new = @($now | Where-Object { $before -notcontains $_.Com })
        if ($new.Count -gt 0) { return $new[0].Com }
        Start-Sleep -Milliseconds 200
    }
    return $null
}

# После заливки плата перезапускается и возвращается как обычный порт.
function Wait-AppPort($prefer, [int]$seconds) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $seconds) {
        $apps = @(Get-Boards | Where-Object { -not $_.Boot })
        if (@($apps | Where-Object { $_.Com -eq $prefer }).Count -gt 0) { return $prefer }
        if ($apps.Count -eq 1) { return $apps[0].Com }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

# Слушать порт платы $seconds секунд. $keys — что и когда отправить:
# массив пар @(секунда, текст). Возвращает все принятые строки.
function Listen($com, [int]$seconds, $keys, [switch]$Echo) {
    $lines = New-Object System.Collections.ArrayList
    $p = New-Object System.IO.Ports.SerialPort $com, 115200
    # Без DTR плата на ATmega32U4 считает, что порт никто не открыл,
    # и ничего не отправляет.
    $p.DtrEnable = $true
    $p.RtsEnable = $true
    $p.Encoding = [Text.Encoding]::UTF8
    try { $p.Open() } catch {
        Bad ("  Не удалось открыть порт {0}. Закройте другие программы, которые" -f $com)
        Bad '  могут его занимать (Arduino IDE, монитор порта), и повторите.'
        return $null
    }
    $buf = ''
    $ki = 0
    $nk = 0
    if ($keys) { $nk = $keys.Count }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        while ($sw.Elapsed.TotalSeconds -lt $seconds) {
            while ($ki -lt $nk -and $sw.Elapsed.TotalSeconds -ge $keys[$ki][0]) {
                $p.Write([string]$keys[$ki][1])
                $ki++
            }
            $chunk = ''
            try { $chunk = $p.ReadExisting() } catch { }
            if (-not $chunk) { Start-Sleep -Milliseconds 50; continue }
            $buf += $chunk
            while (($i = $buf.IndexOf("`n")) -ge 0) {
                $l = $buf.Substring(0, $i).TrimEnd("`r")
                $buf = $buf.Substring($i + 1)
                [void]$lines.Add($l)
                if ($Echo) { Write-Host ("     | " + $l) -ForegroundColor DarkGray }
            }
        }
    } finally {
        try { $p.Close() } catch { }
    }
    return ,$lines
}

# ------------------------------------------------------------------ #
#  Заливка                                                             #
# ------------------------------------------------------------------ #

# Залить файл из release\bin. Возвращает порт платы после перезапуска
# или $null, если не вышло.
function Write-Firmware($com, $hexName) {
    $hexPath = Join-Path $BinDir $hexName
    if (-not (Test-Path $hexPath)) { Bad "  Нет файла прошивки: $hexPath"; return $null }
    if (-not (Test-Path $Avrdude)) { Bad "  Нет программы прошивки: $Avrdude"; return $null }

    $before = @(Get-Boards | ForEach-Object { $_.Com })
    Say '  Перевожу плату в режим прошивки...'
    Reset-ToBootloader $com
    $boot = Wait-BootPort $before 10
    if (-not $boot) {
        Warn '  Плата не перешла в режим прошивки сама. Попробую как есть.'
        $boot = $com
    }
    Say ("  Записываю прошивку через {0}. Не отключайте кабель!" -f $boot)

    Push-Location $BinDir
    try {
        $out = & $Avrdude -C $AvrConf -p atmega32u4 -c avr109 -P $boot -b 57600 -D `
                          -U ("flash:w:{0}:i" -f $hexName) 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $out | ForEach-Object { [string]$_ } | Out-File -FilePath $LogFile -Encoding utf8

    if ($code -ne 0) {
        Bad '  Прошивка НЕ записалась.'
        Say ("  Подробности сохранены в файле: {0}" -f $LogFile)
        Say '  Что попробовать: отключить и снова подключить плату, запустить мастер ещё раз.'
        Say '  Не помогает — покажите руководителю файл с подробностями.'
        return $null
    }
    Good '  Прошивка записана.'

    Say '  Жду, пока плата перезапустится...'
    $app = Wait-AppPort $com 15
    if (-not $app) {
        Warn '  Плата пока не появилась снова. Отключите и подключите кабель.'
        $app = Find-Board
    }
    return $app
}

# ------------------------------------------------------------------ #
#  Проверка платы по её сообщениям                                     #
# ------------------------------------------------------------------ #

function Explain-Led {
    Say '  Посмотрите на светодиод платы:'
    Say '   - мигает медленно, раз в 2 секунды    — всё хорошо, плата готова к старту;'
    Say '   - мигает раз в секунду                — плата ждёт, пока её положат и перестанут'
    Say '                                           трогать; через 10 секунд покоя будет готова;'
    Say '   - мигает очень часто, 5 раз в секунду — ошибка, чаще всего не отвечает датчик.'
}

# Прочитать плату и сказать, всё ли в порядке. $expectTalk — ждём ли
# сообщений (у наборов B и D вывода по USB нет).
function Check-Board($com, $expectTalk) {
    if (-not $expectTalk) {
        Say '  Эта прошивка не пишет сообщений в компьютер — так и задумано,'
        Say '  чтобы сберечь память. Проверяем по светодиоду.'
        Explain-Led
        return
    }
    Say '  Слушаю плату 15 секунд. Плату не трогайте и не двигайте.'
    $lines = Listen $com 15 $null
    if ($null -eq $lines) { return }

    $ready = @($lines | Where-Object { $_ -match ' READY ' })
    $bridge = @($lines | Where-Object { $_ -match 'мост USB|HC-12' })
    if ($ready.Count -gt 0) {
        $last = $ready[$ready.Count - 1]
        Say ("  Плата сообщает: {0}" -f $last)
        if ($last -match 'err=0x0\b' -and $last -match 'z=0') {
            Warn '  Плата работает, но ещё не запомнила высоту площадки.'
            Say  '  Положите плату (или ракету) и не трогайте 10 секунд. Когда светодиод'
            Say  '  станет мигать медленно, раз в 2 секунды, — плата готова.'
        } elseif ($last -match 'err=0x0\b') {
            Good '  ВСЁ ХОРОШО: плата готова к старту, ошибок нет.'
        } else {
            Bad  '  Плата работает, но сообщает об ошибке (err не равен 0x0).'
            if ($last -match 'err=0x([0-9A-Fa-f]+)') {
                $f = [Convert]::ToInt32($Matches[1], 16)
                if ($f -band 0x0001) { Say '   - датчики не запустились' }
                if ($f -band 0x0002) { Say '   - не отвечает барометр (BMP280)' }
                if ($f -band 0x0004) { Say '   - не отвечает датчик движения (ICM-20948)' }
                if ($f -band 0x0008) { Say '   - не найдена карта MicroSD' }
                if ($f -band 0x0010) { Say '   - ошибка записи на карту MicroSD' }
                if ($f -band 0x0040) { Say '   - ошибка сервопривода' }
                if ($f -band 0x0100) { Say '   - высота площадки не запомнилась: плату долго двигали после включения.' ; Say '     Положите её и не трогайте 10 секунд — ошибка пропадёт сама.' }
            }
            Say  '  Отключите плату, проверьте, что модули вставлены до конца, и повторите.'
            Say  '  Не помогло — покажите это сообщение руководителю.'
        }
    } elseif ($bridge.Count -gt 0) {
        Warn '  На плате сейчас программа настройки радио, а не полётная прошивка.'
        Say  '  Выберите в меню пункт 1 — «Прошить плату для полёта».'
    } elseif ($lines.Count -gt 0) {
        Say '  Плата что-то сообщает, но состояния READY среди сообщений нет:'
        $lines | Select-Object -Last 5 | ForEach-Object { Say ("     | " + $_) }
        Say '  Если плата уже «слетала» (профиль r) — отключите и снова подключите её.'
    } else {
        Warn '  Плата молчит.'
        Say  '  Если на ней прошивка B или D — это нормально, у них нет сообщений.'
        Explain-Led
    }
}

# ------------------------------------------------------------------ #
#  Радиомодуль HC-12                                                   #
# ------------------------------------------------------------------ #

$PowerDbm = @{ 1 = '-1'; 2 = '+2'; 3 = '+5'; 4 = '+8'; 5 = '+11'; 6 = '+14'; 7 = '+17'; 8 = '+20' }

# Залить программу настройки, поставить мощность и проверить.
# Возвращает порт платы или $null.
function Setup-Radio($com, [int]$level) {
    Say '  Временно загружаю в плату программу настройки радиомодуля.'
    $app = Write-Firmware $com 'vro1_hc12_setup.hex'
    if (-not $app) { return $null }

    Say ("  Ставлю мощность P{0} ({1} dBm) и проверяю модуль..." -f $level, $PowerDbm[$level])
    # Программа настройки сама опрашивает модуль через 4 с после старта.
    # Даём ей закончить, потом ставим мощность и просим показать параметры.
    $lines = Listen $app 11 @(@(6, [string]$level), @(8, '~'))
    if ($null -eq $lines) { return $null }
    $text = ($lines -join "`n")

    if ($text -notmatch 'OK') {
        Bad '  Радиомодуль НЕ отвечает.'
        Say '  Проверьте: модуль HC-12 вставлен в плату до конца и правильной стороной,'
        Say '  не погнуты ножки. Отключите плату, поправьте модуль и повторите.'
        return $null
    }
    if ($text -match 'OK\+RP:([+-]?\d+)dBm') {
        $got = [int]$Matches[1]
        $want = [int]$PowerDbm[$level]
        if ($got -eq $want) {
            Good ("  Радиомодуль отвечает, мощность {0} dBm — как нужно." -f $Matches[1])
        } else {
            Bad ("  Мощность {0} dBm, а нужна {1} dBm. Повторите настройку." -f $Matches[1], $PowerDbm[$level])
            return $null
        }
    }
    if ($text -match 'OK\+B(\d+)')  { Say ("  Скорость: {0}" -f $Matches[1]) }
    if ($text -match 'OK\+RC(\d+)') { Say ("  Канал:    {0}" -f $Matches[1]) }
    if ($text -match 'OK\+B(\d+)' -and $Matches[1] -ne '9600') {
        Warn '  Скорость модуля не 9600 — полётная прошивка с ним не заговорит.'
        Warn '  Покажите это руководителю (docs/RADIO.md, раздел 6).'
    }
    return $app
}

function Ask-RadioPower {
    $n = Ask 'Для чего готовим радиомодуль?' @(
        'к ПОЛЁТУ — полная мощность (обязательно перед запуском ракеты)',
        'к проверке НА СТОЛЕ — малая мощность, когда два модуля лежат рядом')
    if ($n -eq 1) { return 8 } else { return 3 }
}

# ------------------------------------------------------------------ #
#  Сценарии                                                            #
# ------------------------------------------------------------------ #

function Flow-Flight {
    Step 1 'Подключите плату'
    Say '  Подключите плату к компьютеру кабелем USB.'
    Pause-Enter
    $com = Find-Board

    Step 2 'Что стоит на плате'
    Say '  Посмотрите на плату и ответьте на два вопроса.'
    $sd    = AskYesNo 'Вставлен ли модуль карты памяти MicroSD?'
    $radio = AskYesNo 'Вставлен ли радиомодуль HC-12 (маленькая плата с антенной-пружинкой)?'

    if     ($sd -and $radio) { $set = 'd_full' }
    elseif ($sd)             { $set = 'b_sd' }
    elseif ($radio)          { $set = 'c_radio' }
    else                     { $set = 'a_basic' }
    Say ''
    Good ("  Подходит прошивка {0}" -f $SetNames[$set])

    $level = 0
    if ($radio) { $level = Ask-RadioPower }

    Step 3 'Прошивка'
    Say '  Сейчас плата будет прошита. Это займёт около минуты.'
    Say '  Пока идёт прошивка, НЕ отключайте кабель.'
    Pause-Enter 'Нажмите Enter, чтобы начать'

    if ($radio) {
        $com = Setup-Radio $com $level
        if (-not $com) { return }
        Say ''
        Say '  Теперь записываю полётную прошивку.'
    }
    $com = Write-Firmware $com ("vro1_{0}.hex" -f $set)
    if (-not $com) { return }

    Step 4 'Проверка'
    Start-Sleep -Seconds 2
    Check-Board $com ($set -eq 'a_basic' -or $set -eq 'c_radio')

    Say ''
    Good '  ГОТОВО. Плату можно отключать.'
    if ($radio -and $level -ne 8) {
        Warn '  ВНИМАНИЕ: радио на малой мощности. Перед полётом запустите мастер'
        Warn '  ещё раз и выберите «к ПОЛЁТУ».'
    }
}

function Flow-Radio {
    Step 1 'Подключите плату'
    Say '  Подключите к компьютеру плату, на которой стоит радиомодуль HC-12.'
    Pause-Enter
    $com = Find-Board
    $level = Ask-RadioPower

    Step 2 'Настройка'
    Pause-Enter 'Нажмите Enter, чтобы начать. Кабель не отключайте'
    $com = Setup-Radio $com $level
    if (-not $com) { return }

    Step 3 'Вернуть полётную прошивку'
    Warn '  Сейчас в плате программа настройки, на ней ракета не полетит.'
    $sd = AskYesNo 'Вставлен ли в эту плату модуль карты памяти MicroSD?'
    if ($sd) { $set = 'd_full' } else { $set = 'c_radio' }
    Say ("  Записываю прошивку {0}" -f $SetNames[$set])
    $com = Write-Firmware $com ("vro1_{0}.hex" -f $set)
    if (-not $com) { return }
    Start-Sleep -Seconds 2
    Check-Board $com ($set -eq 'c_radio')
    Say ''
    Good '  ГОТОВО.'
}

function Flow-Check {
    Step 1 'Подключите плату'
    Pause-Enter
    $com = Find-Board
    Step 2 'Проверка'
    Check-Board $com $true
}

# ------------------------------------------------------------------ #
#  Главное меню                                                        #
# ------------------------------------------------------------------ #

Clear-Host
Write-Host ''
Write-Host '  ВРО-1 — мастер прошивки платы водяной ракеты' -ForegroundColor Cyan
Write-Host '  ------------------------------------------------'
Write-Host '  Мастер задаст несколько вопросов и всё сделает сам.'
Write-Host '  Интернет не нужен, устанавливать ничего не нужно.'

while ($true) {
    $n = Ask 'Что нужно сделать?' @(
        'Прошить плату для полёта',
        'Настроить мощность радиомодуля HC-12',
        'Проверить, что плата работает',
        'Установить драйверы (нужно только на Windows 7 и 8 или для переходника FT232RL)',
        'Выйти')
    switch ($n) {
        1 { Flow-Flight }
        2 { Flow-Radio }
        3 { Flow-Check }
        4 { Install-Drivers }
        5 { exit 0 }
    }
    Pause-Enter 'Нажмите Enter, чтобы вернуться в меню'
}
