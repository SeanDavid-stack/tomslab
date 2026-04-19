"""QThread that runs the embedding pipeline off the UI thread."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod
from tomslab.ai import registry
from tomslab.ai.base import ProviderError, ProviderUnavailable
from tomslab import embed_service


class EmbedWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, status
    finished_ok = pyqtSignal(int)           # count embedded
    failed = pyqtSignal(str)

    def __init__(self, scope: str = "all", parent=None) -> None:
        """scope: 'windows' | 'docs' | 'videos' | 'both' (legacy) | 'all'"""
        super().__init__(parent)
        self._scope = scope

    def run(self) -> None:
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            provider = registry.get_embed_provider(conn)
            total_done = 0
            if self._scope in ("windows", "both", "all"):
                total_done += embed_service.embed_pending(
                    conn,
                    provider,
                    progress=lambda d, t, s: self.progress.emit(d, t, f"windows: {s}"),
                )
            if self._scope in ("docs", "both", "all"):
                total_done += embed_service.embed_pending_doc_pages(
                    conn,
                    provider,
                    progress=lambda d, t, s: self.progress.emit(d, t, f"doc pages: {s}"),
                )
            if self._scope in ("videos", "all"):
                total_done += embed_service.embed_pending_video_chunks(
                    conn,
                    provider,
                    progress=lambda d, t, s: self.progress.emit(d, t, f"video chunks: {s}"),
                )
            conn.close()
            self.finished_ok.emit(total_done)
        except (ProviderError, ProviderUnavailable) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
