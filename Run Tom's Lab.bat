@echo off
REM Double-click to launch Tom's Lab.
REM Keeps all runtime data next to this folder (data\, logs\) instead of
REM %LOCALAPPDATA%. Set TOMSLAB_DATA_DIR here to override.

setlocal
cd /d "%~dp0"

REM Pin runtime data to D:\Toms Lab\data so everything stays on this drive.
set "TOMSLAB_DATA_DIR=%~dp0data"

if not exist ".venv\Scripts\tomslab.exe" (
    echo.
    echo Tom's Lab isn't installed yet. Run these two commands in a terminal first:
    echo.
    echo     py -m venv .venv
    echo     .venv\Scripts\pip install -e .
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\tomslab.exe
if errorlevel 1 (
    echo.
    echo Tom's Lab exited with an error. Copy the lines above and send them to me.
    pause
)
endlocal
