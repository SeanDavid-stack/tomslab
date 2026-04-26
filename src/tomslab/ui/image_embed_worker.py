"""QThread wrapper around the CLIP image embedding pipeline."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod, image_embed_service
from tomslab.jobs import registry as jobs_registry


class ImageEmbedWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, status
    finished_ok = pyqtSignal(int)
    failed = pyqtSignal(str)

    _JOB_ID = "embed.images_clip"
    _JOB_NAME = "Image CLIP embeddings"

    def run(self) -> None:
        jobs_registry.start(self._JOB_ID, self._JOB_NAME)
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)

            def _progress(d: int, t: int, s: str) -> None:
                self.progress.emit(d, t, s)
                jobs_registry.update(
                    self._JOB_ID, done=d, total=t, message=s,
                )

            n = image_embed_service.embed_pending(
                conn, progress=_progress,
            )
            conn.close()
            try:
                from tomslab import visual, semantic
                visual.invalidate_cache()
                semantic.invalidate_all_caches()
            except Exception:
                pass
            jobs_registry.finish(
                self._JOB_ID, ok=True, message=f"{n} embedded",
            )
            self.finished_ok.emit(n)
        except Exception as exc:
            jobs_registry.finish(
                self._JOB_ID, ok=False,
                message=f"{type(exc).__name__}: {exc}",
            )
            self.failed.emit(f"{type(exc).__name__}: {exc}")
