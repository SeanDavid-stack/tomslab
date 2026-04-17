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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            provider = registry.get_embed_provider(conn)
            n = embed_service.embed_pending(
                conn,
                provider,
                progress=lambda d, t, s: self.progress.emit(d, t, s),
            )
            conn.close()
            self.finished_ok.emit(n)
        except (ProviderError, ProviderUnavailable) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
