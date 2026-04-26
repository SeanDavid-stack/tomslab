"""QThread that enumerates a YouTube channel off the UI thread.

Without this, ``find_new_videos`` runs on the main thread and yt-dlp's
network round-trip freezes the window (Windows shows "Not Responding")
for anywhere between 5 seconds and a minute. With this worker the
window stays responsive, a job-panel entry shows the in-progress
state, and the call is cancellable by closing the app.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod
from tomslab.jobs import registry as jobs_registry


class YouTubeCheckWorker(QThread):
    # payload: list[VideoEntry] new, list[VideoEntry] existing
    finished_ok = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    _JOB_ID = "youtube.check"

    def __init__(self, title_filter: str = "tom b", parent=None) -> None:
        super().__init__(parent)
        self._title_filter = title_filter

    def run(self) -> None:
        jobs_registry.start(self._JOB_ID, "Checking YouTube for new Tom videos")
        try:
            # Use a private DB connection — QThreads can't share sqlite3
            # handles with the UI thread safely.
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                from tomslab.ingest.youtube import find_new_videos
                new, existing = find_new_videos(
                    conn, title_filter=self._title_filter
                )
            finally:
                conn.close()
            jobs_registry.finish(
                self._JOB_ID, ok=True,
                message=f"{len(new)} new, {len(existing)} already indexed",
            )
            self.finished_ok.emit(new, existing)
        except Exception as exc:
            jobs_registry.finish(
                self._JOB_ID, ok=False,
                message=f"{type(exc).__name__}: {exc}",
            )
            self.failed.emit(f"{type(exc).__name__}: {exc}")
