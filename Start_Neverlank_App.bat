@echo off
setlocal
cd /d "%~dp0"

if not exist "requirements.txt" (
    echo.
    echo ============================================================
    echo   This app's files aren't fully unpacked yet.
    echo.
    echo   It looks like this .bat file was run directly from inside
    echo   the .zip ^(Windows lets you preview zips without actually
    echo   extracting them, which breaks this script^).
    echo.
    echo   To fix it:
    echo     1. Close this window.
    echo     2. Right-click the downloaded .zip file and choose
    echo        "Extract All..."
    echo     3. Open the FOLDER that gets created ^(not the .zip^).
    echo     4. Double-click Start_Neverlank_App.bat from inside
    echo        that extracted folder.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

set VENV_DIR=venv
set PY=

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set PY=py -3
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set PY=python
    )
)

if "%PY%"=="" (
    echo.
    echo ============================================================
    echo   Python was not found on this computer.
    echo.
    echo   This app needs Python installed once ^(it's free^):
    echo     1. Go to https://www.python.org/downloads/
    echo     2. Download and run the Windows installer
    echo     3. IMPORTANT: tick "Add python.exe to PATH" before
    echo        clicking Install
    echo     4. Once installed, double-click this file again
    echo ============================================================
    echo.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Setting up the app for the first time - this only happens once.
    echo Please wait, this can take a minute...
    %PY% -m venv "%VENV_DIR%"
)

"%VENV_DIR%\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo Could not reach the internet to download packages - trying the
    echo offline copies included in this download instead...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --no-index --find-links=wheelhouse -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Setup failed - see the messages above for details.
        echo ^(This can happen if your Python version doesn't match the
        echo offline packages included here - connecting this computer
        echo to the internet for the first setup will fix it.^)
        pause
        exit /b 1
    )
)

echo Starting the Neverlank Audit App...
echo Your browser will open automatically in a moment.
echo.
"%VENV_DIR%\Scripts\python.exe" app.py

pause
