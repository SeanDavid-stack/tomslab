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
set REMAINING_LIST=D:\Tom Videos\_urls_remaining.txt
set SHUFFLED_LIST=D:\Tom Videos\_urls_shuffled.txt
set BGUTIL=C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js
REM Persistent ledger of finished videos. yt-dlp writes here as it
REM finishes videos; we also pre-filter URL_LIST against it each pass
REM so already-done videos never even appear in the list yt-dlp sees.
REM That gives clean 'X of Y still to download' numbers and zero
REM wasted YouTube hits on completed items.
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
echo   Pass #%ATTEMPT%  -  computing remaining list, shuffling
echo ==========================================================

REM 1. Cross-check: build _urls_remaining.txt = URL_LIST minus any
REM    video ids already recorded in the archive. Also rewrites
REM    _urls_shuffled.txt with the same set in random order for yt-dlp.
%PYEXE% -c "import random, re, os; done = set(); p = r'%ARCHIVE%'; open(p,'a',encoding='utf-8').close(); [done.add(l.split()[1]) for l in open(p,encoding='utf-8') if l.startswith('youtube ')]; lines = [l for l in open(r'%URL_LIST%',encoding='utf-8') if (m:=re.search(r'v=([A-Za-z0-9_-]{11})', l)) and m.group(1) not in done]; open(r'%REMAINING_LIST%','w',encoding='utf-8').writelines(lines); random.shuffle(lines); open(r'%SHUFFLED_LIST%','w',encoding='utf-8').writelines(lines); print(f'Cross-check: {len(done)} done in archive, {len(lines)} still to download.')"

set REMAIN=0
for /f %%i in ('type "%REMAINING_LIST%" 2^>nul ^| find /v /c ""') do set REMAIN=%%i
if %REMAIN% EQU 0 (
    echo.
    echo ==========================================================
    echo   Nothing left to download. Everything in the URL list is
    echo   already recorded in the archive. Done.
    echo ==========================================================
    pause
    exit /b 0
)

%PYEXE% -m yt_dlp --cookies-from-browser firefox --js-runtimes node --extractor-args "youtubepot-bgutilscript:script_path=%BGUTIL%" --format "bestaudio[ext=webm]/bestaudio/best" --no-overwrites --continue --ignore-errors --no-warnings --sleep-interval 30 --max-sleep-interval 180 --sleep-requests 2 --download-archive "%ARCHIVE%" -o "%TARGET_DIR%\%%(title)s [%%(id)s].%%(ext)s" -a "%SHUFFLED_LIST%"

set DONE=0
for /f %%i in ('type "%ARCHIVE%" 2^>nul ^| find /c "youtube "') do set DONE=%%i
set /a STILL=%TOTAL% - %DONE%
echo.
echo Progress: %DONE% of %TOTAL% videos in archive  (%STILL% still to download)

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
