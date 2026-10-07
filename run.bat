@echo off
rem Photo Sync - double-click this file to install (first time) and run.
rem Note: no "chcp 65001" here - it makes cmd lose track of labels in this file.
setlocal
cd /d "%~dp0"
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
echo   7. Find duplicate photos on the server - report only
echo   8. Enhance underwater photos - copies to a new folder
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
if "%CHOICE%"=="7" goto duplicates
if "%CHOICE%"=="8" goto enhance
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

:duplicates
"%PY%" -m photo_sync duplicates
if errorlevel 1 goto menu
for /f "delims=" %%F in ('dir /b /o-d "reports\duplicates-*.html" 2^>nul') do (
    start "" "reports\%%F"
    goto menu
)
goto menu

:enhance
echo.
echo Drag the folder with the underwater photos into this window, then press Enter.
set "FOLDER="
set /p "FOLDER=Folder: "
if not defined FOLDER goto menu
set "FOLDER=%FOLDER:"=%"
"%PY%" -m photo_sync enhance "%FOLDER%"
if errorlevel 1 goto menu
for /f "delims=" %%F in ('dir /b /o-d "reports\enhance-*.html" 2^>nul') do (
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
call :findpython
if not defined BASEPY goto nopython
echo Using Python: %BASEPY%
%BASEPY% -m venv .venv
if not exist "%PY%" goto venvfailed
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto installfailed
"%PY%" -m pip install -r requirements-optional.txt >nul 2>nul
echo Installation complete.
exit /b 0

:venvfailed
echo.
echo Could not create the Python environment - see the messages above.
exit /b 1

:installfailed
echo.
echo Installation failed - see the messages above, and send a screenshot.
rmdir /s /q .venv
exit /b 1

:findpython
rem Each candidate is actually run, so the Microsoft Store "python" shortcut is skipped.
set "BASEPY="
py -3 -c "import venv" >nul 2>nul && set "BASEPY=py -3"
if defined BASEPY exit /b 0
python -c "import venv" >nul 2>nul && set "BASEPY=python"
if defined BASEPY exit /b 0
python3 -c "import venv" >nul 2>nul && set "BASEPY=python3"
if defined BASEPY exit /b 0
rem Python installed but not on PATH: look in the usual install folders.
rem Anaconda / Miniconda first; a regular python.org install found later wins.
for /d %%D in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "C:\anaconda3" "C:\miniconda3" "%LOCALAPPDATA%\Programs\Python\Python3*" "%LOCALAPPDATA%\Python\pythoncore-3*" "%ProgramFiles%\Python3*" "C:\Python3*") do (
    if exist "%%~D\python.exe" set BASEPY="%%~D\python.exe"
)
exit /b 0

:nopython
echo.
echo Python was not found on this computer.
echo Open "Anaconda Prompt" from the Start menu, then type:
echo     cd /d "%~dp0"
echo     run.bat
exit /b 1

:end
echo.
pause
