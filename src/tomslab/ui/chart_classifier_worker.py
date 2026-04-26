"""QThread wrapper around the chart-image classifier pipeline.

Mirrors ``image_embed_worker`` — opens its own DB connection, runs the
cheap pre-filter then the CLIP scoring, and emits progress back to the
main window. GPU contention with transcription is real; this worker
will queue up behind whoever grabs the CUDA context first.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from tomslab import db as dbmod
from tomslab.ingest import chart_classifier
from tomslab.jobs import registry as jobs_registry


class ChartClassifierWorker(QThread):
    progress = pyqtSignal(int, int, str)       # done, total, status
    finished_ok = pyqtSignal(dict)             # counts dict
    failed = pyqtSignal(str)

    _JOB_ID = "classify.charts"
    _JOB_NAME = "Classify Discord images"

    def run(self) -> None:
        jobs_registry.start(self._JOB_ID, self._JOB_NAME)
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)

            def _progress(s: str, d: int, t: int) -> None:
                self.progress.emit(d, t, s)
                jobs_registry.update(
                    self._JOB_ID, done=d, total=t, message=s,
                )

            counts = chart_classifier.run_full_classification(
                conn,
                progress=_progress,
            )
            conn.close()
            jobs_registry.finish(self._JOB_ID, ok=True, message="done")
            self.finished_ok.emit(counts)
        except Exception as exc:
            jobs_registry.finish(
                self._JOB_ID, ok=False,
                message=f"{type(exc).__name__}: {exc}",
            )
            self.failed.emit(f"{type(exc).__name__}: {exc}")
