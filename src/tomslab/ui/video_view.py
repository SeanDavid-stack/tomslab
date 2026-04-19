"""TomTube tab — browse indexed Tom B YouTube videos + their transcript chunks.

Left pane: list of videos (title + duration + status).
Right pane: chunks of the selected video with timestamps; each chunk has a
"▶ open at 14:32 on YouTube" link that launches the browser at that second.
"""
from __future__ import annotations

import sqlite3
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tomslab.chat import _fmt_timestamp, _youtube_link


_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_BORDER = "#3F4147"


class TomTubeView(QWidget):
    """Browse indexed Tom B videos; open timestamps on YouTube."""

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._current_video_id: str | None = None
        self._current_video_url: str = ""
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(
            f"QSplitter::handle {{ background: {_COLOR_BORDER}; width: 1px; }}"
        )

        # --- left: video list ----------------------------------------
        left = QWidget()
        left.setStyleSheet(f"background: {_COLOR_CARD};")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 10, 8, 10)
        left_lay.setSpacing(6)

        title = QLabel("Tom's videos")
        title.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-weight: 600; font-size: 12px; padding: 4px 6px;"
        )
        left_lay.addWidget(title)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {_COLOR_TEXT};"
            f" border: none; padding: 4px; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 8px 8px; border-radius: 6px; }}"
            f"QListWidget::item:hover {{ background: {_COLOR_BG}; }}"
            f"QListWidget::item:selected {{ background: #3A3320; color: {_COLOR_GOLD}; }}"
        )
        self._list.currentItemChanged.connect(self._on_video_selected)
        left_lay.addWidget(self._list, stretch=1)
        split.addWidget(left)

        # --- right: transcript chunks --------------------------------
        right = QWidget()
        right.setStyleSheet(f"background: {_COLOR_BG};")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(12, 10, 12, 10)
        right_lay.setSpacing(6)

        self._heading = QLabel("")
        self._heading.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-weight: 600; font-size: 14px;"
        )
        right_lay.addWidget(self._heading)
        self._subheading = QLabel("")
        self._subheading.setStyleSheet(f"color: {_COLOR_DIM}; font-size: 11px;")
        right_lay.addWidget(self._subheading)
        self._timestamp_note = QLabel(
            "<i>Note: ▶ timestamps land at the start of a ~90 second window. "
            "Listen forward from that mark — Tom's cited phrase is somewhere "
            "within the next minute or so.</i>"
        )
        self._timestamp_note.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 10px; padding: 4px 2px 6px 2px;"
        )
        self._timestamp_note.setWordWrap(True)
        right_lay.addWidget(self._timestamp_note)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor)
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background: {_COLOR_BG}; color: {_COLOR_TEXT};"
            f" border: none; padding: 8px 4px; font-size: 12px;"
            f" selection-background-color: #5865F2; }}"
        )
        right_lay.addWidget(self._browser, stretch=1)

        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, stretch=1)

    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        rows = self._conn.execute(
            """
            SELECT v.id, v.title, v.duration_sec, v.transcript_status, v.url,
                   (SELECT COUNT(*) FROM video_chunks c WHERE c.video_id = v.id) AS n_chunks
              FROM videos v
             ORDER BY v.added_at DESC
            """
        ).fetchall()
        status_glyphs = {
            "transcribed": "✅",
            "downloaded": "⏳",
            "pending": "·",
            "failed": "⚠",
        }
        for r in rows:
            dur_min = int((r["duration_sec"] or 0) // 60)
            icon = status_glyphs.get(r["transcript_status"] or "pending", "·")
            line1 = r["title"] or r["id"]
            line2 = (
                f"     {icon}  {dur_min} min  ·  "
                f"{r['n_chunks']} chunks  ·  {r['transcript_status'] or 'pending'}"
            )
            item = QListWidgetItem(f"{line1}\n{line2}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, r["url"])
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._heading.setText("No videos ingested yet")
            self._subheading.setText(
                "Use File → Import YouTube (TomTube)… to scrape Tom B's videos."
            )
            self._browser.setHtml("")

    # ------------------------------------------------------------------
    def _on_video_selected(self, current, _previous) -> None:
        if current is None:
            self._current_video_id = None
            self._current_video_url = ""
            self._browser.setHtml("")
            return
        vid = current.data(Qt.ItemDataRole.UserRole)
        url = current.data(Qt.ItemDataRole.UserRole + 1) or ""
        self._current_video_id = vid
        self._current_video_url = url
        row = self._conn.execute(
            "SELECT title, duration_sec, transcript_status, summary FROM videos WHERE id=?",
            (vid,),
        ).fetchone()
        if row is None:
            return
        dur_min = int((row["duration_sec"] or 0) // 60)
        self._heading.setText(row["title"] or vid)
        self._subheading.setText(
            f"{dur_min} min  ·  status: {row['transcript_status']}  ·  "
            f"<a href='{_youtube_link(url, 0)}' style='color:#6AA1FF;'>open on YouTube</a>"
        )
        # Using setTextFormat to let subheading render the link
        self._subheading.setTextFormat(Qt.TextFormat.RichText)
        self._subheading.setOpenExternalLinks(True)

        chunks = self._conn.execute(
            "SELECT chunk_index, start_sec, end_sec, text FROM video_chunks "
            "WHERE video_id=? ORDER BY chunk_index",
            (vid,),
        ).fetchall()
        parts: list[str] = []
        for c in chunks:
            ts = _fmt_timestamp(c["start_sec"])
            ytl = _youtube_link(url, c["start_sec"])
            text = (c["text"] or "").replace("\n", " ")
            parts.append(
                f'<div style="margin: 10px 0; padding: 8px 12px;'
                f' background: {_COLOR_CARD}; border-left: 3px solid {_COLOR_GOLD};'
                f' border-radius: 6px;">'
                f'<div style="font-size: 11px; color: {_COLOR_DIM};">'
                f'<a href="{ytl}" style="color: {_COLOR_GOLD}; text-decoration: none;">'
                f'▶ {ts}</a>   ·   open on YouTube at this moment'
                f'</div>'
                f'<div style="margin-top: 4px;">{text}</div></div>'
            )
        if not parts:
            parts.append(
                f'<div style="color: {_COLOR_DIM}; margin-top: 24px;">'
                f'This video hasn\'t been transcribed yet.</div>'
            )
        self._browser.setHtml("\n".join(parts))

    # ------------------------------------------------------------------
    def _on_anchor(self, url) -> None:
        """Clicked on a ▶ timestamp link → open in the user's default browser."""
        href = url.toString()
        if href.startswith("http"):
            webbrowser.open(href)
