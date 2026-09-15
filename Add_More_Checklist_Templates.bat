@echo off
setlocal
cd /d "%~dp0"

if not exist "requirements.txt" (
    echo.
    echo This app's files aren't fully unpacked yet - please right-click the
    echo .zip and choose "Extract All..." first, then run this from inside
    echo the extracted folder.
    echo.
    pause
    exit /b 1
)

set VENV_DIR=venv

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo.
    echo Please run Start_Neverlank_App.bat at least once first ^(so the
    echo app's Python environment is set up^), then run this file again.
    echo.
    pause
    exit /b 1
)

echo Adding any new checklist templates and Document Templates to your
echo existing app data. Your clients, engagements, users, and any templates
echo you've already customised are not touched.
echo.
"%VENV_DIR%\Scripts\python.exe" -m flask --app app seed

echo.
echo ============================================================
echo   Done! Open the app:
echo     - "Checklist Templates" in the top menu for audit programs
echo     - "Document Templates" in the top menu for the Word/Excel
echo       workpaper library
echo   Any new ones should now be listed alongside your existing ones.
echo ============================================================
echo.
pause
