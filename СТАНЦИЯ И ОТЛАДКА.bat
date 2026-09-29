@echo off
chcp 65001 >nul
cd /d "%~dp0ground"
if not exist "vro_station.py" (
    echo.
    echo   Похоже, архив не распакован. Распакуйте его целиком
    echo   ^(правая кнопка мыши - "Извлечь все..."^) и запустите файл оттуда.
    echo.
    pause
    exit /b 1
)
where py >nul 2>nul && ( py -3 vro_station.py & goto :end )
where python >nul 2>nul && ( python vro_station.py & goto :end )
echo.
echo   Для станции и отладки нужен Python 3.
echo   Скачайте его с https://www.python.org/downloads/ и при установке
echo   поставьте галочку "Add python.exe to PATH". Потом запустите этот файл снова.
echo.
pause
:end
