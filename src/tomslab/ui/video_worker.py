"""QThread wrapper for the YouTube ingest pipeline."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod


class VideoIngestWorker(QThread):
    progress = pyqtSignal(str, int, int)      # stage, current, total
    finished_ok = pyqtSignal(object)           # report dict
    failed = pyqtSignal(str)

    def __init__(
        self,
        limit: int | None = None,
        model_name: str = "large-v3",
        bitrate_kbps: int = 96,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._limit = limit
        self._model_name = model_name
        self._bitrate_kbps = bitrate_kbps

    def run(self) -> None:
        try:
            from tomslab.ingest.youtube import ingest_channel
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                report = ingest_channel(
                    conn,
                    limit=self._limit,
                    model_name=self._model_name,
                    bitrate_kbps=self._bitrate_kbps,
                    progress=lambda s, c, t: self.progress.emit(s, c, t),
                )
            finally:
                conn.close()
            self.finished_ok.emit(report)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
