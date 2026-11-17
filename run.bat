@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [HATA] Sanal ortam bulunamadi. Once setup.bat dosyasini calistirin.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo [HATA] Uygulama bir hata ile kapandi.
    pause
)
