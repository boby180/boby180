@echo off
rem Builds dist\Tuner4164-v<version>.exe (the tuning assistant).
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    python -m venv .venv || (echo Python not found. Install it from python.org & pause & exit /b 1)
)
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt pyinstaller
for /f %%v in ('"%PY%" -c "from tune4164.version import __version__; print(__version__)"') do set "VER=%%v"
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "Tuner4164-v%VER%" run_tuner.py
echo.
echo Done: dist\Tuner4164-v%VER%.exe
pause
