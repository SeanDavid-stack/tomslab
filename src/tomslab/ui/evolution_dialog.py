"""Evolution timeline dialog — shows how Tom's framing of a concept has
changed over time. Vertical timeline with per-quarter buckets, each
containing a few representative Discord posts / video chunks. Clicking a
citation inside the dialog opens the usual detail view / YouTube link.
"""
from __future__ import annotations

import html
import sqlite3
from typing import Callable

from tomslab.ui.browser_open import open_browser

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from tomslab import db as dbmod
from tomslab.evolution import EvolutionTimeline, build_timeline


class _TimelineWorker(QThread):
    """Run build_timeline() off the UI thread. The retrieval does
    semantic search across every indexed post + video chunk for a
    concept, which on a big corpus can take 3-10 seconds and blocks the
    window enough that Windows flags it 'Not Responding'."""

    finished_ok = pyqtSignal(object)   # EvolutionTimeline
    failed = pyqtSignal(str)

    def __init__(self, concept: str, parent=None) -> None:
        super().__init__(parent)
        self._concept = concept

    def run(self) -> None:
        try:
            # QThreads cannot share a sqlite3 connection safely with the
            # UI thread, so each worker opens its own.
            conn = dbmod.connect()
            dbmod.initialise(conn)
            try:
                tl = build_timeline(conn, self._concept)
            finally:
                conn.close()
            self.finished_ok.emit(tl)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_BLUE = "#8FA1FF"
_COLOR_RED = "#FF6B6B"


