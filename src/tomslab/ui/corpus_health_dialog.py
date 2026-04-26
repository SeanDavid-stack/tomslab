"""Corpus Health dialog — human-readable view of what the AI can see.

Opens from Help → Corpus Health Check. Shows:
  * one row per retrieval category with status icon + coverage %
  * a "Fix" hint per row when coverage is low
  * optional canary-query panel that fires standard questions and
    reports whether each source type (Discord / PDFs / YouTube)
    actually returned hits — catches silent retrieval breakage
    like the "video cache stuck at empty" bug
"""
from __future__ import annotations

import sqlite3
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tomslab import corpus_health, db as dbmod


_COLOR_BG = "#2B2D31"
_COLOR_CARD = "#313338"
_COLOR_BORDER = "#3F4147"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_OK = "#43B581"
_COLOR_WARN = "#FAA61A"
_COLOR_CRITICAL = "#ED4245"
_COLOR_ACCENT = "#FFC857"


class _ReportWorker(QThread):
    """Runs the (potentially slow) audit + canary queries off the UI thread."""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object)    # CorpusHealthReport
    failed = pyqtSignal(str)

    def __init__(self, include_canaries: bool, parent: Any = None) -> None:
        super().__init__(parent)
        self._include_canaries = include_canaries

    def run(self) -> None:
        try:
            conn = dbmod.connect()
            dbmod.initialise(conn)
            rep = corpus_health.full_report(
                conn,
                include_canaries=self._include_canaries,
                progress=lambda s: self.progress.emit(s),
            )
            conn.close()
            self.finished_ok.emit(rep)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class CorpusHealthDialog(QDialog):
    """Modal audit dialog.

    Layout:
        Header (overall status)
        Scrollable list of category rows
        Canary queries panel
        [Re-run]  [Close] footer
    """
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tom's Lab — Corpus Health")
        self.setModal(True)
        self.resize(780, 640)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        heading = QLabel("<b>Corpus Health Check</b>")
        heading.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 16px;")
        outer.addWidget(heading)

        sub = QLabel(
            "Checks every knowledge source the AI relies on — Discord "
            "messages, Tom's PDFs, YouTube transcripts, chart images "
            "— and verifies each has been fully embedded and is "
            "retrievable. Runs a handful of canary questions to catch "
            "silent gaps (missing embeddings, stale caches)."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {_COLOR_DIM}; font-size: 11px;")
        outer.addWidget(sub)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-size: 12px; padding: 8px 10px;"
            f" background: {_COLOR_CARD}; border-radius: 6px;"
        )
        outer.addWidget(self._status_lbl)

        self._progress_lbl = QLabel("")
        self._progress_lbl.setStyleSheet(
            f"color: {_COLOR_ACCENT}; font-size: 11px;"
        )
        outer.addWidget(self._progress_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminate while running
        self._bar.setFixedHeight(4)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {_COLOR_BORDER};"
            f" border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {_COLOR_ACCENT};"
            f" border-radius: 2px; }}"
        )
        outer.addWidget(self._bar)

        # Scrollable results area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {_COLOR_BG}; border: none; }}"
        )
        self._results_host = QWidget()
        self._results_layout = QVBoxLayout(self._results_host)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(8)
        self._scroll.setWidget(self._results_host)
        outer.addWidget(self._scroll, stretch=1)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._rerun_btn = QPushButton("Re-run")
        self._rerun_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_COLOR_TEXT};"
            f" padding: 8px 16px; border: 1px solid {_COLOR_BORDER};"
            f" border-radius: 6px; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {_COLOR_TEXT}; }}"
        )
        self._rerun_btn.clicked.connect(self._start_audit)
        footer.addWidget(self._rerun_btn)

        close = QPushButton("Close")
        close.setStyleSheet(
            f"QPushButton {{ background: {_COLOR_ACCENT}; color: #1E1F22;"
            f" padding: 8px 18px; border: none; border-radius: 6px;"
            f" font-weight: 600; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #FFD87A; }}"
        )
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        outer.addLayout(footer)

        self._worker: _ReportWorker | None = None
        self._start_audit()

    # ------------------------------------------------------------------
    def _start_audit(self) -> None:
        self._rerun_btn.setEnabled(False)
        self._bar.setVisible(True)
        self._status_lbl.setText("Auditing corpus…")
        self._progress_lbl.setText("")
        self._clear_results()

        self._worker = _ReportWorker(include_canaries=True, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, msg: str) -> None:
        self._progress_lbl.setText(msg)

    def _on_failed(self, err: str) -> None:
        self._bar.setVisible(False)
        self._progress_lbl.setText("")
        self._status_lbl.setText(
            f'<span style="color: {_COLOR_CRITICAL};">Audit failed:</span> {err}'
        )
        self._rerun_btn.setEnabled(True)

    def _on_finished(self, report) -> None:
        self._bar.setVisible(False)
        self._progress_lbl.setText("")
        self._rerun_btn.setEnabled(True)

        # Overall banner
        icon = "✅" if report.overall_ok else "⚠️"
        color = _COLOR_OK if report.overall_ok else _COLOR_WARN
        self._status_lbl.setText(
            f'<span style="color: {color}; font-size: 18px;">{icon}</span>  '
            f"{report.summary}"
        )

        # Categories
        self._clear_results()
        section = QLabel("<b>Coverage by category</b>")
        section.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 13px;")
        self._results_layout.addWidget(section)

        for c in report.categories:
            self._results_layout.addWidget(_CategoryRow(c))

        # Canaries
        if report.canaries:
            self._results_layout.addSpacing(8)
            section2 = QLabel("<b>Canary retrieval tests</b>")
            section2.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 13px;")
            self._results_layout.addWidget(section2)

            hint = QLabel(
                "Each canary fires a real question through the retrieval "
                "pipeline. A canary <b>passes</b> when the question gets "
                "hits from all three source types: Discord, PDFs, YouTube."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet(
                f"color: {_COLOR_DIM}; font-size: 11px; padding-bottom: 4px;"
            )
            self._results_layout.addWidget(hint)

            for cq in report.canaries:
                self._results_layout.addWidget(_CanaryRow(cq))

        self._results_layout.addStretch(1)

    def _clear_results(self) -> None:
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()


class _CategoryRow(QWidget):
    """One card per category — icon, title, coverage bar, detail text."""

    _ICON = {"ok": "✅", "warn": "⚠️", "critical": "❌", "empty": "·"}
    _COLOR = {
        "ok": _COLOR_OK,
        "warn": _COLOR_WARN,
        "critical": _COLOR_CRITICAL,
        "empty": _COLOR_DIM,
    }

    def __init__(self, cat: corpus_health.CategoryCoverage) -> None:
        super().__init__()
        self.setStyleSheet(
            f"background: {_COLOR_CARD}; border: 1px solid {_COLOR_BORDER};"
            f" border-radius: 8px;"
        )
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        header = QHBoxLayout()
        icon = QLabel(self._ICON.get(cat.status, "·"))
        icon.setStyleSheet(
            f"color: {self._COLOR[cat.status]}; font-size: 15px;"
            f" padding-right: 4px;"
        )
        header.addWidget(icon)

        title = QLabel(f"<b>{cat.name}</b>")
        title.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 12px;")
        header.addWidget(title, stretch=1)

        stat = QLabel(
            f"{cat.embedded:,} / {cat.total:,}  ({cat.pct:.1f}%)"
        )
        stat.setStyleSheet(
            f"color: {self._COLOR[cat.status]}; font-size: 12px;"
            f" font-weight: 600;"
        )
        header.addWidget(stat)
        v.addLayout(header)

        bar = QProgressBar()
        bar.setRange(0, max(1, cat.total))
        bar.setValue(cat.embedded)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {_COLOR_BORDER};"
            f" border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {self._COLOR[cat.status]};"
            f" border-radius: 2px; }}"
        )
        v.addWidget(bar)

        if cat.warning:
            warn = QLabel(f"<b>{cat.warning}.</b>  {cat.detail}")
            warn.setWordWrap(True)
            warn.setStyleSheet(
                f"color: {_COLOR_DIM}; font-size: 11px; padding-top: 2px;"
            )
            v.addWidget(warn)
        elif cat.detail:
            det = QLabel(cat.detail)
            det.setWordWrap(True)
            det.setStyleSheet(f"color: {_COLOR_DIM}; font-size: 11px;")
            v.addWidget(det)


