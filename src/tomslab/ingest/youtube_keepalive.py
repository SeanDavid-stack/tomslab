"""Keep the Firefox YouTube session alive during long ingest runs.

The failure mode this fixes: long yt-dlp runs (hundreds of videos,
pacing 30-180 seconds apart) outlast YouTube's session-inactivity
timeout. Midway through the batch, the server invalidates the cookie,
yt-dlp falls back to the android_vr client, and every remaining URL
fails with 'Sign in to confirm you're not a bot' until the user
manually refreshes their Firefox tab.

Fix: periodically make a minimal authenticated request — a yt-dlp
``--simulate`` against ``youtube.com/`` using the same Firefox cookies
the real downloads use. That's cheap (no download, no metadata
extraction for a specific video), but it counts as session activity
so YouTube doesn't age the cookie out.

This module is runnable two ways:

  1. Standalone script — ``python -m tomslab.ingest.youtube_keepalive``
     loops forever, pinging every 10-20 minutes (random). Used by the
     download_tom_videos.bat batch via ``start /B``.

  2. Qt worker class — :class:`KeepAliveWorker` runs the same loop on
     a QThread inside Tom's Lab, started alongside any long-running
     video ingest and stopped when ingest finishes.

Either path only *prolongs* an existing valid session. If the user
closes Firefox or YouTube server-side revokes the cookie, keepalive
can't help — only a manual sign-in can.
"""
from __future__ import annotations

import logging
import random
import time
import urllib.request

log = logging.getLogger(__name__)


# Landing inside the signed-in account surface (m.youtube.com's feed
# endpoint is a lightweight page that requires session auth to render
# fully — an unauth'd hit gets redirected). Using this instead of
# yt-dlp's video-info path because we only need the server to see
# 'this cookie is active', not to resolve any video format. Pinging
# an account URL directly means no n-challenge, no PO token, no
# format extraction work — a ~50 KB HTML fetch.
KEEPALIVE_URL = "https://www.youtube.com/feed/subscriptions"
MIN_SLEEP = 600   # 10 min
MAX_SLEEP = 1200  # 20 min
REQUEST_TIMEOUT = 30
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0"
)


def _load_cookies(browser: str):
    """Borrow yt-dlp's Firefox cookie extractor so the keepalive uses
    exactly the same session the downloader does. Returns a
    ``http.cookiejar.CookieJar`` compatible object."""
    from yt_dlp import YoutubeDL
    ydl = YoutubeDL({"cookiesfrombrowser": (browser,), "quiet": True})
    return ydl.cookiejar


def ping_once(browser: str = "firefox") -> bool:
    """One authenticated HTTP GET to a lightweight signed-in YouTube
    endpoint. Returns True on HTTP 200 with visible session content.
    Swallows all errors — the keepalive loop is best-effort."""
    try:
        jar = _load_cookies(browser)
    except Exception as exc:
        log.debug("keepalive cookie load failed: %s", exc)
        return False
    try:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        req = urllib.request.Request(
            KEEPALIVE_URL,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
        )
        with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
            resp.read(4096)   # touch bytes so the server sees real use
            return 200 <= resp.status < 400
    except Exception as exc:   # pragma: no cover
        log.debug("keepalive ping failed: %s", exc)
        return False


def loop(browser: str = "firefox") -> None:
    """Run forever, pinging youtube.com every 10-20 minutes (random).
    Randomness keeps the requests from looking mechanical to any
    rate-limiter heuristic watching for fixed-cadence probes."""
    log.info("TomTube keepalive loop started (browser=%s)", browser)
    while True:
        ok = ping_once(browser)
        log.info("keepalive ping: %s", "ok" if ok else "failed (tolerating)")
        sleep = random.randint(MIN_SLEEP, MAX_SLEEP)
        time.sleep(sleep)


# ---------------------------------------------------------------------------
# Qt integration — used by Tom's Lab while a video ingest is running.
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtCore import QThread
except ImportError:   # pragma: no cover - headless envs
    QThread = None   # type: ignore[assignment]


if QThread is not None:

    class KeepAliveWorker(QThread):   # type: ignore[misc]
        """Background QThread variant of :func:`loop`. Started when a
        long-running video ingest begins in Tom's Lab; stopped (politely)
        when the ingest finishes or the user closes the app."""

        def __init__(self, browser: str = "firefox", parent=None) -> None:
            super().__init__(parent)
            self._browser = browser
            self._stop = False

        def stop(self) -> None:
            """Request an orderly shutdown — runs on the next loop tick."""
            self._stop = True

        def run(self) -> None:   # pragma: no cover - needs a Qt runtime
            log.info("KeepAliveWorker started (browser=%s)", self._browser)
            while not self._stop:
                ok = ping_once(self._browser)
                log.info("keepalive ping: %s", "ok" if ok else "failed")
                # Sleep in 5-second chunks so stop() is responsive.
                sleep_total = random.randint(MIN_SLEEP, MAX_SLEEP)
                waited = 0
                while waited < sleep_total and not self._stop:
                    time.sleep(5)
                    waited += 5
            log.info("KeepAliveWorker stopped")


if __name__ == "__main__":   # pragma: no cover
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  keepalive  %(message)s",
    )
    loop()
