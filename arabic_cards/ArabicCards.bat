@echo off
rem Opens the Arabic Cards window. Installs the libraries on first run.
cd /d "%~dp0"
python -c "import PySide6, edge_tts, zstandard" 2>nul
if errorlevel 1 (
    echo Installing libraries, this happens once...
    python -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
)
python main.py
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Something went wrong. See the message above.
pause
exit /b 1
