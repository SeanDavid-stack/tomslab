"""QThread wrapper around tomslab.data_pack_install.install_pack so the
big extract phase doesn't block the UI thread.

Without this, on the v1.0 data pack (~10.5 GB compressed → ~25-30 GB
on disk) Windows shows "Not Responding" on the Tom's Lab window for
5-15 minutes during extraction, which users routinely interpret as a
crash and kill the app — the install is actually working, but the UX
reads as broken. Running the extract on a worker thread keeps the UI
paint loop alive and lets us stream real progress into the status bar.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


class DataPackInstallWorker(QThread):
    progress = pyqtSignal(int, int, str)        # done, total, status
    finished_ok = pyqtSignal(object)            # InstallResult
    failed = pyqtSignal(str, str)               # error_class_name, message

    def __init__(self, pack_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._pack_path = Path(pack_path)

    def run(self) -> None:  # runs on the worker thread
        # Import inside run() so the heavy module (zstd, tarfile)
        # only loads when an install is actually requested.
        from tomslab import data_pack_install as dp

        def _emit_progress(done: int, total: int, status: str) -> None:
            self.progress.emit(done, total, status)

        try:
            result = dp.install_pack(self._pack_path, progress=_emit_progress)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(type(exc).__name__, str(exc))
