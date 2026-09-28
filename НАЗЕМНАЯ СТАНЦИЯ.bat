@echo off
chcp 65001 >nul
cd /d "%~dp0ground"
if not exist "rocket_ground.py" (
    echo.
    echo   Похоже, архив не распакован. Распакуйте его целиком
    echo   ^(правая кнопка мыши - "Извлечь все..."^) и запустите файл оттуда.
    echo.
    pause
    exit /b 1
)
where py >nul 2>nul && ( py -3 rocket_ground.py & goto :end )
where python >nul 2>nul && ( python rocket_ground.py & goto :end )
echo.
echo   Для наземной станции нужен Python 3.
echo   Скачайте его с https://www.python.org/downloads/ и при установке
echo   поставьте галочку "Add python.exe to PATH". Потом запустите этот файл снова.
echo.
pause
:end