class _CanaryRow(QWidget):
    def __init__(self, cq: corpus_health.CanaryResult) -> None:
        super().__init__()
        color = _COLOR_OK if cq.passed else _COLOR_WARN
        icon = "✅" if cq.passed else "⚠️"
        self.setStyleSheet(
            f"background: {_COLOR_CARD}; border: 1px solid {_COLOR_BORDER};"
            f" border-radius: 8px;"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)

        top = QHBoxLayout()
        ic = QLabel(icon)
        ic.setStyleSheet(
            f"color: {color}; font-size: 14px; padding-right: 4px;"
        )
        top.addWidget(ic)

        q = QLabel(f'<i>"{cq.query}"</i>')
        q.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 12px;")
        top.addWidget(q, stretch=1)
        v.addLayout(top)

        counts = QLabel(
            f"💬 {cq.n_messages} Discord  ·  "
            f"📄 {cq.n_doc_pages} PDF pages  ·  "
            f"▶ {cq.n_video_chunks} video chunks"
        )
        counts.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 11px; padding-left: 20px;"
        )
        v.addWidget(counts)

        if cq.note:
            note = QLabel(
                f'<span style="color: {color};">▲</span>  {cq.note}'
            )
            note.setStyleSheet(
                f"color: {_COLOR_DIM}; font-size: 11px; padding-left: 20px;"
            )
            v.addWidget(note)
