"""Background worker that warms the in-memory semantic + visual
matrices at app startup.

Without prewarming, the first Ask Tom question and the first Gallery
visual search each pay a 3-to-5 second disk-read + normalisation cost
to populate their caches. The user experiences that as the app
"hanging" on their first real interaction. Prewarming from a
background thread shifts that cost to the first few seconds AFTER the
main window is visible, where it's invisible to the user.
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod
from tomslab import semantic, visual


log = logging.getLogger(__name__)


class PrewarmWorker(QThread):
    """Load the semantic (text) + visual (CLIP) in-memory matrices on
    a background QThread so their first-query warm-up is paid while
    the user is still reading the main window, not waiting on a
    chat response."""

    finished_ok = pyqtSignal(float)     # seconds it took to warm everything
    failed = pyqtSignal(str)

    def run(self) -> None:
        t0 = time.time()
        try:
            # QThreads cannot share sqlite3 connections with the UI
            # thread, so we open a fresh one. The matrices we populate
            # are module-level objects guarded by threading locks, so
            # they're visible to the main-thread queries once this
            # worker finishes.
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                semantic.prewarm_all_caches(conn)
                log.info("prewarm: semantic caches loaded")
                visual.prewarm_all_caches(conn)
                log.info("prewarm: visual cache loaded")
            finally:
                conn.close()
        except Exception as exc:
            log.warning("prewarm worker failed (non-fatal): %s", exc)
            self.failed.emit(str(exc))
            return
        elapsed = time.time() - t0
        log.info("prewarm: done in %.2fs", elapsed)
        self.finished_ok.emit(elapsed)
