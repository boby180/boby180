@echo off
rem Photo Sync - double-click this file to install (first time) and run.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Photo Sync

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" call :setup
if not exist "%PY%" goto end

if not exist "config.yaml" (
    copy /y "config.example.yaml" "config.yaml" >nul
    echo.
    echo config.yaml was created. Fill in your folders, save, and close Notepad.
    notepad "config.yaml"
)

:menu
echo.
echo ==================== Photo Sync ====================
echo   1. Check settings
echo   2. Compare computer with server - creates a report
echo   3. Upload missing photos - TRIAL, nothing is copied
echo   4. Upload missing photos - FOR REAL
echo   5. Edit settings - config.yaml
echo   6. Open reports folder
echo   0. Exit
echo ====================================================
set "CHOICE="
set /p "CHOICE=Choose a number: "
if "%CHOICE%"=="1" goto check
if "%CHOICE%"=="2" goto compare
if "%CHOICE%"=="3" goto dryrun
if "%CHOICE%"=="4" goto upload
if "%CHOICE%"=="5" goto edit
if "%CHOICE%"=="6" goto reports
if "%CHOICE%"=="0" goto end
goto menu

:check
"%PY%" -m photo_sync check
goto menu

:compare
"%PY%" -m photo_sync compare
if errorlevel 1 goto menu
for /f "delims=" %%F in ('dir /b /o-d "reports\compare-*.html" 2^>nul') do (
    start "" "reports\%%F"
    goto menu
)
goto menu

:dryrun
"%PY%" -m photo_sync upload
goto menu

:upload
echo.
echo This will COPY the missing photos to the server.
echo Existing files on the server are never overwritten.
set "CONFIRM="
set /p "CONFIRM=Type YES to continue: "
if /i not "%CONFIRM%"=="YES" goto menu
"%PY%" -m photo_sync upload --execute
goto menu

:edit
notepad "config.yaml"
goto menu

:reports
if not exist "reports" mkdir "reports"
start "" "reports"
goto menu

:setup
echo First run: installing, this takes a minute or two...
set "BASEPY="
where py >nul 2>nul && set "BASEPY=py -3"
if not defined BASEPY (
    where python >nul 2>nul && set "BASEPY=python"
)
if not defined BASEPY goto nopython
%BASEPY% -m venv .venv
if not exist "%PY%" goto nopython
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

:nopython
echo.
echo Python is not installed.
echo Install it from the page that opens now, and tick "Add python.exe to PATH".
echo Then double-click run.bat again.
start "" "https://www.python.org/downloads/"
exit /b 1

:end
echo.
pause
