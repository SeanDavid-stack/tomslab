"""Ask Tom — conversational RAG chat UI.

Shows a transcript with citation links that jump to the original
message or PDF page.  Chat history is in-memory per session.
Includes a "Did you mean?" interactive spell-correction banner and
friendly error rendering for transient provider hiccups.
"""
from __future__ import annotations

import hashlib
import html
import re
import sqlite3

import tempfile
import time
from pathlib import Path
from PyQt6.QtCore import QMimeData, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QPixmap, QTextCursor
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tomslab import bookmarks as bmmod
from tomslab import chat as chatmod
from tomslab import db as dbmod
from tomslab.ai import registry as aireg
from tomslab.chat import AnswerResult, ChatTurn, CITATION_RE
from tomslab.ui.chat_worker import ChatWorker


SAMPLE_PROMPTS = [
    # Concepts & definitions
    "What is a Mean Reversion Structured Trade?",
    "What does Tom mean by 'absorption at VPOC'?",
    "Explain Naked VPOC (NVPOC) and why Tom watches for it.",
    "What's the difference between initiative and responsive activity?",
    # Process / playbook
    "How does Tom approach the opening?",
    "Walk me through Tom's Initial Balance (IB) playbook.",
    "How does Tom handle overnight inventory imbalance?",
    "What does Tom's Opening Context Alignment look like in practice?",
    # Setups & tactics
    "What are the conditions for an HVN break-and-reject setup?",
    "How does Tom use VWAP and the Volume Profile together?",
    "When does Tom fade an IB extension vs trade the continuation?",
    # Tools / environment
    "What subscriptions do I need to replicate Tom's Investor/RT charts?",
    "How do I export market levels from Investor/RT to Bookmap cloud notes?",
    # Mindset & risk
    "Summarize Tom's risk-management rules from his posts.",
    "What does Tom say about trading psychology during drawdowns?",
]


# ---- palette (matches MessageDelegate) -------------------------------------
COLOR_BG     = "#1E1F22"
COLOR_BG_ALT = "#2B2D31"
COLOR_TEXT   = "#DBDEE1"
COLOR_DIM    = "#949BA4"
COLOR_AUTHOR_YOU = "#6AA1FF"
COLOR_AUTHOR_TOM = "#FFC857"
COLOR_BORDER = "#3F4147"
COLOR_PRIMARY = "#5865F2"


# A small palette for user-avatar auto-coloring. Not used for Tom's Lab
# (that always uses the gold accent).
_AVATAR_COLORS = [
    "#5865F2", "#E67E22", "#2ECC71", "#9B59B6", "#E91E63",
    "#1ABC9C", "#F1C40F", "#E74C3C", "#3498DB", "#95A5A6",
]


def _avatar_for(name: str) -> tuple[str, str]:
    """Deterministic (letter, hex-color) avatar for a given display name."""
    n = (name or "?").strip()
    letter = n[:1].upper() if n else "?"
    h = int(hashlib.md5(n.lower().encode("utf-8")).hexdigest(), 16)
    color = _AVATAR_COLORS[h % len(_AVATAR_COLORS)]
    return letter, color


