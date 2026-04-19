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
set SHUFFLED_LIST=D:\Tom Videos\_urls_shuffled.txt
set BGUTIL=C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js
REM Persistent ledger of finished videos (yt-dlp skips anything
REM listed here BEFORE touching the network — cuts resume-pass
REM YouTube hits from ~450 to zero on already-done items).
set ARCHIVE=D:\Tom Videos\_downloaded_ids.txt
set PYEXE="D:\Toms Lab\.venv\Scripts\python.exe"

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

REM Start the browser-session keepalive in the background. Pings
REM youtube.com via yt-dlp+Firefox cookies every 10-20 min (random) so
REM the session doesn't age out mid-run and force manual re-sign-in.
REM Inherits the parent window; dies when the user Ctrl+C's the batch.
echo Starting YouTube session keepalive in background...
start /B "" %PYEXE% -m tomslab.ingest.youtube_keepalive

set TOTAL=0
for /f %%i in ('type "%URL_LIST%" ^| find /v /c ""') do set TOTAL=%%i
echo Target: %TOTAL% videos in %TARGET_DIR%
echo.

set /a ATTEMPT=0
:retry_loop
set /a ATTEMPT+=1
echo ==========================================================
echo   Pass #%ATTEMPT%  -  shuffling URL order, starting yt-dlp
echo ==========================================================

REM Shuffle the URL list so YouTube doesn't see the same sequential
REM walk every pass. Fresh order = no 'this bot always hits videos in
REM the same order' fingerprint.
%PYEXE% -c "import random; lines = open(r'%URL_LIST%', encoding='utf-8').readlines(); random.shuffle(lines); open(r'%SHUFFLED_LIST%', 'w', encoding='utf-8').writelines(lines)"

%PYEXE% -m yt_dlp --cookies-from-browser firefox --js-runtimes node --extractor-args "youtubepot-bgutilscript:script_path=%BGUTIL%" --format "bestaudio[ext=webm]/bestaudio/best" --no-overwrites --continue --ignore-errors --no-warnings --sleep-interval 30 --max-sleep-interval 180 --sleep-requests 2 --download-archive "%ARCHIVE%" -o "%TARGET_DIR%\%%(title)s [%%(id)s].%%(ext)s" -a "%SHUFFLED_LIST%"

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
echo Not finished yet. Taking a randomised rest (3-15 min) before
echo the next pass — variable rests look more like a person walking
echo away from their keyboard than a bot with a fixed schedule.
echo (Ctrl+C in this window to stop and resume later.)
for /f %%r in ('%PYEXE% -c "import random; print(random.randint(180, 900))"') do set REST=%%r
echo Resting %REST% seconds...
timeout /t %REST% /nobreak
goto retry_loop
