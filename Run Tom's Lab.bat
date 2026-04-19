@echo off
REM Double-click to launch Tom's Lab.
REM Pins runtime data to .\data so everything stays on this drive.

setlocal
cd /d "%~dp0"
set "TOMSLAB_DATA_DIR=%~dp0data"
set "PYTHONUNBUFFERED=1"

echo Starting Tom's Lab...
echo Python: %~dp0.venv\Scripts\python.exe
echo Data:   %TOMSLAB_DATA_DIR%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Tom's Lab isn't installed yet. Run these commands in a terminal first:
    echo.
    echo     py -m venv .venv
    echo     .venv\Scripts\pip install -e .
    echo.
    pause
    exit /b 1
)

REM Use `python -m tomslab` (not tomslab.exe) so any startup error prints
REM directly to this window instead of being swallowed by the console
REM launcher shim. -u flag forces unbuffered stderr so log lines show
REM immediately.
.venv\Scripts\python.exe -u -m tomslab
set RC=%ERRORLEVEL%

echo.
echo ==========================================================
echo Tom's Lab exited with code %RC%.
echo ==========================================================
echo.
echo If the window above is empty the app couldn't start at all —
echo check that python.exe isn't being blocked by antivirus,
echo and that no stale python processes are still running:
echo.
echo     taskkill /F /IM python.exe
echo.
pause
endlocal
