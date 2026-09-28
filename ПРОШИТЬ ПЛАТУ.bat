@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "release\windows\wizard.ps1" (
    echo.
    echo   Похоже, архив не распакован.
    echo   Нажмите на архив правой кнопкой мыши, выберите "Извлечь все...",
    echo   откройте распакованную папку и запустите этот файл оттуда.
    echo.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "release\windows\wizard.ps1"
if errorlevel 1 pause
