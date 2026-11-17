@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [CanliAltyazi / LiveSub] Sanal ortam hazirlaniyor...
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)

if errorlevel 1 (
    echo [HATA] Python 3.10 - 3.12 bulunamadi. Lutfen python.org uzerinden Python kurun ve PATH'e ekleyin.
    pause
    exit /b 1
)

echo [CanliAltyazi / LiveSub] Bagimliliklar yukleniyor...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

if errorlevel 1 (
    echo [HATA] Paket kurulumu sirasinda sorun olustu.
    pause
    exit /b 1
)

echo.
echo Kurulum tamamlandi!
echo Ollama modelini indirmek icin ornek: ollama pull ali6parmak/hy-mt1.5:1.8b
echo Uygulamayi baslatmak icin run.bat dosyasina cift tiklayin.
pause
