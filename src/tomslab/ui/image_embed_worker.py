"""QThread wrapper around the CLIP image embedding pipeline."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod, image_embed_service


class ImageEmbedWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, status
    finished_ok = pyqtSignal(int)
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            n = image_embed_service.embed_pending(
                conn, progress=lambda d, t, s: self.progress.emit(d, t, s)
            )
            conn.close()
            self.finished_ok.emit(n)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
