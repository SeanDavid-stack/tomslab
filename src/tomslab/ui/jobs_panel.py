"""Jobs panel — live view of every background worker.

Polls ``tomslab.jobs.registry`` on a 500 ms QTimer and renders one
compact row per running/recent job with progress + ETA. Auto-hides
when nothing is running so it doesn't clutter the idle UI.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from tomslab.jobs import JobSnapshot, registry


COLOR_BG_ALT = "#2B2D31"
COLOR_BORDER = "#3F4147"
COLOR_TEXT = "#DBDEE1"
COLOR_DIM = "#949BA4"
COLOR_ACCENT = "#FFC857"
COLOR_OK = "#43B581"
COLOR_FAIL = "#ED4245"


class JobsPanel(QWidget):
    """Floating / docked panel of active jobs.

    Parent owns the widget and decides where to place it (we assume
    it's added as a child of MainWindow, anchored bottom-right).
    The panel shows/hides itself automatically based on whether any
    jobs are present.
    """

    POLL_MS = 500

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("JobsPanel")
        self.setStyleSheet(
            f"#JobsPanel {{ background: transparent; }}"
        )
        # Card container with rounded corner + border.
        self._card = QFrame(self)
        self._card.setObjectName("JobsCard")
        self._card.setStyleSheet(
            f"#JobsCard {{ background: {COLOR_BG_ALT};"
            f" border: 1px solid {COLOR_BORDER};"
            f" border-radius: 10px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        v = QVBoxLayout(self._card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Background jobs")
        title.setStyleSheet(
            f"color: {COLOR_TEXT}; font-weight: 600; font-size: 12px;"
        )
        header.addWidget(title)
        header.addStretch(1)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {COLOR_DIM}; font-size: 10px;"
        )
        header.addWidget(self._count_label)
        v.addLayout(header)

        # Container for dynamic rows — one per job.
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        v.addWidget(self._rows_host)

        self._row_widgets: dict[str, _JobRow] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self.setFixedWidth(320)
        self.setVisible(False)

    def _refresh(self) -> None:
        jobs = registry.snapshot()
        if not jobs:
            self.setVisible(False)
            return
        self.setVisible(True)

        # Upsert rows per job_id.
        present = set()
        for j in jobs:
            present.add(j.job_id)
            row = self._row_widgets.get(j.job_id)
            if row is None:
                row = _JobRow(self._rows_host)
                self._rows_layout.addWidget(row)
                self._row_widgets[j.job_id] = row
            row.update_from(j)

        # Drop rows whose jobs have been pruned from the registry.
        to_remove = [jid for jid in self._row_widgets if jid not in present]
        for jid in to_remove:
            w = self._row_widgets.pop(jid)
            w.setParent(None)
            w.deleteLater()

        running = sum(1 for j in jobs if j.status == "running")
        done = sum(1 for j in jobs if j.status == "done")
        failed = sum(1 for j in jobs if j.status == "failed")
        bits: list[str] = []
        if running:
            bits.append(f"{running} running")
        if done:
            bits.append(f"{done} done")
        if failed:
            bits.append(f"{failed} failed")
        self._count_label.setText("  ·  ".join(bits))


class _JobRow(QFrame):
    """A single line for one job: name, progress bar, ETA/message."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background: transparent; }"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        self._name_lbl = QLabel("")
        self._name_lbl.setStyleSheet(
            f"color: {COLOR_TEXT}; font-size: 11px; font-weight: 500;"
        )
        top.addWidget(self._name_lbl, stretch=1)
        self._eta_lbl = QLabel("")
        self._eta_lbl.setStyleSheet(
            f"color: {COLOR_DIM}; font-size: 10px;"
        )
        top.addWidget(self._eta_lbl)
        v.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {COLOR_BORDER};"
            f" border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {COLOR_ACCENT};"
            f" border-radius: 2px; }}"
        )
        v.addWidget(self._bar)

        self._msg_lbl = QLabel("")
        self._msg_lbl.setStyleSheet(
            f"color: {COLOR_DIM}; font-size: 10px;"
        )
        self._msg_lbl.setWordWrap(False)
        v.addWidget(self._msg_lbl)

    def update_from(self, j: JobSnapshot) -> None:
        # Status tint on the progress bar to flag done/failed distinctly.
        if j.status == "done":
            chunk_color = COLOR_OK
        elif j.status == "failed":
            chunk_color = COLOR_FAIL
        else:
            chunk_color = COLOR_ACCENT
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {COLOR_BORDER};"
            f" border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {chunk_color};"
            f" border-radius: 2px; }}"
        )

        self._name_lbl.setText(j.name)
        if j.total > 0:
            self._bar.setRange(0, j.total)
            self._bar.setValue(j.done)
        else:
            self._bar.setRange(0, 0)   # indeterminate spinner mode

        tag = ""
        if j.status == "done":
            tag = "done"
        elif j.status == "failed":
            tag = "failed"
        elif j.pct:
            tag = f"{j.pct}%"
        eta = j.eta_label()
        pieces = [p for p in (tag, eta) if p]
        self._eta_lbl.setText("  ·  ".join(pieces))

        # Truncate long status messages so the panel width stays stable.
        msg = j.message or ""
        if len(msg) > 60:
            msg = msg[:57] + "…"
        self._msg_lbl.setText(msg)
