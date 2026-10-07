@echo off
rem Photo Sync - double-click this file to install (first time) and run.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Photo Sync

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" call :setup
echo First run: installing, this takes a minute or two...
call :findpython
if not defined BASEPY goto nopython
echo Using Python: %BASEPY%
%BASEPY% -m venv .venv
if not exist "%PY%" (
    echo.
    echo Could not create the Python environment with %BASEPY% - see the messages above.
    exit /b 1
)
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Installation failed - see the messages above.
    rmdir /s /q .venv
    exit /b 1
)
"%PY%" -m pip install -r requirements-optional.txt >nul 2>nul
echo Installation complete.
exit /b 0

:findpython
rem Actually run each candidate: the Microsoft Store "python" shortcut exists but does not work.
set "BASEPY="
py -3 -c "import venv" >nul 2>nul && set "BASEPY=py -3"
if defined BASEPY exit /b 0
python -c "import venv" >nul 2>nul && set "BASEPY=python"
if defined BASEPY exit /b 0
python3 -c "import venv" >nul 2>nul && set "BASEPY=python3"
if defined BASEPY exit /b 0
rem Python installed but not on PATH: look in the usual install folders, newest last wins.
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%LOCALAPPDATA%\Python\pythoncore-3*" "%ProgramFiles%\Python3*" "C:\Python3*") do (
    if exist "%%~D\python.exe" set BASEPY="%%~D\python.exe"
)
exit /b 0

:nopython
echo.
echo Python was not found on this computer.
echo If it IS installed, open cmd and run:  where python   and  py --version
echo and send the result. Otherwise install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH", then double-click run.bat again.
exit /b 1

:end
echo.
pause
