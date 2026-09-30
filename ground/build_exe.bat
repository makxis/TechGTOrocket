@echo off
chcp 65001 >nul
rem Сборка VRO1-Station.exe из vro_station.py. Запускать на Windows.
rem Нужен Python 3 (с галочкой Add python.exe to PATH) и интернет для pip.
rem Готовый файл появится в ground\dist\VRO1-Station.exe. Журналы CSV он
rem пишет в папку logs рядом с собой. Внутрь кладутся готовые прошивки и
rem avrdude, чтобы прошивать плату из программы без установки чего-либо.
rem Если их рядом нет, exe всё равно собирается (режим "Прошивка" тогда
rem работает только со своим файлом .hex).
rem Параметр nopause: не ждать нажатия клавиши (так его зовёт "СОБРАТЬ EXE.bat").
cd /d "%~dp0"
set NOPAUSE=
if /i "%~1"=="nopause" set NOPAUSE=1

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

rem Прошивки и avrdude: из архива (рядом с этим файлом) или из проекта (..\release).
set "FW="
if exist "%~dp0firmware\vro1_c_radio.hex" set "FW=%~dp0firmware"
if not defined FW if exist "%~dp0..\release\bin\vro1_c_radio.hex" set "FW=%~dp0..\release\bin"
set "AV="
if exist "%~dp0avrdude\windows\bin\avrdude.exe" set "AV=%~dp0avrdude"
if not defined AV if exist "%~dp0..\release\avrdude\windows\bin\avrdude.exe" set "AV=%~dp0..\release\avrdude"

set "ADD_FW="
set "ADD_AV="
if defined FW set ADD_FW=--add-data "%FW%;firmware"
if defined AV set ADD_AV=--add-data "%AV%;avrdude"
if not defined FW echo.
if not defined FW echo   ВНИМАНИЕ: готовые прошивки не найдены, собираю без них.
if not defined FW echo   Искал: %~dp0firmware  и  %~dp0..\release\bin
if not defined AV echo   ВНИМАНИЕ: avrdude не найден, собираю без него. Прошивка из программы работать не будет.
if not defined AV echo   Искал: %~dp0avrdude  и  %~dp0..\release\avrdude
if defined FW echo   Прошивки: %FW%
if defined AV echo   avrdude:  %AV%
echo.

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
    %ADD_FW% ^
    %ADD_AV% ^
    vro_station.py
if errorlevel 1 (
    echo Сборка не удалась.
    if not defined NOPAUSE pause
    exit /b 1
)

echo.
echo Готово: %~dp0dist\VRO1-Station.exe
if not defined NOPAUSE pause
