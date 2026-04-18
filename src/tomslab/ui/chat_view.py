"""Ask Tom — conversational RAG chat UI.

Shows a transcript with citation links that jump to the original
message or PDF page.  Chat history is in-memory per session.
"""
from __future__ import annotations

import html
import re
import sqlite3

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tomslab.chat import AnswerResult, ChatTurn, CITATION_RE
from tomslab.ui.chat_worker import ChatWorker


SAMPLE_PROMPTS = [
    "What is a Mean Reversion Structured Trade?",
    "How does Tom approach the opening?",
    "What does Tom mean by 'absorption at VPOC'?",
    "What is the Initial Balance and why does it matter?",
    "Show me Tom's view on overnight inventory imbalance",
]


class _InputBox(QTextEdit):
    """Text input that submits on Ctrl+Enter / Cmd+Enter."""

    submit = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        mods = event.modifiers()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
            mods & Qt.KeyboardModifier.ControlModifier
            or mods & Qt.KeyboardModifier.MetaModifier
        ):
            self.submit.emit()
            return
        super().keyPressEvent(event)


class ChatView(QWidget):
    """Ask Tom chat widget.

    Emits ``citation_clicked(kind, raw_id)`` so the host MainWindow can
    navigate to the original Discord message or PDF page.
    """

    citation_clicked = pyqtSignal(str, str)   # ('msg'|'doc', id)

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._history: list[ChatTurn] = []
        self._last_answer_sources: list = []
        self._worker: ChatWorker | None = None

        self._build_ui()
        self._render_empty_state()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._transcript = QTextBrowser()
        self._transcript.setOpenExternalLinks(False)
        self._transcript.setOpenLinks(False)
        self._transcript.anchorClicked.connect(self._on_anchor_clicked)
        self._transcript.setStyleSheet(
            "QTextBrowser { background: #1E1F22; color: #DBDEE1; border: none; "
            " padding: 12px; font-size: 13px; }"
        )
        outer.addWidget(self._transcript, stretch=1)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #949BA4; padding-left: 12px;")
        outer.addWidget(self._status)

        # --- input row ------------------------------------------------
        input_row = QHBoxLayout()
        input_row.setContentsMargins(8, 0, 8, 8)

        self._input = _InputBox()
        self._input.setPlaceholderText(
            "Ask Tom anything about his framework — Ctrl+Enter to send"
        )
        self._input.setFixedHeight(80)
        self._input.setStyleSheet(
            "QTextEdit { background: #2B2D31; color: #DBDEE1;"
            " border: 1px solid #3F4147; border-radius: 8px; padding: 6px 10px; }"
            "QTextEdit:focus { border: 1px solid #5865F2; }"
        )
        self._input.submit.connect(self._on_send)
        input_row.addWidget(self._input, stretch=1)

        button_col = QVBoxLayout()
        self._send_btn = QPushButton("Ask")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setStyleSheet(
            "QPushButton { background: #5865F2; color: white; padding: 8px 18px;"
            " border: none; border-radius: 6px; font-weight: 600; }"
            "QPushButton:disabled { background: #3F4147; color: #949BA4; }"
            "QPushButton:hover:!disabled { background: #4752C4; }"
        )
        button_col.addWidget(self._send_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self.clear_history)
        self._clear_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #949BA4; padding: 6px 14px;"
            " border: 1px solid #3F4147; border-radius: 6px; }"
            "QPushButton:hover { color: #DBDEE1; }"
        )
        button_col.addWidget(self._clear_btn)
        button_col.addStretch(1)
        input_row.addLayout(button_col)

        outer.addLayout(input_row)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _render_empty_state(self) -> None:
        prompts = "".join(
            f'<li><a href="sample:{html.escape(p)}" style="color: #6AA1FF; '
            f'text-decoration:none;">{html.escape(p)}</a></li>'
            for p in SAMPLE_PROMPTS
        )
        body = (
            f'<div style="max-width: 720px; margin: 40px auto; color: #949BA4;">'
            f'<h2 style="color: #DBDEE1; font-weight: 600;">Ask Tom</h2>'
            f'<p>Type a question and I\'ll retrieve the best matches from '
            f'Tom\'s Discord posts, his authored PDFs, and the reference books. '
            f'Answers come with <code>[msg:ID]</code> and <code>[doc:ID]</code> '
            f'citations you can click to jump to the source.</p>'
            f'<p style="color: #DBDEE1; margin-top: 18px;">Try one of these:</p>'
            f'<ul>{prompts}</ul>'
            f'</div>'
        )
        self._transcript.setHtml(body)

    def _render_transcript(self) -> None:
        parts = []
        for turn in self._history:
            if turn.role == "user":
                parts.append(self._render_user(turn.content))
            else:
                parts.append(self._render_assistant(turn.content))
        self._transcript.setHtml("\n".join(parts))
        # scroll to bottom
        cur = self._transcript.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self._transcript.setTextCursor(cur)

    def _render_user(self, text: str) -> str:
        safe = html.escape(text).replace("\n", "<br>")
        return (
            '<div style="margin: 16px 0 8px 0;">'
            '<div style="color: #6AA1FF; font-weight: 600; margin-bottom: 4px;">You</div>'
            f'<div style="color: #DBDEE1;">{safe}</div>'
            '</div>'
        )

    def _render_assistant(self, text: str) -> str:
        body = self._linkify_citations(text)
        return (
            '<div style="margin: 16px 0 8px 0; padding: 10px 14px;'
            ' background: #2B2D31; border-left: 3px solid #FFC857;'
            ' border-radius: 6px;">'
            '<div style="color: #FFC857; font-weight: 600; margin-bottom: 4px;">Tom\'s Lab</div>'
            f'<div style="color: #DBDEE1; white-space: pre-wrap;">{body}</div>'
            '</div>'
        )

    @staticmethod
    def _linkify_citations(text: str) -> str:
        """Turn [msg:ID] and [doc:ID] into HTML anchors. Escapes the rest."""
        out: list[str] = []
        last_end = 0
        for m in CITATION_RE.finditer(text or ""):
            start, end = m.span()
            if start > last_end:
                out.append(html.escape(text[last_end:start]))
            kind, raw = m.group(1), m.group(2)
            label = html.escape(f"[{kind}:{raw}]")
            href = f"{kind}:{raw}"
            out.append(
                f'<a href="{html.escape(href)}" '
                f'style="color: #FFC857; text-decoration: none; '
                f'background: rgba(255,200,87,0.1); padding: 1px 4px; border-radius: 3px;">{label}</a>'
            )
            last_end = end
        if last_end < len(text):
            out.append(html.escape(text[last_end:]))
        return "".join(out)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _on_send(self) -> None:
        q = self._input.toPlainText().strip()
        if not q:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._input.clear()

        self._history.append(ChatTurn(role="user", content=q))
        self._render_transcript()
        self._set_busy(True)
        self._status.setText("Thinking…")

        self._worker = ChatWorker(q, self._history[:-1], self)
        self._worker.answered.connect(self._on_answered)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_answered(self, result: AnswerResult) -> None:
        self._worker = None
        self._last_answer_sources = result.sources
        self._history.append(ChatTurn(role="assistant", content=result.answer or "(no answer)"))
        self._render_transcript()
        self._set_busy(False)
        n_cites = len(result.citations)
        n_src = len(result.sources)
        self._status.setText(
            f"{n_src} sources retrieved · {n_cites} citations · Ctrl+Enter to send"
        )

    def _on_failed(self, err: str) -> None:
        self._worker = None
        self._history.append(ChatTurn(
            role="assistant",
            content=f"Error: {err}",
        ))
        self._render_transcript()
        self._set_busy(False)
        self._status.setText("Ready · Ctrl+Enter to send")

    def _set_busy(self, busy: bool) -> None:
        self._send_btn.setEnabled(not busy)
        self._send_btn.setText("Thinking…" if busy else "Ask")
        self._input.setReadOnly(busy)

    def clear_history(self) -> None:
        self._history = []
        self._render_empty_state()
        self._status.setText("")

    # ------------------------------------------------------------------
    # citation clicks
    # ------------------------------------------------------------------
    def _on_anchor_clicked(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("sample:"):
            self._input.setPlainText(href[len("sample:"):])
            self._input.setFocus()
            return
        m = re.match(r"^(msg|doc):(.+)$", href)
        if not m:
            return
        self.citation_clicked.emit(m.group(1), m.group(2))
