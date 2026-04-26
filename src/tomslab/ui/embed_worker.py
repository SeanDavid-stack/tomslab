"""QThread that runs the embedding pipeline off the UI thread."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod
from tomslab.ai import registry
from tomslab.ai.base import ProviderError, ProviderUnavailable
from tomslab import embed_service
from tomslab.jobs import registry as jobs_registry


class EmbedWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, status
    finished_ok = pyqtSignal(int)           # count embedded
    failed = pyqtSignal(str)

    _JOB_ID = "embed.text"

    def __init__(self, scope: str = "all", parent=None) -> None:
        """scope: 'windows' | 'docs' | 'videos' | 'both' (legacy) | 'all'"""
        super().__init__(parent)
        self._scope = scope

    def run(self) -> None:
        jobs_registry.start(self._JOB_ID, "Text embeddings")
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            provider = registry.get_embed_provider(conn)
            total_done = 0

            def _prog(prefix: str):
                def _cb(d: int, t: int, s: str) -> None:
                    self.progress.emit(d, t, f"{prefix}: {s}")
                    jobs_registry.update(
                        self._JOB_ID, done=d, total=t, message=f"{prefix}: {s}",
                    )
                return _cb

            if self._scope in ("windows", "both", "all"):
                total_done += embed_service.embed_pending(
                    conn, provider, progress=_prog("windows"),
                )
            if self._scope in ("docs", "both", "all"):
                total_done += embed_service.embed_pending_doc_pages(
                    conn, provider, progress=_prog("doc pages"),
                )
            if self._scope in ("videos", "all"):
                total_done += embed_service.embed_pending_video_chunks(
                    conn, provider, progress=_prog("video chunks"),
                )
            conn.close()
            # Blow every semantic cache so Ask Tom sees the new content
            # immediately — without this, a process that queried before
            # the embed job would serve a stale matrix forever.
            try:
                from tomslab import semantic
                semantic.invalidate_all_caches()
            except Exception:
                pass
            jobs_registry.finish(
                self._JOB_ID, ok=True, message=f"{total_done} embedded",
            )
            self.finished_ok.emit(total_done)
        except (ProviderError, ProviderUnavailable) as exc:
            jobs_registry.finish(self._JOB_ID, ok=False, message=str(exc))
            self.failed.emit(str(exc))
        except Exception as exc:
            jobs_registry.finish(
                self._JOB_ID, ok=False,
                message=f"{type(exc).__name__}: {exc}",
            )
            self.failed.emit(f"{type(exc).__name__}: {exc}")
