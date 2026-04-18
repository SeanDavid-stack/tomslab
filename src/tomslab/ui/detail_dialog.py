"""Detail popover for Ask-Tom citations.

Shows the full text of a cited Discord message or PDF page without
jumping the user away from the chat.  A "Show in timeline" button is
provided for when they DO want the surrounding conversation.
"""
from __future__ import annotations

import hashlib
import html
import sqlite3
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


# Keep the palette in sync with MessageDelegate.
_COLOR_BG     = "#1E1F22"
_COLOR_CARD   = "#2B2D31"
_COLOR_TEXT   = "#DBDEE1"
_COLOR_DIM    = "#949BA4"
_COLOR_GOLD   = "#FFC857"
_COLOR_BLUE   = "#6AA1FF"
_COLOR_BORDER = "#3F4147"


_AVATAR_PALETTE = [
    "#5865F2", "#3498DB", "#1ABC9C", "#2ECC71", "#F39C12", "#E67E22",
    "#E74C3C", "#E91E63", "#9B59B6", "#7289DA", "#11806A", "#1F8B4C",
]


def _avatar_color(name: str) -> str:
    h = int(hashlib.md5((name or "?").lower().encode("utf-8")).hexdigest(), 16)
    return _AVATAR_PALETTE[h % len(_AVATAR_PALETTE)]


