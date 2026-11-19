@echo off
rem Builds dist\AliTracker.exe (one file, no console window).
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
python -m PyInstaller --noconfirm --clean --onefile --windowed --name AliTracker main.py
if errorlevel 1 goto :fail
echo.
echo Done: %~dp0dist\AliTracker.exe
pause
exit /b 0

:fail
echo.
echo The build failed. See the message above.
pause
exit /b 1
