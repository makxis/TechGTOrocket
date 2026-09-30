@echo off
chcp 65001 >nul
cd /d "%~dp0ground"
if not exist "build_exe.bat" (
    echo.
    echo   Похоже, архив не распакован. Распакуйте его целиком
    echo   ^(правая кнопка мыши - "Извлечь все..."^) и запустите файл оттуда.
    echo.
    pause
    exit /b 1
)
echo.
echo   Сборка VRO1-Station.exe. Нужен Python 3 и интернет для pip, около минуты.
echo.
call build_exe.bat nopause
if errorlevel 1 (
    echo.
    echo   Сборка не удалась, причина в сообщениях выше.
    echo.
    pause
    exit /b 1
)
if not exist "dist\VRO1-Station.exe" (
    echo.
    echo   Не найден dist\VRO1-Station.exe, сборка не завершилась.
    echo.
    pause
    exit /b 1
)
echo.
echo   Готово: %~dp0ground\dist\VRO1-Station.exe
echo   Теперь СТАНЦИЯ И ОТЛАДКА.bat запускает именно эту сборку.
echo.
choice /c YN /n /m "  Запустить программу сейчас? [Y - да, N - нет] "
if errorlevel 2 goto :end
start "" "dist\VRO1-Station.exe"
:end