class DetailDialog(QDialog):
    """Reusable popover — use :meth:`show_message` or :meth:`show_doc_page`.

    Emits ``jump_to_message(message_id)`` when the user clicks
    "Show in timeline" from a Discord-message view, and
    ``open_image(path)`` when they click the attached chart thumb.
    """

    jump_to_message = pyqtSignal(str)
    open_image = pyqtSignal(str)

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(640, 540)
        self.setStyleSheet(
            f"QDialog {{ background: {_COLOR_BG}; color: {_COLOR_TEXT}; }}"
        )

        self._current_message_id: str | None = None
        self._current_attachment_paths: list[str] = []

        self._build_ui()
        QShortcut(QKeySequence("Esc"), self, self.close)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- header (author / page / date) --------------------------------
        self._header = QLabel("")
        self._header.setTextFormat(Qt.TextFormat.RichText)
        self._header.setStyleSheet(
            f"QLabel {{ background: {_COLOR_CARD};"
            f" border-bottom: 1px solid {_COLOR_BORDER};"
            f" padding: 14px 18px; font-size: 13px; color: {_COLOR_TEXT}; }}"
        )
        outer.addWidget(self._header)

        # -- scrollable body (text + thumbs) ------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {_COLOR_BG}; }}")

        self._body = QWidget()
        self._body.setStyleSheet(f"QWidget {{ background: {_COLOR_BG}; }}")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(22, 16, 22, 16)
        self._body_lay.setSpacing(10)

        self._text_label = QLabel("")
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._text_label.setStyleSheet(
            f"QLabel {{ color: {_COLOR_TEXT}; font-size: 14px;"
            f" line-height: 150%; }}"
        )
        self._body_lay.addWidget(self._text_label)

        self._thumbs_host = QWidget()
        self._thumbs_lay = QHBoxLayout(self._thumbs_host)
        self._thumbs_lay.setContentsMargins(0, 8, 0, 0)
        self._thumbs_lay.setSpacing(8)
        self._body_lay.addWidget(self._thumbs_host)
        self._body_lay.addStretch(1)

        scroll.setWidget(self._body)
        outer.addWidget(scroll, stretch=1)

        # -- action bar ---------------------------------------------------
        bar = QHBoxLayout()
        bar.setContentsMargins(14, 10, 14, 12)

        self._jump_btn = QPushButton("Show in timeline")
        self._jump_btn.setStyleSheet(
            f"QPushButton {{ background: #5865F2; color: white;"
            f" padding: 8px 16px; border: none; border-radius: 7px;"
            f" font-weight: 600; }}"
            f"QPushButton:hover {{ background: #4752C4; }}"
            f"QPushButton:disabled {{ background: {_COLOR_BORDER}; color: {_COLOR_DIM}; }}"
        )
        self._jump_btn.clicked.connect(self._on_jump)
        bar.addWidget(self._jump_btn)

        bar.addStretch(1)

        close = QPushButton("Close")
        close.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_COLOR_DIM};"
            f" padding: 8px 14px; border: 1px solid {_COLOR_BORDER};"
            f" border-radius: 7px; }}"
            f"QPushButton:hover {{ color: {_COLOR_TEXT}; }}"
        )
        close.clicked.connect(self.close)
        bar.addWidget(close)

        bar_wrap = QWidget()
        bar_wrap.setStyleSheet(
            f"QWidget {{ background: {_COLOR_CARD};"
            f" border-top: 1px solid {_COLOR_BORDER}; }}"
        )
        bar_wrap.setLayout(bar)
        outer.addWidget(bar_wrap)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def show_message(self, message_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT m.id, m.author_nickname, m.author_name, m.timestamp, m.content,
                   m.is_featured_speaker, m.reply_to_message_id AS reply_id,
                   parent.author_nickname AS reply_author_nick,
                   parent.author_name     AS reply_author_name,
                   SUBSTR(COALESCE(parent.content, ''), 1, 180) AS reply_snippet
              FROM messages m
              LEFT JOIN messages parent ON parent.id = m.reply_to_message_id
             WHERE m.id = ?
            """,
            (message_id,),
        ).fetchone()
        if not row:
            self._set_error(f"Message {message_id} not found in this database.")
            self._current_message_id = None
            self._jump_btn.setEnabled(False)
            self._open_if_hidden()
            return False

        # attachments
        attachments = self._conn.execute(
            "SELECT filename, local_path FROM attachments WHERE message_id = ?",
            (message_id,),
        ).fetchall()

        self._current_message_id = row["id"]
        self.setWindowTitle(f"{row['author_nickname'] or row['author_name']} · {row['timestamp'][:19].replace('T',' ')}")

        display = row["author_nickname"] or row["author_name"] or "?"
        is_tom = bool(row["is_featured_speaker"])
        accent = _COLOR_GOLD if is_tom else _avatar_color(row["author_name"] or display)

        reply_block = ""
        if row["reply_id"]:
            rnick = row["reply_author_nick"] or row["reply_author_name"] or ""
            rsnip = (row["reply_snippet"] or "").strip().replace("\n", " ")
            if len(rsnip) > 140:
                rsnip = rsnip[:137] + "…"
            reply_block = (
                f'<div style="color: {_COLOR_DIM}; font-size: 11px;'
                f' border-left: 2px solid {_COLOR_BORDER}; padding-left: 8px;'
                f' margin-bottom: 6px;">↪ replying to <b>{html.escape(rnick)}</b>: '
                f'{html.escape(rsnip)}</div>'
            )

        pinned = " 📌" if row_get(row, "is_pinned", 0) else ""
        header_html = (
            f"{reply_block}"
            f'<span style="display:inline-block; background:{accent};'
            f' color:{_pick_text_color(accent)};'
            f' width:30px; height:30px; border-radius:15px;'
            f' text-align:center; line-height:30px; font-weight:700; margin-right:10px;">'
            f"{html.escape(_initial(display))}</span>"
            f'<span style="color:{accent}; font-weight:600;">{html.escape(display)}</span>'
            f'<span style="color:{_COLOR_DIM}; margin-left:10px;'
            f' font-size:11px;">{html.escape(row["timestamp"][:19].replace("T", " "))}{pinned}</span>'
        )
        self._header.setText(header_html)

        content = (row["content"] or "").strip()
        if not content:
            self._text_label.setText(
                f'<span style="color:{_COLOR_DIM}; font-style:italic;">(no text — '
                f'attachment-only message)</span>'
            )
        else:
            self._text_label.setText(
                html.escape(content).replace("\n", "<br>")
            )

        self._populate_thumbnails(
            [(a["filename"], a["local_path"]) for a in attachments]
        )
        self._jump_btn.setEnabled(True)
        self._jump_btn.setText("Show in timeline")
        self._open_if_hidden()
        return True

    def show_doc_page(self, page_id: int) -> bool:
        row = self._conn.execute(
            """
            SELECT p.page_num, p.rendered_path,
                   COALESCE(NULLIF(p.ocr_text,''), p.extracted_text) AS text,
                   d.title, d.filename, d.author
              FROM document_pages p
              JOIN documents d ON d.id = p.document_id
             WHERE p.id = ?
            """,
            (page_id,),
        ).fetchone()
        if not row:
            self._set_error(f"Doc page {page_id} not found.")
            self._current_message_id = None
            self._jump_btn.setEnabled(False)
            self._open_if_hidden()
            return False

        self._current_message_id = None   # no timeline for docs
        title = row["title"] or row["filename"] or "Document"
        self.setWindowTitle(f"📄 {title} · page {row['page_num']}")

        who = "Tom B" if row["author"] == "tom_b" else (row["title"] or "Reference")
        header_html = (
            f'<span style="display:inline-block; background:{_COLOR_BLUE};'
            f' color:white; width:30px; height:30px; border-radius:15px;'
            f' text-align:center; line-height:30px; font-weight:700; margin-right:10px;">📄</span>'
            f'<span style="color:{_COLOR_BLUE}; font-weight:600;">{html.escape(title)}</span>'
            f'<span style="color:{_COLOR_DIM}; margin-left:10px; font-size:11px;">'
            f'page {int(row["page_num"])} · {html.escape(who)}</span>'
        )
        self._header.setText(header_html)

        text = (row["text"] or "").strip() or "(no text extracted from this page)"
        self._text_label.setText(html.escape(text).replace("\n", "<br>"))

        # Show the rendered page as a thumbnail — clickable to open full-size.
        thumbs = []
        if row["rendered_path"]:
            thumbs.append((f"page_{int(row['page_num']):04d}.png", row["rendered_path"]))
        self._populate_thumbnails(thumbs)

        # Repurpose the "timeline" button as "Open full page"
        self._jump_btn.setEnabled(bool(row["rendered_path"]))
        self._jump_btn.setText("Open full page")
        self._jump_target_path = row["rendered_path"] or ""
        self._open_if_hidden()
        return True

    # ------------------------------------------------------------------
    def _populate_thumbnails(self, items: list[tuple[str, str]]) -> None:
        # clear existing
        while self._thumbs_lay.count():
            it = self._thumbs_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._current_attachment_paths = []
        shown = 0
        for fn, path in items:
            if not path:
                continue
            p = Path(path)
            if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                continue
            if not p.exists():
                continue
            pix = QPixmap(str(p))
            if pix.isNull():
                continue
            thumb = pix.scaled(
                240, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            btn = QPushButton()
            btn.setIcon(self._pixmap_to_icon(thumb))
            btn.setIconSize(thumb.size())
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Click to open full-size\n{fn}")
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; padding: 0;"
                f" border: 1px solid {_COLOR_BORDER}; border-radius: 6px; }}"
                f"QPushButton:hover {{ border: 1px solid {_COLOR_GOLD}; }}"
            )
            btn.clicked.connect(lambda _chk, pp=str(p): self.open_image.emit(pp))
            self._thumbs_lay.addWidget(btn)
            self._current_attachment_paths.append(str(p))
            shown += 1
            if shown >= 4:
                break
        self._thumbs_lay.addStretch(1)
        self._thumbs_host.setVisible(shown > 0)

    @staticmethod
    def _pixmap_to_icon(pix):
        from PyQt6.QtGui import QIcon
        return QIcon(pix)

    def _set_error(self, msg: str) -> None:
        self._header.setText(f'<span style="color:{_COLOR_DIM};">Not found</span>')
        self._text_label.setText(msg)
        self._populate_thumbnails([])

    def _on_jump(self) -> None:
        # For Discord messages: jump to timeline.
        if self._current_message_id:
            self.jump_to_message.emit(self._current_message_id)
            self.close()
            return
        # For doc pages we reuse the button as "Open full page".
        path = getattr(self, "_jump_target_path", "")
        if path:
            self.open_image.emit(path)

    def _open_if_hidden(self) -> None:
        if not self.isVisible():
            self.show()
        else:
            self.raise_()
            self.activateWindow()


def row_get(row, key, default=None):
    try:
        return row[key]
    except Exception:
        return default


def _initial(name: str) -> str:
    for ch in (name or "?").strip():
        if ch.isalpha() or ch.isdigit():
            return ch.upper()
    return "?"


def _pick_text_color(bg_hex: str) -> str:
    # Roughly readable — dark text on light bg, light text on dark bg.
    try:
        r = int(bg_hex[1:3], 16)
        g = int(bg_hex[3:5], 16)
        b = int(bg_hex[5:7], 16)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#111214" if lum > 0.6 else "#FFFFFF"
    except Exception:
        return "#FFFFFF"
