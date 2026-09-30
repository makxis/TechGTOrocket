@echo off
chcp 65001 >nul
rem Сборка VRO1-Station.exe из vro_station.py. Запускать на Windows.
rem Нужен Python 3 (с галочкой Add python.exe to PATH) и интернет для pip.
rem Готовый файл появится в ground\dist\VRO1-Station.exe. Журналы CSV он
rem пишет в папку logs рядом с собой. Внутрь кладутся готовые прошивки и
rem avrdude, чтобы прошивать плату из программы без установки чего-либо.
cd /d "%~dp0"
rem Параметр nopause: не ждать нажатия клавиши (так его зовёт "СОБРАТЬ EXE.bat").
set NOPAUSE=
if /i "%~1"=="nopause" set NOPAUSE=1

where py >nul 2>nul && ( set PY=py -3 ) || ( set PY=python )

rem Откуда брать прошивки и avrdude: из архива (рядом) или из проекта.
if exist "firmware" ( set FW=firmware ) else ( set FW=..\release\bin )
if exist "avrdude" ( set AV=avrdude ) else ( set AV=..\release\avrdude )

if not exist "%FW%\vro1_c_radio.hex" (
    echo Не найдены готовые прошивки в %FW%
    if not defined NOPAUSE pause
    exit /b 1
)
if not exist "%AV%\windows\bin\avrdude.exe" (
    echo Не найден avrdude в %AV%
    if not defined NOPAUSE pause
    exit /b 1
)

%PY% -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo Не удалось поставить pyinstaller.
    if not defined NOPAUSE pause
    exit /b 1
)

%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name VRO1-Station ^
    --paths vendor ^
    --hidden-import serial.tools.list_ports_windows ^
    --add-data "%FW%;firmware" ^
    --add-data "%AV%;avrdude" ^
    vro_station.py
if errorlevel 1 (
    echo Сборка не удалась.
    if not defined NOPAUSE pause
    exit /b 1
)

echo.
echo Готово: %~dp0dist\VRO1-Station.exe
if not defined NOPAUSE pause
