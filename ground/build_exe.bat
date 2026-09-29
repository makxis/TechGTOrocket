@echo off
chcp 65001 >nul
rem Сборка VRO1-Station.exe из vro_station.py. Запускать на Windows.
rem Нужен Python 3 (с галочкой Add python.exe to PATH) и интернет для pip.
rem Готовый файл появится в ground\dist\VRO1-Station.exe. Журналы CSV он
rem пишет в папку logs рядом с собой.
cd /d "%~dp0"

where py >nul 2>nul && ( set PY=py -3 ) || ( set PY=python )

%PY% -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo Не удалось поставить pyinstaller.
    pause
    exit /b 1
)

%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name VRO1-Station ^
    --paths vendor ^
    --hidden-import serial.tools.list_ports_windows ^
    vro_station.py
if errorlevel 1 (
    echo Сборка не удалась.
    pause
    exit /b 1
)

echo.
echo Готово: %~dp0dist\VRO1-Station.exe
pause
