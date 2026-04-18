@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM   Tom B video bulk downloader for TomTube  (resilient loop)
REM ============================================================
REM   Keeps re-invoking yt-dlp after any error/exit. Each pass
REM   skips files that are already fully downloaded, so the loop
REM   converges safely. Stops automatically when the .webm count
REM   reaches the URL-list count, or on Ctrl+C.
REM ============================================================

set TARGET_DIR=D:\Tom Videos
set URL_LIST=D:\Toms Lab\tom_video_urls_fresh.txt
set BGUTIL=C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

REM Count total URLs so we know when we're done.
set TOTAL=0
for /f %%i in ('type "%URL_LIST%" ^| find /v /c ""') do set TOTAL=%%i
echo Target: %TOTAL% videos in %TARGET_DIR%
echo.

set /a ATTEMPT=0
:retry_loop
set /a ATTEMPT+=1
echo ==========================================================
echo   Pass #%ATTEMPT%  —  starting yt-dlp
echo ==========================================================

"D:\Toms Lab\.venv\Scripts\python.exe" -m yt_dlp --cookies-from-browser firefox --js-runtimes node --extractor-args "youtubepot-bgutilscript:script_path=%BGUTIL%" --format "bestaudio[ext=webm]/bestaudio/best" --no-overwrites --continue --ignore-errors --no-warnings -o "%TARGET_DIR%\%%(title)s [%%(id)s].%%(ext)s" -a "%URL_LIST%"

REM Count .webm files currently on disk.
set DONE=0
for /f %%i in ('dir /b /a-d "%TARGET_DIR%\*.webm" 2^>nul ^| find /v /c ""') do set DONE=%%i
echo.
echo Progress: %DONE% of %TOTAL% videos downloaded.

if %DONE% geq %TOTAL% (
    echo.
    echo ==========================================================
    echo   All %TOTAL% videos downloaded. Done.
    echo ==========================================================
    echo.
    echo Next: in Tom's Lab, File -^> Import videos from folder...
    echo and point it at %TARGET_DIR%.
    pause
    exit /b 0
)

echo Not finished yet — waiting 30 seconds, then trying again...
echo (Ctrl+C in this window to stop and resume later.)
timeout /t 30 /nobreak
goto retry_loop