class EvolutionDialog(QDialog):
    """Vertical timeline of mentions for one concept.

    Citation links use the same 'msg:ID' / 'vid:ID' scheme as the chat
    transcript; the parent widget supplies a click handler via
    ``on_citation_clicked`` so we route to the same destinations
    (detail dialog for msg/doc, browser for vid).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        concept: str,
        on_citation_clicked: Callable[[str, str], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._concept = concept
        self._on_citation = on_citation_clicked
        self.setWindowTitle(f"How Tom's framing of {concept} evolved")
        self.resize(900, 720)
        self._build_ui()
        self._load_timeline()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        self._heading = QLabel(f"Evolution of <b>{html.escape(self._concept)}</b>")
        self._heading.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-size: 16px;"
        )
        outer.addWidget(self._heading)
        self._subhead = QLabel("")
        self._subhead.setStyleSheet(f"color: {_COLOR_DIM}; font-size: 11px;")
        outer.addWidget(self._subhead)

        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.setOpenLinks(False)
        self._body.anchorClicked.connect(self._on_anchor)
        self._body.setStyleSheet(
            f"QTextBrowser {{ background: {_COLOR_BG}; color: {_COLOR_TEXT};"
            f" border: 1px solid #3F4147; border-radius: 8px;"
            f" padding: 10px 14px; font-size: 12px;"
            f" selection-background-color: #5865F2; }}"
        )
        outer.addWidget(self._body, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        close.setStyleSheet(
            f"QPushButton {{ background: {_COLOR_CARD}; color: {_COLOR_TEXT};"
            f" padding: 6px 16px; border: 1px solid #3F4147;"
            f" border-radius: 4px; }}"
            f"QPushButton:hover {{ color: white; }}"
        )
        btn_row.addWidget(close)
        outer.addLayout(btn_row)

    def _load_timeline(self) -> None:
        """Kick off the timeline build on a background QThread. Without
        this, the build runs on the UI thread and the dialog flashes
        'Not Responding' for several seconds on large corpora."""
        self._subhead.setText(f"Loading timeline for {self._concept}…")
        self._body.setHtml(
            f"<div style='color:{_COLOR_DIM}; padding: 24px 4px;'>"
            f"Scanning Discord posts and video transcripts for "
            f"<b>{html.escape(self._concept)}</b>. This usually takes "
            f"a few seconds on a large corpus.</div>"
        )
        self._worker = _TimelineWorker(self._concept, parent=self)
        self._worker.finished_ok.connect(self._render)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    def _on_worker_failed(self, err: str) -> None:
        self._subhead.setText("")
        self._body.setHtml(
            f"<p style='color:{_COLOR_RED};'>Failed to build timeline: "
            f"{html.escape(err)}</p>"
        )

    def _render(self, tl: EvolutionTimeline) -> None:
        if not tl.buckets:
            self._subhead.setText("No Discord or video mentions found.")
            self._body.setHtml(
                f"<p style='color:{_COLOR_DIM};'>"
                f"Tom doesn't appear to have posted publicly about "
                f"<b>{html.escape(tl.concept)}</b> in the indexed Discord "
                f"corpus or the TomTube transcripts we have so far.</p>"
                f"<p style='color:{_COLOR_DIM};'>"
                f"Try a different spelling or a related concept. Evolution "
                f"view works best on concepts Tom repeats across time "
                f"(e.g. NVPOC, IBH, micro structure).</p>"
            )
            return

        span = f"{tl.buckets[0][0]} → {tl.buckets[-1][0]}"
        self._subhead.setText(
            f"{tl.total_hits:,} mentions across {len(tl.buckets)} quarters "
            f"({span}). Each bucket shows up to 3 representative posts / "
            f"video chunks. Click a citation to open the source."
        )

        parts: list[str] = []
        for q_label, items in tl.buckets:
            parts.append(
                f'<div style="margin: 18px 0 6px 0; padding-left: 6px;'
                f' border-left: 3px solid {_COLOR_GOLD};'
                f' font-size: 13px; font-weight: 600; color: {_COLOR_GOLD};">'
                f'{html.escape(q_label)}'
                f' <span style="color: {_COLOR_DIM}; font-weight: 400;'
                f' font-size: 11px;">'
                f'&nbsp;&nbsp;·&nbsp;&nbsp;{len(items)} shown</span></div>'
            )
            for h in items:
                if h.source_type == "video_chunk":
                    pill_fg = _COLOR_RED
                    pill_bg = "rgba(255,77,77,0.14)"
                    pill_label = "▶ video"
                else:
                    pill_fg = _COLOR_BLUE
                    pill_bg = "rgba(88,101,242,0.14)"
                    pill_label = "💬 Discord"
                parts.append(
                    f'<div style="margin: 6px 0 10px 22px; padding: 10px 12px;'
                    f' background: {_COLOR_CARD}; border-radius: 6px;">'
                    f'<div style="display: inline-block; margin-bottom: 4px;">'
                    f'<a href="{html.escape(h.citation_id)}" '
                    f'style="color: {pill_fg}; text-decoration: none; '
                    f'background: {pill_bg}; padding: 2px 8px;'
                    f' border-radius: 4px; font-size: 11px;">'
                    f'{pill_label}  ·  {html.escape(h.title)}</a></div>'
                    f'<div style="margin-top: 4px; color: {_COLOR_TEXT};'
                    f' line-height: 1.5;">{html.escape(h.preview)}</div>'
                    f'</div>'
                )
        self._body.setHtml("".join(parts))

    def _on_anchor(self, url: QUrl) -> None:
        href = url.toString()
        # vid: citations always route to youtube.com; others delegate.
        if href.startswith("vid:"):
            self._open_video_citation(href[len("vid:"):])
            return
        if ":" in href and self._on_citation:
            kind, raw = href.split(":", 1)
            self._on_citation(kind, raw)

    def _open_video_citation(self, chunk_id_str: str) -> None:
        try:
            cid = int(chunk_id_str)
        except ValueError:
            return
        row = self._conn.execute(
            """
            SELECT v.url AS url, c.start_sec AS ss
              FROM video_chunks c JOIN videos v ON v.id = c.video_id
             WHERE c.id = ?
            """,
            (cid,),
        ).fetchone()
        if row is None:
            return
        from tomslab.chat import _youtube_link
        open_browser(_youtube_link(row["url"] or "", float(row["ss"] or 0.0)))
