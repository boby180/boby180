@echo off
rem Controller 4164 - double-click to install (first time) and run from source.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Creating .venv and installing packages...
    python -m venv .venv || (echo Python not found. Install it from python.org & pause & exit /b 1)
    "%PY%" -m pip install --upgrade pip
    "%PY%" -m pip install -r requirements.txt
)
"%PY%" run.py
