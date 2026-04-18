"""QThread wrapper for the YouTube / folder ingest pipelines."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod


class VideoIngestWorker(QThread):
    """Runs the direct-YouTube ingest path (scrape → download → transcribe)."""

    progress = pyqtSignal(str, int, int)       # stage, current, total
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


class FolderIngestWorker(QThread):
    """Runs the folder-import path (scan folder → transcribe existing files).

    This is the reliable alternative when YouTube's direct-download auth
    gates are in a bad state — the user bulk-downloads with any consumer
    tool, drops the files in a folder, and we transcribe from there.
    """

    progress = pyqtSignal(str, int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        folder: Path,
        model_name: str = "large-v3",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._folder = Path(folder)
        self._model_name = model_name

    def run(self) -> None:
        try:
            from tomslab.ingest.youtube import ingest_folder
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                report = ingest_folder(
                    conn,
                    self._folder,
                    model_name=self._model_name,
                    progress=lambda s, c, t: self.progress.emit(s, c, t),
                )
            finally:
                conn.close()
            self.finished_ok.emit(report)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
