@echo off
setlocal EnableDelayedExpansion
REM ============================================================
REM   Tom B video bulk downloader for TomTube  (resilient loop)
REM ============================================================
REM   Keeps re-invoking yt-dlp after any error/exit. Each pass
REM   skips files that are already fully downloaded, so the loop
REM   converges safely. Stops automatically when the .webm count
REM   reaches the URL-list count, or on Ctrl+C.
REM
REM   --sleep-interval / --max-sleep-interval add a 20-50 second
REM   random pause between videos so YouTube's rate-limiter
REM   doesn't flag the session. Without this, a full-speed run
REM   hits the bot wall ~10-15 min in and every further request
REM   gets blocked for an hour. Slower but reliable.
REM ============================================================

set TARGET_DIR=D:\Tom Videos
set URL_LIST=D:\Toms Lab\tom_video_urls_fresh.txt
set BGUTIL=C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

set TOTAL=0
for /f %%i in ('type "%URL_LIST%" ^| find /v /c ""') do set TOTAL=%%i
echo Target: %TOTAL% videos in %TARGET_DIR%
echo.

set /a ATTEMPT=0
:retry_loop
set /a ATTEMPT+=1
echo ==========================================================
echo   Pass #%ATTEMPT%  -  starting yt-dlp
echo ==========================================================

"D:\Toms Lab\.venv\Scripts\python.exe" -m yt_dlp --cookies-from-browser firefox --js-runtimes node --extractor-args "youtubepot-bgutilscript:script_path=%BGUTIL%" --format "bestaudio[ext=webm]/bestaudio/best" --no-overwrites --continue --ignore-errors --no-warnings --sleep-interval 20 --max-sleep-interval 50 --sleep-requests 1 -o "%TARGET_DIR%\%%(title)s [%%(id)s].%%(ext)s" -a "%URL_LIST%"

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

echo.
echo Not finished yet. If YouTube just rate-limited you, that
echo normally clears in 30-60 minutes. Waiting 5 minutes before
echo the next pass so the rate-limiter cools down.
echo (Ctrl+C in this window to stop and resume later.)
timeout /t 300 /nobreak
goto retry_loop
