@echo off
REM Builds dist\FC27TacticalStudio\FC27TacticalStudio.exe
REM Requirements: Windows 10/11 x64 and Python 3.11 or 3.12 (64-bit) from python.org
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

if not exist .venv (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

echo Installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-dev.txt || goto :error

echo Downloading the AI player-selection model...
python scripts\fetch_models.py || goto :error

echo Running tests...
set QT_QPA_PLATFORM=offscreen
python -m pytest -q tests || goto :error
set QT_QPA_PLATFORM=

echo Building executable...
pyinstaller --noconfirm --clean FC27TacticalStudio.spec || goto :error

echo Verifying the build...
dist\FC27TacticalStudio\FC27TacticalStudio.exe --selfcheck --selfcheck-log "%TEMP%\fc27_selfcheck.txt"
type "%TEMP%\fc27_selfcheck.txt"
findstr /b "exit=0" "%TEMP%\fc27_selfcheck.txt" >nul || goto :error

echo.
echo Done:  dist\FC27TacticalStudio\FC27TacticalStudio.exe
echo Copy the whole dist\FC27TacticalStudio folder to share or install the app.
exit /b 0

:error
echo.
echo BUILD FAILED. See the messages above.
exit /b 1
