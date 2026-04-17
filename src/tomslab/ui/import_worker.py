"""QThread wrapper around tomslab.ingest.importer so imports don't block the UI."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod
from tomslab.ingest.importer import ImportResult, import_export_file


class ImportWorker(QThread):
    progress = pyqtSignal(str, int, int)       # phase, current, total
    finished_ok = pyqtSignal(object)           # ImportResult
    failed = pyqtSignal(str)

    def __init__(self, json_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._json_path = Path(json_path)

    def run(self) -> None:  # runs on the worker thread
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                result: ImportResult = import_export_file(
                    self._json_path,
                    conn=conn,
                    progress=lambda phase, cur, tot: self.progress.emit(phase, cur, tot),
                )
            finally:
                conn.close()
            self.finished_ok.emit(result)
        except Exception as exc:  # surface to UI rather than crash
            self.failed.emit(f"{type(exc).__name__}: {exc}")
