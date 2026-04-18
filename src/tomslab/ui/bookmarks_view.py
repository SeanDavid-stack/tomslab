"""Bookmarks tab — saved Discord messages and saved Ask-Tom answers."""
from __future__ import annotations

import html
import sqlite3

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tomslab import bookmarks as bm


_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_BLUE = "#6AA1FF"
_COLOR_BORDER = "#3F4147"


class BookmarksView(QWidget):
    """Two sub-tabs: saved messages + saved chat answers."""

    message_activated = pyqtSignal(str)   # click a saved message -> jump to feed
    citation_clicked = pyqtSignal(str, str)   # click a citation inside a saved answer
    chat_bookmark_deleted = pyqtSignal()

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 0; }}"
            f"QTabBar::tab {{ background: {_COLOR_BG}; color: {_COLOR_DIM};"
            f" padding: 6px 14px; border: none; }}"
            f"QTabBar::tab:selected {{ background: {_COLOR_CARD}; color: {_COLOR_TEXT}; }}"
        )

        # messages sub-tab
        self._msg_browser = _make_browser()
        self._msg_browser.anchorClicked.connect(self._on_msg_anchor)
        self._tabs.addTab(self._msg_browser, "Starred messages")

        # answers sub-tab
        self._answer_browser = _make_browser()
        self._answer_browser.anchorClicked.connect(self._on_answer_anchor)
        self._tabs.addTab(self._answer_browser, "Saved answers")

        outer.addWidget(self._tabs, stretch=1)

    # ------------------------------------------------------------------
    # reload
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._render_messages()
        self._render_answers()

    def _render_messages(self) -> None:
        bookmarks = bm.list_message_bookmarks(self._conn)
        if not bookmarks:
            self._msg_browser.setHtml(
                _empty_state(
                    "No starred messages yet.",
                    "Click the ☆ on any Discord message in the Feed to star it.",
                )
            )
            return

        ids = [b.message_id for b in bookmarks]
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"""
            SELECT id, author_nickname, author_name, timestamp, content,
                   is_featured_speaker
              FROM messages WHERE id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        by_id = {r["id"]: r for r in rows}

        parts = [_title("Starred messages", f"{len(bookmarks)} saved")]
        for b in bookmarks:
            r = by_id.get(b.message_id)
            if r is None:
                continue
            author = r["author_nickname"] or r["author_name"] or "?"
            ts = (r["timestamp"] or "")[:19].replace("T", " ")
            accent = _COLOR_GOLD if r["is_featured_speaker"] else _COLOR_BLUE
            text = (r["content"] or "").strip()
            if not text:
                text = "(attachment-only message)"
            text_html = html.escape(text).replace("\n", "<br>")
            parts.append(_message_card(author, ts, text_html, accent, r["id"]))
        self._msg_browser.setHtml("\n".join(parts))

    def _render_answers(self) -> None:
        bookmarks = bm.list_chat_bookmarks(self._conn)
        if not bookmarks:
            self._answer_browser.setHtml(
                _empty_state(
                    "No saved answers yet.",
                    "In the Ask Tom tab, click ⭐ Save on any answer you want to keep.",
                )
            )
            return
        parts = [_title("Saved answers", f"{len(bookmarks)} saved")]
        for b in bookmarks:
            q_html = html.escape(b.question).replace("\n", "<br>")
            ans_html = html.escape(b.answer).replace("\n", "<br>")
            when = (b.created_at or "")[:16].replace("T", " ")
            parts.append(_answer_card(q_html, ans_html, when, b.id))
        self._answer_browser.setHtml("\n".join(parts))

    # ------------------------------------------------------------------
    def _on_msg_anchor(self, url) -> None:
        href = url.toString()
        if href.startswith("msg:"):
            self.message_activated.emit(href[4:])

    def _on_answer_anchor(self, url) -> None:
        href = url.toString()
        if href.startswith("delete:"):
            try:
                bid = int(href[len("delete:"):])
            except ValueError:
                return
            bm.delete_chat_bookmark(self._conn, bid)
            self.chat_bookmark_deleted.emit()
            self.reload()
            return
        # citation links (msg:ID, doc:ID) bubble up to MainWindow
        if ":" in href:
            kind, raw = href.split(":", 1)
            if kind in ("msg", "doc"):
                self.citation_clicked.emit(kind, raw)


# ---------------------------------------------------------------------------
# html helpers
# ---------------------------------------------------------------------------
def _make_browser() -> QTextBrowser:
    b = QTextBrowser()
    b.setOpenExternalLinks(False)
    b.setOpenLinks(False)
    b.setStyleSheet(
        f"QTextBrowser {{ background: {_COLOR_BG}; color: {_COLOR_TEXT};"
        f" border: none; padding: 16px 24px; font-size: 13px;"
        f" selection-background-color: #5865F2; }}"
    )
    return b


def _title(title: str, stat: str) -> str:
    return (
        f'<div style="color: {_COLOR_TEXT}; font-weight: 600; margin-bottom: 14px;'
        f' font-size: 16px;">{html.escape(title)}'
        f'<span style="color: {_COLOR_DIM}; font-weight: 400; font-size: 12px;'
        f' margin-left: 10px;">{html.escape(stat)}</span></div>'
    )


def _empty_state(title: str, sub: str) -> str:
    return (
        f'<div style="margin: 48px auto; max-width: 520px; text-align: center;'
        f' color: {_COLOR_DIM};">'
        f'<div style="font-size: 40px; margin-bottom: 12px;">☆</div>'
        f'<div style="color: {_COLOR_TEXT}; font-weight: 600; margin-bottom: 6px;">'
        f'{html.escape(title)}</div>'
        f'<div>{html.escape(sub)}</div></div>'
    )


def _message_card(author: str, ts: str, text_html: str, accent: str, mid: str) -> str:
    safe_author = html.escape(author)
    return (
        f'<div style="margin: 14px 0; padding: 12px 16px; background: {_COLOR_CARD};'
        f' border-left: 3px solid {accent}; border-radius: 8px;">'
        f'<div style="color: {accent}; font-weight: 600;">{safe_author}'
        f'<span style="color: {_COLOR_DIM}; font-weight: 400; margin-left: 10px;'
        f' font-size: 11px;">{html.escape(ts)}</span></div>'
        f'<div style="color: {_COLOR_TEXT}; margin-top: 4px; white-space: pre-wrap;">'
        f'{text_html}</div>'
        f'<div style="margin-top: 8px;">'
        f'<a href="msg:{html.escape(mid)}" style="color: {_COLOR_BLUE};'
        f' text-decoration: none; font-size: 11px;">→ open in timeline</a>'
        f'</div></div>'
    )


def _answer_card(q_html: str, ans_html: str, when: str, bid: int) -> str:
    return (
        f'<div style="margin: 14px 0; padding: 12px 16px; background: {_COLOR_CARD};'
        f' border-left: 3px solid {_COLOR_GOLD}; border-radius: 8px;">'
        f'<div style="color: {_COLOR_BLUE}; font-weight: 600; font-size: 12px;">You asked'
        f'<span style="color: {_COLOR_DIM}; font-weight: 400; margin-left: 10px;'
        f' font-size: 11px;">{html.escape(when)}</span></div>'
        f'<div style="color: {_COLOR_TEXT}; margin-top: 4px; font-size: 13px;">{q_html}</div>'
        f'<div style="color: {_COLOR_GOLD}; font-weight: 600; font-size: 12px;'
        f' margin-top: 10px;">Tom\'s Lab answered</div>'
        f'<div style="color: {_COLOR_TEXT}; margin-top: 4px; white-space: pre-wrap;'
        f' line-height: 1.55;">{ans_html}</div>'
        f'<div style="margin-top: 10px;">'
        f'<a href="delete:{bid}" style="color: {_COLOR_DIM}; text-decoration: none;'
        f' font-size: 11px;">× remove from saved</a></div>'
        f'</div>'
    )