class _InputBox(QTextEdit):
    """Text input that submits on Ctrl+Enter / Cmd+Enter, grows with content,
    and attaches clipboard images as chart uploads on Ctrl+V."""

    submit = pyqtSignal()
    image_pasted = pyqtSignal(str)       # emits path of a PNG written to disk

    _MIN_H = 64
    _MAX_H = 280

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(self._MIN_H)
        self.setMaximumHeight(self._MAX_H)
        self.document().documentLayout().documentSizeChanged.connect(self._autogrow)
        self._autogrow()

    def canInsertFromMimeData(self, source: QMimeData) -> bool:     # noqa: N802
        # Accept clipboard pastes that contain an image (normal text/html
        # pastes fall through to the default handler).
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:        # noqa: N802
        if source.hasImage():
            pix = QPixmap(source.imageData())
            if pix.isNull():
                # try via rgba tag
                img = source.imageData()
                if img is not None:
                    pix = QPixmap.fromImage(img)
            if not pix.isNull():
                path = _dump_pixmap_to_tmp(pix)
                if path:
                    self.image_pasted.emit(path)
                    return
        # Plain-text / HTML / file-URLs fall through unchanged.
        super().insertFromMimeData(source)

    def _autogrow(self) -> None:
        # Account for frame + internal padding; QTextDocument height is
        # the pixel height of the rendered text at the current width.
        doc_h = int(self.document().size().height())
        margin = 2 * (self.frameWidth() + 8)
        target = max(self._MIN_H, min(self._MAX_H, doc_h + margin))
        if target != self.height():
            self.setFixedHeight(target)

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
        self._pending_corrected: str | None = None    # used by the Did-you-mean banner
        # Supports multiple attachments per turn (e.g. HTF chart + intraday
        # Bookmap in the same question).  Added via the paperclip or paste.
        self._attachment_paths: list[str] = []

        # Elapsed-timer for the "Thinking..." state so the user always
        # sees whether something is actually happening or the call is hung.
        self._think_started: float = 0.0
        self._think_timer = QTimer(self)
        self._think_timer.setInterval(500)
        self._think_timer.timeout.connect(self._tick_thinking)

        self._build_ui()
        self._render_empty_state()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # --- top notice: what this actually is -------------------------
        # The bottom footer ("experimental · not financial advice") is too
        # easy to skim past and doesn't clarify the biggest misconception:
        # that you're talking to Tom himself. This banner leads with that.
        notice = QLabel(
            "<b>ℹ️  Independent study tool.</b>  You're <b>not asking Tom</b> — "
            "answers are generated by AI models (Gemini or local Ollama) "
            "reading <i>publicly shared</i> Discord posts and reference PDFs. "
            "Tom B has <b>not reviewed or endorsed</b> this tool. "
            "Not affiliated with Tom or Bookmap."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet(
            f"QLabel {{ background: #2A2118; color: {COLOR_TEXT};"
            f" border-left: 3px solid {COLOR_AUTHOR_TOM};"
            f" border-radius: 4px;"
            f" padding: 10px 14px; margin: 10px 16px 0 16px; font-size: 12px; }}"
        )
        outer.addWidget(notice)

        # --- quick-switch: Gemini <-> Ollama ---------------------------
        switch_row = QHBoxLayout()
        switch_row.setContentsMargins(16, 8, 16, 0)
        switch_row.setSpacing(6)
        switch_label = QLabel("Chat model:")
        switch_label.setStyleSheet(f"color: {COLOR_DIM}; font-size: 11px;")
        switch_row.addWidget(switch_label)

        self._btn_gemini = QPushButton("🌩  Gemini (cloud)")
        self._btn_ollama = QPushButton("🖥  Ollama (local)")
        for btn in (self._btn_gemini, self._btn_ollama):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: {COLOR_BG_ALT}; color: {COLOR_DIM};"
                f" padding: 4px 12px; border: 1px solid {COLOR_BORDER};"
                f" border-radius: 12px; font-size: 11px; }}"
                f"QPushButton:checked {{ background: #3A3320; color: {COLOR_AUTHOR_TOM};"
                f" border: 1px solid {COLOR_AUTHOR_TOM}; }}"
                f"QPushButton:hover {{ color: {COLOR_TEXT}; }}"
            )
        self._btn_gemini.clicked.connect(lambda: self._set_chat_provider("gemini"))
        self._btn_ollama.clicked.connect(lambda: self._set_chat_provider("ollama"))
        switch_row.addWidget(self._btn_gemini)
        switch_row.addWidget(self._btn_ollama)
        switch_row.addStretch(1)
        self._provider_hint = QLabel("")
        self._provider_hint.setStyleSheet(f"color: {COLOR_DIM}; font-size: 10px;")
        switch_row.addWidget(self._provider_hint)
        outer.addLayout(switch_row)
        self._refresh_chat_provider_buttons()

        self._transcript = QTextBrowser()
        self._transcript.setOpenExternalLinks(False)
        self._transcript.setOpenLinks(False)
        self._transcript.anchorClicked.connect(self._on_anchor_clicked)
        self._transcript.setStyleSheet(
            f"QTextBrowser {{ background: {COLOR_BG}; color: {COLOR_TEXT};"
            f" border: none; padding: 16px 24px; font-size: 13px;"
            f" selection-background-color: {COLOR_PRIMARY}; }}"
        )
        outer.addWidget(self._transcript, stretch=1)

        # Did-you-mean banner (hidden until a correction is available)
        self._banner = QFrame()
        self._banner.setStyleSheet(
            f"QFrame {{ background: #3A3320; border-left: 3px solid {COLOR_AUTHOR_TOM};"
            f" border-radius: 6px; padding: 8px 12px; margin: 0 12px; }}"
            f"QLabel {{ color: {COLOR_TEXT}; }}"
            f"QPushButton {{ background: {COLOR_AUTHOR_TOM}; color: #1E1F22;"
            f" padding: 5px 12px; border: none; border-radius: 4px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #FFD87A; }}"
            f"QPushButton#secondary {{ background: transparent;"
            f" color: {COLOR_DIM}; border: 1px solid {COLOR_BORDER}; }}"
            f"QPushButton#secondary:hover {{ color: {COLOR_TEXT}; }}"
        )
        bh = QHBoxLayout(self._banner)
        bh.setContentsMargins(10, 6, 10, 6)
        bh.setSpacing(10)
        self._banner_label = QLabel("")
        self._banner_label.setWordWrap(True)
        self._banner_use_btn = QPushButton("Use correction")
        self._banner_use_btn.clicked.connect(self._on_banner_use)
        self._banner_keep_btn = QPushButton("Send as typed")
        self._banner_keep_btn.setObjectName("secondary")
        self._banner_keep_btn.clicked.connect(self._on_banner_keep)
        bh.addWidget(self._banner_label, stretch=1)
        bh.addWidget(self._banner_use_btn)
        bh.addWidget(self._banner_keep_btn)
        self._banner.setVisible(False)
        outer.addWidget(self._banner)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {COLOR_DIM}; padding-left: 24px;")
        outer.addWidget(self._status)

        # --- attachment preview strip (hidden until any chart is attached) ---
        self._attachment_frame = QFrame()
        self._attachment_frame.setStyleSheet(
            f"QFrame {{ background: {COLOR_BG_ALT}; border: 1px solid {COLOR_BORDER};"
            f" border-radius: 8px; padding: 6px 10px; margin: 0 12px 4px 12px; }}"
            f"QLabel {{ color: {COLOR_TEXT}; }}"
            f"QPushButton {{ background: transparent; color: {COLOR_DIM};"
            f" padding: 2px 8px; border: 1px solid {COLOR_BORDER}; border-radius: 4px; }}"
            f"QPushButton:hover {{ color: {COLOR_TEXT}; }}"
        )
        self._attachment_lay = QHBoxLayout(self._attachment_frame)
        self._attachment_lay.setContentsMargins(8, 4, 8, 4)
        self._attachment_lay.setSpacing(8)
        self._attachment_label = QLabel("")
        self._attachment_lay.addWidget(self._attachment_label)
        self._attachment_lay.addStretch(1)
        self._attachment_clear = QPushButton("Remove all")
        self._attachment_clear.clicked.connect(self._clear_attachment)
        self._attachment_lay.addWidget(self._attachment_clear)
        self._attachment_frame.setVisible(False)
        outer.addWidget(self._attachment_frame)

        # --- input row ------------------------------------------------
        input_row = QHBoxLayout()
        input_row.setContentsMargins(12, 0, 12, 4)
        input_row.setSpacing(10)

        self._input = _InputBox()
        self._input.setPlaceholderText(
            "Ask Tom anything about his framework — Ctrl+Enter to send"
        )
        self._input.setStyleSheet(
            f"QTextEdit {{ background: {COLOR_BG_ALT}; color: {COLOR_TEXT};"
            f" border: 1px solid {COLOR_BORDER}; border-radius: 10px;"
            f" padding: 10px 14px; font-size: 14px; }}"
            f"QTextEdit:focus {{ border: 1px solid {COLOR_PRIMARY}; }}"
        )
        self._input.submit.connect(self._on_send)
        self._input.image_pasted.connect(self._on_image_pasted)
        input_row.addWidget(self._input, stretch=1)

        button_col = QVBoxLayout()
        button_col.setSpacing(6)

        # Paperclip → attach a chart image for multimodal analysis.
        self._attach_btn = QPushButton("📎 Attach chart")
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip(
            "Attach a Bookmap / Investor-RT screenshot so Ask Tom can\n"
            "decipher context, entry, stop, target through Tom's framework."
        )
        self._attach_btn.setStyleSheet(
            f"QPushButton {{ background: {COLOR_BG_ALT}; color: {COLOR_TEXT};"
            f" padding: 8px 14px; border: 1px solid {COLOR_BORDER};"
            f" border-radius: 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ border: 1px solid {COLOR_AUTHOR_TOM}; color: {COLOR_AUTHOR_TOM}; }}"
        )
        self._attach_btn.clicked.connect(self._on_attach_clicked)
        button_col.addWidget(self._attach_btn)

        self._send_btn = QPushButton("Ask")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {COLOR_PRIMARY}; color: white;"
            f" padding: 10px 22px; border: none; border-radius: 8px;"
            f" font-weight: 600; font-size: 13px; }}"
            f"QPushButton:disabled {{ background: {COLOR_BORDER}; color: {COLOR_DIM}; }}"
            f"QPushButton:hover:!disabled {{ background: #4752C4; }}"
        )
        button_col.addWidget(self._send_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self.clear_history)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLOR_DIM};"
            f" padding: 7px 16px; border: 1px solid {COLOR_BORDER};"
            f" border-radius: 8px; }}"
            f"QPushButton:hover {{ color: {COLOR_TEXT}; }}"
        )
        button_col.addWidget(self._clear_btn)
        button_col.addStretch(1)
        input_row.addLayout(button_col)

        outer.addLayout(input_row)

        # --- persistent disclaimer footer ------------------------------
        disclaimer = QLabel(
            "⚠️ Experimental research tool · Not financial advice · "
            "Verify everything independently · "
            "You alone are responsible for your trading decisions"
        )
        disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        disclaimer.setStyleSheet(
            f"color: {COLOR_DIM}; font-size: 10px; padding: 6px 12px 10px 12px;"
            f" background: {COLOR_BG}; border-top: 1px solid {COLOR_BORDER};"
        )
        outer.addWidget(disclaimer)

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _render_empty_state(self) -> None:
        prompts = "".join(
            f'<li style="margin: 6px 0;"><a href="sample:{html.escape(p)}" '
            f'style="color: {COLOR_AUTHOR_YOU}; text-decoration:none;">'
            f'{html.escape(p)}</a></li>'
            for p in SAMPLE_PROMPTS
        )
        body = (
            f'<div style="max-width: 720px; margin: 40px auto; color: {COLOR_DIM};">'
            f'<div style="display:inline-block; background:{COLOR_AUTHOR_TOM};'
            f' color:#1E1F22; width:48px; height:48px; border-radius:24px;'
            f' text-align:center; line-height:48px; font-weight:700;'
            f' font-size: 22px; margin-bottom: 14px;">T</div>'
            f'<h2 style="color: {COLOR_TEXT}; font-weight: 600; margin: 0 0 8px 0;">Ask Tom\'s Lab</h2>'
            f'<p>An <b>independent study tool</b> that searches Tom B\'s publicly '
            f'shared Discord posts and reference PDFs and has an AI model '
            f'synthesise an answer. Every claim comes with <code>[citation]</code> '
            f'pills that jump back to the source.</p>'
            f'<p style="color: #C9B380; margin-top: 14px;">'
            f'<b>You are not asking Tom directly.</b> Tom B has not reviewed or '
            f'endorsed this tool. Treat answers as a starting point for your own '
            f'research, never as advice.</p>'
            f'<p style="color: {COLOR_TEXT}; margin-top: 22px; font-weight: 600;">Try one:</p>'
            f'<ul style="padding-left: 18px;">{prompts}</ul>'
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

    @staticmethod
    def _avatar_html(letter: str, color: str) -> str:
        return (
            f'<span style="display:inline-block; background:{color}; color:white;'
            f' width:28px; height:28px; border-radius:14px; text-align:center;'
            f' line-height:28px; font-weight:700; font-size:13px;'
            f' margin-right:10px;">{html.escape(letter)}</span>'
        )

    def _render_user(self, text: str) -> str:
        safe = html.escape(text).replace("\n", "<br>")
        avatar = self._avatar_html("Y", COLOR_AUTHOR_YOU)
        return (
            '<div style="margin: 18px 0 10px 0;">'
            f'<div style="margin-bottom: 6px;">{avatar}'
            f'<span style="color: {COLOR_AUTHOR_YOU}; font-weight: 600;">You</span></div>'
            f'<div style="color: {COLOR_TEXT}; margin-left: 38px;">{safe}</div>'
            '</div>'
        )

    def _render_assistant(self, text: str) -> str:
        labels = self._resolve_citation_labels(text)
        body = self._linkify_citations(text, labels)
        sources_panel = self._sources_panel_html(text, labels)
        avatar = self._avatar_html("T", COLOR_AUTHOR_TOM)
        # Each assistant turn gets a "Save answer" link in its footer —
        # encodes the index in history so _on_anchor_clicked can look up
        # the right (question, answer) pair to persist.
        idx = len(self._history) - 1
        save_footer = (
            f'<div style="margin-left: 38px; margin-top: 6px;">'
            f'<a href="save:{idx}" style="color: {COLOR_DIM}; text-decoration: none;'
            f' font-size: 11px;">⭐ Save this answer</a></div>'
        )
        return (
            '<div style="margin: 18px 0 10px 0;">'
            f'<div style="margin-bottom: 6px;">{avatar}'
            f'<span style="color: {COLOR_AUTHOR_TOM}; font-weight: 600;">'
            f'Tom\'s Lab</span></div>'
            f'<div style="margin-left: 38px; padding: 12px 16px;'
            f' background: {COLOR_BG_ALT}; border-left: 3px solid {COLOR_AUTHOR_TOM};'
            f' border-radius: 8px; color: {COLOR_TEXT}; white-space: pre-wrap;'
            f' line-height: 1.55;">{body}</div>'
            f'{sources_panel}'
            f'{save_footer}'
            '</div>'
        )

    def _sources_panel_html(self, text: str, labels: dict[str, str]) -> str:
        """Grouped 'Sources' panel under the answer body — one row per
        source type (💬 Discord / 📄 PDFs / ▶ TomTube) with deduplicated
        clickable chips. Mirrors the inline citation colors so the group
        lines up visually with its inline pills."""
        # Bucket + dedupe citations in order of first appearance.
        buckets: dict[str, list[str]] = {"msg": [], "doc": [], "vid": []}
        seen: dict[str, set[str]] = {"msg": set(), "doc": set(), "vid": set()}
        for m in CITATION_RE.finditer(text or ""):
            kind, raw = m.group(1), m.group(2)
            if kind not in buckets:
                continue
            if raw in seen[kind]:
                continue
            seen[kind].add(raw)
            buckets[kind].append(raw)

        if not any(buckets.values()):
            return ""

        rows: list[str] = []
        for kind, heading, emoji in (
            ("msg", "Discord", "💬"),
            ("doc", "PDFs",    "📄"),
            ("vid", "TomTube", "▶"),
        ):
            if not buckets[kind]:
                continue
            style = self._PILL_STYLES[kind]
            chips = []
            for raw in buckets[kind]:
                href = f"{kind}:{raw}"
                friendly = labels.get(href) or {"msg": "msg", "doc": "doc",
                                                "vid": "▶ video"}[kind]
                chips.append(
                    f'<a href="{html.escape(href)}" '
                    f'style="color: {style["fg"]}; text-decoration: none; '
                    f'background: {style["bg"]}; padding: 2px 8px;'
                    f' border-radius: 4px; margin-right: 6px; font-size: 11px;'
                    f' font-weight: 500;">{html.escape(friendly)}</a>'
                )
            rows.append(
                f'<div style="margin: 4px 0; font-size: 11px;">'
                f'<span style="color: {COLOR_DIM}; margin-right: 8px;">'
                f'{emoji} <b>{heading}</b></span>'
                f'{"".join(chips)}</div>'
            )

        return (
            f'<div style="margin-left: 38px; margin-top: 8px; padding: 8px 12px;'
            f' background: rgba(0,0,0,0.18); border-radius: 6px;">'
            f'<div style="color: {COLOR_DIM}; font-size: 10px; '
            f'text-transform: uppercase; letter-spacing: 0.5px; '
            f'margin-bottom: 4px;">Sources</div>'
            f'{"".join(rows)}'
            f'</div>'
        )

    # Per-source pill styling so Discord, PDF, and YouTube citations are
    # distinguishable at a glance rather than sharing one gold pill.
    # Discord → channel blue-violet. PDF → warm gold (author-authored docs).
    # Video → YouTube red. Each uses a soft tinted background + matching text.
    _PILL_STYLES: dict[str, dict[str, str]] = {
        "msg": {"fg": "#8FA1FF", "bg": "rgba(88,101,242,0.14)"},
        "doc": {"fg": "#FFC857", "bg": "rgba(255,200,87,0.12)"},
        "vid": {"fg": "#FF6B6B", "bg": "rgba(255,77,77,0.14)"},
    }

    @classmethod
    def _linkify_citations(cls, text: str, labels: dict[str, str] | None = None) -> str:
        """Turn [msg:ID], [doc:ID], [vid:ID] into HTML anchors with friendly
        labels and per-source color coding.

        ``labels`` maps the raw "msg:123"/"doc:42"/"vid:17" key to a human
        label. The ID is still encoded in the anchor href so clicks route
        correctly.
        """
        labels = labels or {}
        out: list[str] = []
        last_end = 0
        for m in CITATION_RE.finditer(text or ""):
            start, end = m.span()
            if start > last_end:
                out.append(html.escape(text[last_end:start]))
            kind, raw = m.group(1), m.group(2)
            href = f"{kind}:{raw}"
            friendly = labels.get(href)
            if not friendly:
                friendly = {"msg": "msg", "doc": "doc", "vid": "▶ video"}.get(
                    kind, kind
                )
            label = html.escape(friendly)
            style = cls._PILL_STYLES.get(kind, cls._PILL_STYLES["doc"])
            out.append(
                f'<a href="{html.escape(href)}" '
                f'style="color: {style["fg"]}; text-decoration: none; '
                f'background: {style["bg"]}; padding: 1px 6px;'
                f' border-radius: 4px; font-weight: 500;">{label}</a>'
            )
            last_end = end
        if last_end < len(text):
            out.append(html.escape(text[last_end:]))
        return "".join(out)

    def _resolve_citation_labels(self, text: str) -> dict[str, str]:
        """Look up nicer display labels for every [msg:…]/[doc:…] in ``text``.

        Message ids → "Author · Mon YYYY".  Doc page ids → "<short-title> · pN".
        Returns a map from the full "kind:raw" citation key to a friendly
        string; unknown ids silently drop out and get the generic "msg"/"doc"
        fallback in :meth:`_linkify_citations`.
        """
        msg_ids: set[str] = set()
        doc_ids: set[str] = set()
        vid_chunk_ids: set[str] = set()
        for m in CITATION_RE.finditer(text or ""):
            kind, raw = m.group(1), m.group(2)
            if kind == "msg":
                msg_ids.add(raw)
            elif kind == "doc":
                doc_ids.add(raw)
            elif kind == "vid":
                vid_chunk_ids.add(raw)

        out: dict[str, str] = {}

        # ---- messages ----------------------------------------------------
        if msg_ids:
            placeholders = ",".join("?" * len(msg_ids))
            rows = self._conn.execute(
                f"""
                SELECT id, author_nickname, author_name, timestamp
                  FROM messages WHERE id IN ({placeholders})
                """,
                list(msg_ids),
            ).fetchall()
            for r in rows:
                nick = r["author_nickname"] or r["author_name"] or "?"
                nick = nick.strip()
                if len(nick) > 18:
                    nick = nick[:16] + "…"
                ts = (r["timestamp"] or "")
                date = _fmt_short_ts(ts)
                label = f"{nick} · {date}" if date else nick
                out[f"msg:{r['id']}"] = label

        # ---- doc pages --------------------------------------------------
        if doc_ids:
            try:
                int_ids = [int(x) for x in doc_ids]
            except ValueError:
                int_ids = []
            if int_ids:
                placeholders = ",".join("?" * len(int_ids))
                rows = self._conn.execute(
                    f"""
                    SELECT p.id AS pid, p.page_num AS pnum,
                           d.title AS title, d.filename AS fn
                      FROM document_pages p
                      JOIN documents d ON d.id = p.document_id
                     WHERE p.id IN ({placeholders})
                    """,
                    int_ids,
                ).fetchall()
                for r in rows:
                    title = r["title"] or (r["fn"] or "doc")
                    title = _short_doc_title(title)
                    out[f"doc:{int(r['pid'])}"] = f"{title} · p{int(r['pnum'])}"

        # ---- video chunks -----------------------------------------------
        # Each vid:N citation resolves to "▶ 14:32 · <short title>" which
        # hints at the timestamp. The actual youtube.com/...&t= URL is
        # assembled in the click handler, not here.
        if vid_chunk_ids:
            try:
                int_cids = [int(x) for x in vid_chunk_ids]
            except ValueError:
                int_cids = []
            if int_cids:
                placeholders = ",".join("?" * len(int_cids))
                rows = self._conn.execute(
                    f"""
                    SELECT c.id AS cid, c.start_sec AS ss,
                           v.title AS title
                      FROM video_chunks c
                      JOIN videos v ON v.id = c.video_id
                     WHERE c.id IN ({placeholders})
                    """,
                    int_cids,
                ).fetchall()
                for r in rows:
                    title = r["title"] or "Tom video"
                    if len(title) > 30:
                        title = title[:28] + "…"
                    ts = _fmt_short_timestamp(float(r["ss"] or 0.0))
                    out[f"vid:{int(r['cid'])}"] = f"▶ {ts} · {title}"

        return out

    # ------------------------------------------------------------------
    # Did-you-mean banner
    # ------------------------------------------------------------------
    def _show_correction_banner(self, original: str, corrected: str,
                                corrections: list[tuple[str, str]]) -> None:
        pairs = ", ".join(f"<b>{html.escape(o)}</b>→<b>{html.escape(c)}</b>"
                          for o, c in corrections)
        self._banner_label.setText(
            f"Did you mean: <i>{html.escape(corrected)}</i>? ({pairs})"
        )
        self._pending_corrected = corrected
        self._banner.setVisible(True)

    def _hide_correction_banner(self) -> None:
        self._banner.setVisible(False)
        self._pending_corrected = None
        self._banner_label.setText("")

    def _on_banner_use(self) -> None:
        q = self._pending_corrected
        self._hide_correction_banner()
        if q:
            self._input.setPlainText(q)
            self._submit_now(q)

    def _on_banner_keep(self) -> None:
        q = self._input.toPlainText().strip()
        self._hide_correction_banner()
        if q:
            self._submit_now(q)

    # ------------------------------------------------------------------
    # send flow
    # ------------------------------------------------------------------
    def _on_send(self) -> None:
        q = self._input.toPlainText().strip()
        if not q:
            return
        if self._worker is not None and self._worker.isRunning():
            return

        # Check for a spell suggestion first — don't swallow typos silently.
        corrected_q, corrections = chatmod.preview_corrections(q)
        if corrections and corrected_q != q:
            self._show_correction_banner(q, corrected_q, corrections)
            return

        self._submit_now(q)

    def _submit_now(self, question: str) -> None:
        self._input.clear()

        # Render the user turn with attachment preview(s) inline.
        if self._attachment_paths:
            names = ", ".join(Path(p).name for p in self._attachment_paths)
            display = question + f"\n\n📎 [attached: {names}]"
        else:
            display = question
        self._history.append(ChatTurn(role="user", content=display))
        self._render_transcript()
        self._set_busy(True)
        # Kick off the elapsed-time ticker so the user sees progress.
        self._think_started = time.time()
        has_image = bool(self._attachment_paths)
        self._status.setText(
            "Thinking… 0:00"
            + ("   (vision model — first call may take 30–90s)" if has_image else "")
        )
        self._think_timer.start()

        attachments = list(self._attachment_paths)
        self._worker = ChatWorker(
            question, self._history[:-1],
            attachment_paths=attachments or None, parent=self,
        )
        self._worker.answered.connect(self._on_answered)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()
        # Consume the attachment — a single ask uses it once.
        self._clear_attachment()

    def _tick_thinking(self) -> None:
        elapsed = time.time() - self._think_started
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        has_image = any(
            t.role == "user" and "[attached:" in t.content
            for t in self._history[-1:]
        )
        hint = ""
        if has_image and elapsed > 60:
            hint = "   (local vision model — this is slow)"
        elif elapsed > 90:
            hint = "   (taking longer than expected…)"
        elif has_image:
            hint = "   (vision model — first call may take 30–90s)"
        self._status.setText(f"Thinking… {mins}:{secs:02d}{hint}")

    def _on_answered(self, result: AnswerResult) -> None:
        self._think_timer.stop()
        self._worker = None
        self._last_answer_sources = result.sources
        self._last_answer_citations = list(result.citations)

        # If we had to fall back (e.g. Gemini rate-limited), inline a small
        # note at the top of the answer so the user knows what happened.
        content = result.answer or "(no answer)"
        if result.fallback_reason and result.provider_used:
            content = (
                f"_⚠️ Primary chat provider hit a limit — answered by "
                f"{result.provider_used} (local fallback) instead._\n\n" + content
            )

        elapsed = time.time() - self._think_started if self._think_started else 0
        self._history.append(ChatTurn(role="assistant", content=content))
        self._render_transcript()
        self._set_busy(False)
        n_cites = len(result.citations)
        n_src = len(result.sources)
        provider = result.provider_used or ""
        tail = f" · answered by {provider}" if provider else ""
        took = f" · took {elapsed:.1f}s" if elapsed else ""
        self._status.setText(
            f"{n_src} sources · {n_cites} citations{tail}{took} · Ctrl+Enter to send"
        )

    def _on_failed(self, err: str) -> None:
        self._think_timer.stop()
        self._worker = None
        friendly = _friendly_error(err)
        self._history.append(ChatTurn(role="assistant", content=friendly))
        self._render_transcript()
        self._set_busy(False)
        self._status.setText("Ready · Ctrl+Enter to send")

    def _set_busy(self, busy: bool) -> None:
        self._send_btn.setEnabled(not busy)
        self._send_btn.setText("Thinking…" if busy else "Ask")
        self._input.setReadOnly(busy)

    def clear_history(self) -> None:
        self._history = []
        self._hide_correction_banner()
        self._render_empty_state()
        self._status.setText("")

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(1500)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(500)
            self._worker = None

    # ------------------------------------------------------------------
    # citation / sample clicks
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # quick-switch chat provider (Gemini cloud ↔ Ollama local)
    # ------------------------------------------------------------------
    def _set_chat_provider(self, name: str) -> None:
        name = name.strip().lower()
        if name not in ("gemini", "ollama"):
            return
        # Primary = chosen, fallback = the other one (so bursts over Gemini
        # free-tier caps spill onto local llama3.1 instead of erroring).
        fallback = "ollama" if name == "gemini" else "gemini"
        dbmod.set_setting(self._conn, "ai_provider_chat", name)
        dbmod.set_setting(self._conn, "ai_provider_chat_fallback", fallback)
        # Blow the provider cache so the next ask() picks the new setting up
        aireg.reset_cache()
        self._refresh_chat_provider_buttons()

    def _refresh_chat_provider_buttons(self) -> None:
        current = (
            dbmod.get_setting(self._conn, "ai_provider_chat", "gemini") or "gemini"
        ).strip().lower()
        fb = (
            dbmod.get_setting(self._conn, "ai_provider_chat_fallback", "") or ""
        ).strip().lower()

        # .setChecked without signals
        for btn, tag in ((self._btn_gemini, "gemini"), (self._btn_ollama, "ollama")):
            btn.blockSignals(True)
            btn.setChecked(tag == current)
            btn.blockSignals(False)

        if fb and fb != current:
            self._provider_hint.setText(
                f"Primary: {current}   ·   fallback: {fb} (auto when primary rate-limits)"
            )
        else:
            self._provider_hint.setText(f"Primary: {current}")

    # ------------------------------------------------------------------
    # attachment handling
    # ------------------------------------------------------------------
    def _on_attach_clicked(self) -> None:
        # Multi-select so a user can pick HTF + intraday + Bookmap in one go;
        # single selections still work.
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach chart(s) for analysis",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        for p in paths or []:
            if p and p not in self._attachment_paths:
                self._attachment_paths.append(p)
        self._refresh_attachments_preview()

    def _clear_attachment(self) -> None:
        self._attachment_paths = []
        self._refresh_attachments_preview()

    def _on_image_pasted(self, path: str) -> None:
        """Handler fired when the user pastes a screenshot into the composer.

        Appends to the existing attachment list rather than replacing, so
        a user can paste HTF first and then paste the intraday Bookmap.
        """
        if path and path not in self._attachment_paths:
            self._attachment_paths.append(path)
        self._refresh_attachments_preview()
        if self._attachment_paths:
            n = len(self._attachment_paths)
            self._status.setText(
                f"📋 {n} chart{'s' if n != 1 else ''} ready — Ctrl+Enter to send"
            )

    def _refresh_attachments_preview(self) -> None:
        """Re-render the attachment strip so it reflects _attachment_paths.

        Keeps the trailing label + "Remove all" button; the thumbnails
        between them are rebuilt every time the list changes.
        """
        # Remove any existing thumb buttons between index 0 (label) and
        # the last two items (stretch + Remove-all button).
        while self._attachment_lay.count() > 3:
            it = self._attachment_lay.takeAt(1)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if not self._attachment_paths:
            self._attachment_label.setText("")
            self._attachment_frame.setVisible(False)
            return
        for i, p in enumerate(self._attachment_paths):
            self._attachment_lay.insertWidget(i + 1, self._make_thumb_button(p))
        n = len(self._attachment_paths)
        self._attachment_label.setText(
            f"<b>{n} chart{'s' if n != 1 else ''}</b> "
            f"<span style='color:{COLOR_DIM};'>· will be sent together"
            f"{' (multi-timeframe)' if n > 1 else ''}</span>"
        )
        self._attachment_frame.setVisible(True)

    def _make_thumb_button(self, path: str) -> QPushButton:
        btn = QPushButton()
        pix = QPixmap(path)
        if not pix.isNull():
            thumb = pix.scaled(
                56, 40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            btn.setIcon(self._pixmap_to_icon(thumb))
            btn.setIconSize(thumb.size())
        btn.setFixedSize(60, 44)
        btn.setFlat(True)
        btn.setToolTip(f"<b>{Path(path).name}</b><br><i>Click to remove</i>")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {COLOR_BORDER};"
            f" border-radius: 4px; padding: 0; }}"
            f"QPushButton:hover {{ border: 1px solid {COLOR_AUTHOR_TOM}; }}"
        )
        btn.clicked.connect(lambda _checked, pp=path: self._remove_attachment(pp))
        return btn

    def _remove_attachment(self, path: str) -> None:
        self._attachment_paths = [p for p in self._attachment_paths if p != path]
        self._refresh_attachments_preview()

    @staticmethod
    def _pixmap_to_icon(pix):
        from PyQt6.QtGui import QIcon
        return QIcon(pix)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("sample:"):
            self._input.setPlainText(href[len("sample:"):])
            self._input.setFocus()
            return
        if href.startswith("save:"):
            self._save_answer(href[len("save:"):])
            return
        m = re.match(r"^(msg|doc|vid):(.+)$", href)
        if not m:
            return
        kind, raw = m.group(1), m.group(2)
        if kind == "vid":
            # Resolve chunk_id → youtube URL with timestamp, open in browser.
            try:
                cid = int(raw)
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
            import webbrowser
            from tomslab.chat import _youtube_link
            webbrowser.open(_youtube_link(row["url"] or "", float(row["ss"] or 0.0)))
            return
        self.citation_clicked.emit(kind, raw)

    def _save_answer(self, idx_str: str) -> None:
        try:
            idx = int(idx_str)
        except ValueError:
            return
        if idx < 0 or idx >= len(self._history):
            return
        answer_turn = self._history[idx]
        if answer_turn.role != "assistant":
            return
        # Find the nearest preceding user turn for the question.
        question = ""
        for j in range(idx - 1, -1, -1):
            if self._history[j].role == "user":
                question = self._history[j].content
                break
        citations = list(CITATION_RE.findall(answer_turn.content))
        citation_strs = [f"{kind}:{raw}" for kind, raw in citations]
        bmmod.save_chat_answer(
            self._conn, question, answer_turn.content, citation_strs
        )
        self._status.setText(
            f"⭐ Saved — view it in the Bookmarks tab · Ctrl+Enter to ask again"
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
_MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt_short_ts(iso_ts: str) -> str:
    """'2023-05-08T15:30:00-04:00' -> 'May 2023'. Returns '' on bad input."""
    if not iso_ts:
        return ""
    try:
        y = int(iso_ts[:4])
        m = int(iso_ts[5:7])
        return f"{_MONTHS_SHORT[m - 1]} {y}"
    except Exception:
        return iso_ts[:7]


def _fmt_short_timestamp(sec: float) -> str:
    """Seconds → 'H:MM:SS' or 'M:SS' depending on length. For compact
    pills next to video citations."""
    s = int(sec or 0)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _short_doc_title(title: str) -> str:
    """Compress long PDF titles into citation-pill-sized strings."""
    t = (title or "").strip()
    # Tom's glossary PDFs — use the acronym or core name.
    aliases = {
        "TomB's 60 Structured Trades": "60 Trades",
        "60 Structured Trades": "60 Trades",
        "Mean Reversion Structured Trade": "Mean Reversion",
        "Opening Context Alignment": "Opening Ctx",
        "Market Structure": "Market Struct",
        "Auction Market Theory-101": "AMT",
        "Auction Market Theory": "AMT",
        "Trader Lab Glossary": "Glossary",
        "Trader_Lab_Glossary": "Glossary",
        "Toms Bookmap Settings": "BM Settings",
        "Toms_Bookmap_Settings": "BM Settings",
        "Stats by Target": "Stats",
    }
    if t in aliases:
        return aliases[t]
    # third-party books — strip author and subtitle noise
    if t.startswith("Best Loser Wins"):
        return "Hougaard"
    if t.startswith("Trade Your Way"):
        return "Tharp"
    # generic: keep first 3 words, cap at 22 chars
    short = " ".join(t.split()[:3])
    return short[:22] + ("…" if len(short) > 22 else "")


def _dump_pixmap_to_tmp(pix: QPixmap) -> str:
    """Save a pasted clipboard image as a fresh PNG under %TEMP% and return
    the path. Files are unique-per-paste so nothing overwrites prior
    attachments that might still be in flight."""
    try:
        tmpdir = Path(tempfile.gettempdir()) / "TomsLab_paste"
        tmpdir.mkdir(parents=True, exist_ok=True)
        fname = f"paste_{int(time.time() * 1000)}.png"
        path = tmpdir / fname
        pix.save(str(path), "PNG")
        return str(path)
    except Exception:
        return ""


def _friendly_error(err: str) -> str:
    """Transform a raw exception string into something a PM wants to see.

    Ordering matters — match the *specific* conditions first (auth, rate
    limit, service outage) before the generic provider-name matches.
    """
    low = (err or "").lower()
    # Authentication / key problems — most specific.
    if "no gemini api key" in low or "unauthorized" in low or " 401" in low:
        return (
            "**No Gemini API key configured, or the key is invalid.** Open "
            "File → Settings → AI Providers and paste a key from "
            "https://aistudio.google.com/app/apikey."
        )
    # Rate-limit / quota.
    if " 429" in err or "resource_exhausted" in low or "rate limit" in low:
        return (
            "**Free-tier rate limit reached.** Wait a minute and retry, "
            "or add billing at console.cloud.google.com for higher limits."
        )
    # Gemini / Google overload.
    if " 503" in err or "unavailable" in low:
        return (
            "**The chat model is overloaded right now.** Free-tier spikes "
            "usually clear in 10–30 seconds — try again."
        )
    # Ollama truly unreachable (daemon down / refused).
    if "connection refused" in low or "connecterror" in low or "timed out" in low:
        return (
            "**Chat backend didn't respond in time.** If your chat provider "
            "is Ollama, make sure the Ollama app is running. If it's Gemini, "
            "your network may be slow right now — try again."
        )
    # Provider name in the message but we didn't match a specific code —
    # show the raw message so we can actually debug it.
    return f"Chat failed: {err}"
