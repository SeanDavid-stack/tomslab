"""In-process job registry for background workers.

Every long-running worker (video transcription, image embedding,
chart classification, DCE ingest, etc.) registers itself here so the
UI's Jobs panel can render a unified view of what's running, progress,
rolling-average throughput, and ETA.

Rolling throughput: we remember the last ``SAMPLE_WINDOW`` progress
deltas and take the average items/sec. Raw instantaneous rate is too
jumpy for a calm ETA string (WhisperModel has 10-second pauses between
files for VAD; CLIP has per-batch stalls).

Design notes:
  * Pure in-memory — jobs don't persist across app restarts.
  * Thread-safe via a single lock. Worker threads call update();
    the UI polls snapshot().
  * No Qt coupling — the UI layer wraps this in a QTimer-driven
    panel (see tomslab/ui/jobs_panel.py).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


SAMPLE_WINDOW = 20    # number of progress ticks to average for ETA


def _fmt_duration(sec: float) -> str:
    """Render seconds as 'HH:MM:SS' / 'MM:SS' / 'Xs' — compact for chips."""
    if sec < 60:
        return f"{int(sec)}s"
    if sec < 3600:
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m}:{s:02d}"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    return f"{h}:{m:02d}:{s:02d}"


@dataclass
class JobSnapshot:
    job_id: str
    name: str
    status: str            # 'running' | 'done' | 'failed'
    done: int
    total: int
    message: str
    started_at: float
    updated_at: float
    finished_at: float | None
    rate_items_per_sec: float   # rolling average
    eta_seconds: float | None   # None if unknown

    @property
    def pct(self) -> int:
        if self.total <= 0:
            return 0
        return max(0, min(100, int(self.done * 100 / self.total)))

    def eta_label(self) -> str:
        if self.status != "running":
            return ""
        if self.eta_seconds is None or self.eta_seconds <= 0:
            return ""
        return f"ETA {_fmt_duration(self.eta_seconds)}"


@dataclass
class _JobState:
    job_id: str
    name: str
    status: str = "running"
    done: int = 0
    total: int = 0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    samples: Deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=SAMPLE_WINDOW)
    )

    def rate_items_per_sec(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        t0, d0 = self.samples[0]
        t1, d1 = self.samples[-1]
        dt = t1 - t0
        dd = d1 - d0
        if dt <= 0 or dd <= 0:
            return 0.0
        return dd / dt

    def eta_seconds(self) -> float | None:
        rate = self.rate_items_per_sec()
        if rate <= 0 or self.total <= 0:
            return None
        remaining = max(0, self.total - self.done)
        if remaining == 0:
            return 0.0
        return remaining / rate


class JobRegistry:
    """Singleton registry. Import the module-level ``registry`` alias."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _JobState] = {}
        # Keep recently-finished jobs in the UI for a brief toast-like
        # moment. The panel drops them after ~30 seconds of "done" state.
        self._listeners: list = []

    # -- mutation (called from worker threads) --------------------------
    def start(self, job_id: str, name: str, total: int = 0) -> None:
        with self._lock:
            self._jobs[job_id] = _JobState(
                job_id=job_id, name=name, total=total,
            )
        self._notify()

    def update(
        self,
        job_id: str,
        *,
        done: int | None = None,
        total: int | None = None,
        message: str = "",
    ) -> None:
        with self._lock:
            st = self._jobs.get(job_id)
            if st is None:
                return
            now = time.time()
            if done is not None:
                st.done = done
            if total is not None and total > 0:
                st.total = total
            if message:
                st.message = message
            st.updated_at = now
            st.samples.append((now, st.done))
        self._notify()

    def finish(self, job_id: str, *, ok: bool = True, message: str = "") -> None:
        with self._lock:
            st = self._jobs.get(job_id)
            if st is None:
                return
            st.status = "done" if ok else "failed"
            st.finished_at = time.time()
            if message:
                st.message = message
        self._notify()

    # -- read-only (UI thread) -----------------------------------------
    def snapshot(self) -> list[JobSnapshot]:
        now = time.time()
        with self._lock:
            # prune any 'done' or 'failed' jobs that finished >30s ago
            to_drop = [
                jid for jid, st in self._jobs.items()
                if st.finished_at is not None and (now - st.finished_at) > 30.0
            ]
            for jid in to_drop:
                del self._jobs[jid]
            out = [
                JobSnapshot(
                    job_id=st.job_id,
                    name=st.name,
                    status=st.status,
                    done=st.done,
                    total=st.total,
                    message=st.message,
                    started_at=st.started_at,
                    updated_at=st.updated_at,
                    finished_at=st.finished_at,
                    rate_items_per_sec=st.rate_items_per_sec(),
                    eta_seconds=st.eta_seconds(),
                )
                for st in self._jobs.values()
            ]
        return out

    def any_running(self) -> bool:
        with self._lock:
            return any(st.status == "running" for st in self._jobs.values())

    # -- listener (UI refresh hint) ------------------------------------
    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        # Listeners are called from the worker thread. UI widgets should
        # use a QTimer-based polling pattern rather than hooking this
        # signal directly — cross-thread widget updates aren't safe.
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass


# Module-level singleton.
registry = JobRegistry()
