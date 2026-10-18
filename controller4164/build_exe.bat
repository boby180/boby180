@echo off
rem Builds dist\Controller4164-v<version>.exe (one file, no console window).
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    python -m venv .venv || (echo Python not found. Install it from python.org & pause & exit /b 1)
)
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt pyinstaller
for /f %%v in ('"%PY%" -c "from ctrl4164.version import __version__; print(__version__)"') do set "VER=%%v"
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "Controller4164-v%VER%" run.py
echo.
echo Done: dist\Controller4164-v%VER%.exe
pause
