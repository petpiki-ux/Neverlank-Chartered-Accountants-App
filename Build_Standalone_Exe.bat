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
    echo        that extracted folder first, then come back to this
    echo        one if you still want a .exe file.
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
    echo Python was not found. Please run Start_Neverlank_App.bat first -
    echo it will tell you how to install Python, which this script also needs.
    echo.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Setting up a Python environment first, please wait...
    %PY% -m venv "%VENV_DIR%"
)

echo Installing the app's dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo Could not reach the internet - trying the offline copies included
    echo in this download instead...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --no-index --find-links=wheelhouse -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies - see messages above.
        pause
        exit /b 1
    )
)

echo Installing the exe-builder tool ^(PyInstaller^)...
"%VENV_DIR%\Scripts\python.exe" -m pip install --quiet pyinstaller pyinstaller-hooks-contrib
if errorlevel 1 (
    echo Could not reach the internet - trying the offline copies included
    echo in this download instead...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --no-index --find-links=wheelhouse pyinstaller pyinstaller-hooks-contrib
    if errorlevel 1 (
        echo Failed to install PyInstaller - see messages above.
        pause
        exit /b 1
    )
)

echo.
echo Building NeverlankApp.exe - this can take a minute or two, please wait...
"%VENV_DIR%\Scripts\python.exe" -m PyInstaller --onefile --noconfirm --name NeverlankApp ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "document_templates;document_templates" ^
  --hidden-import sqlalchemy.dialects.sqlite ^
  --hidden-import sqlalchemy.dialects.sqlite.pysqlite ^
  app.py

if exist "dist\NeverlankApp.exe" (
    copy /Y "dist\NeverlankApp.exe" "NeverlankApp.exe" >nul
    echo.
    echo ============================================================
    echo   Done! NeverlankApp.exe has been created in this folder.
    echo.
    echo   From now on, just double-click NeverlankApp.exe to run the
    echo   app - no more scripts or setup needed.
    echo.
    echo   You can also copy that single file to other office
    echo   computers - they will NOT need Python installed to run it.
    echo ============================================================
) else (
    echo.
    echo Something went wrong - scroll up to see the error message.
)

pause
