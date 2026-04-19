"""First-run setup wizard — optional guided tour for new users.

Shows on the very first launch (after the Getting Started & Policy
dialog) if the user has no ingested content yet. Four quick steps:

  1. Explain the ingest model (user-owned, nothing auto-fetches)
  2. Point at the Discord ingest workflow
  3. Point at the PDFs folder
  4. Point at the TomTube folder-import (as the recommended path)

Every step has a 'Skip' option — nothing is mandatory. The wizard
records completion in settings so it doesn't re-appear on future
launches.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tomslab import db as dbmod
from tomslab.paths import data_dir


_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_BORDER = "#3F4147"


class FirstRunWizard(QDialog):
    """Multi-step intro. The whole thing is skippable — but each page is
    short enough that most users will click through once."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tom's Lab — quick setup")
        self.setModal(True)
        self.resize(680, 520)
        self._step = 0
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 18)
        outer.setSpacing(14)

        self._progress = QLabel("Step 1 of 4")
        self._progress.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 11px;"
            " text-transform: uppercase; letter-spacing: 1px;"
        )
        outer.addWidget(self._progress)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_intro())
        self._stack.addWidget(self._page_discord())
        self._stack.addWidget(self._page_pdfs())
        self._stack.addWidget(self._page_videos())
        outer.addWidget(self._stack, stretch=1)

        # Navigation row
        nav = QHBoxLayout()
        nav.setSpacing(10)
        skip = QPushButton("Skip setup")
        skip.clicked.connect(self.reject)
        skip.setStyleSheet(self._btn_secondary())
        nav.addWidget(skip)
        nav.addStretch(1)
        self._back = QPushButton("← Back")
        self._back.clicked.connect(self._on_back)
        self._back.setStyleSheet(self._btn_secondary())
        self._back.setEnabled(False)
        nav.addWidget(self._back)
        self._next = QPushButton("Next →")
        self._next.clicked.connect(self._on_next)
        self._next.setStyleSheet(self._btn_primary())
        self._next.setDefault(True)
        nav.addWidget(self._next)
        outer.addLayout(nav)

    def _btn_primary(self) -> str:
        return (
            f"QPushButton {{ background: {_COLOR_GOLD}; color: #1E1F22;"
            f" padding: 8px 18px; border: none; border-radius: 6px;"
            f" font-weight: 600; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #FFD87A; }}"
        )

    def _btn_secondary(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {_COLOR_DIM};"
            f" padding: 8px 14px; border: 1px solid {_COLOR_BORDER};"
            f" border-radius: 6px; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {_COLOR_TEXT}; }}"
            f"QPushButton:disabled {{ color: #555; }}"
        )

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------
    def _make_page(self, heading: str, body_html: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        h = QLabel(f"<h2>{heading}</h2>")
        h.setStyleSheet(f"color: {_COLOR_TEXT};")
        lay.addWidget(h)
        body = QLabel(body_html)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(True)
        body.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-size: 13px; line-height: 1.6;"
        )
        lay.addWidget(body)
        lay.addStretch(1)
        return w

    def _page_intro(self) -> QWidget:
        return self._make_page(
            "Welcome — here's how Tom's Lab works",
            "<p>Tom's Lab doesn't auto-fetch anything. You import the "
            "content you want indexed, and the app makes it searchable.</p>"
            "<p>Four source types are supported:</p>"
            "<ul>"
            "<li><b>Discord exports</b> from the Bookmap Traders Lab "
            "channel (<code>traders-lab-tom-b</code>)</li>"
            "<li><b>Tom's PDFs</b> — the glossary, 60 Structured "
            "Trades, Market Structure, Auction Market Theory, etc.</li>"
            "<li><b>TomTube</b> — audio of Tom's YouTube videos, "
            "transcribed locally on your GPU</li>"
            "<li><b>The Linnsoft forum thread</b> — Eddy's curated "
            "archive of Tom's Investor/RT work</li>"
            "</ul>"
            "<p>This wizard walks through each one. <b>You can skip any "
            "step</b> — nothing is mandatory to use the app.</p>"
        )

    def _page_discord(self) -> QWidget:
        return self._make_page(
            "1. Discord messages",
            "<p>Discord exports come from "
            "<a href='https://github.com/Tyrrrz/DiscordChatExporter'>"
            "DiscordChatExporter</a> (DCE) in JSON format. Once you have "
            "one:</p>"
            "<ul>"
            "<li><b>File → Import DCE JSON</b> (Ctrl+I), or just drag "
            "the .json file onto the app window</li>"
            "<li>Re-running is idempotent — existing messages are "
            "skipped in ~16 seconds</li>"
            "</ul>"
            "<p style='color:#b00020; font-size:11px;'><b>Note:</b> "
            "bulk-exporting Discord messages may violate Discord's "
            "Terms of Service. Whether and how you do that is your "
            "responsibility — this app only consumes files you "
            "provide.</p>"
        )

    def _page_pdfs(self) -> QWidget:
        pdfs_dir = str(Path(r"D:\Toms Lab\tom_docs"))
        return self._make_page(
            "2. Tom's PDFs",
            "<p>Drop Tom's reference PDFs into the <code>tom_docs</code> "
            "folder. The app OCRs them, extracts text, and merges them "
            "into Ask Tom's context window alongside Discord.</p>"
            "<ul>"
            "<li>Expected location: "
            f"<code>{pdfs_dir}</code></li>"
            "<li>Authored-by-Tom PDFs get a search boost so definitional "
            "questions surface his own framing first.</li>"
            "<li>PDF ingest is currently code-only; import runs on "
            "startup and on every refresh.</li>"
            "</ul>"
            "<p>You can find Tom's PDFs pinned in the Bookmap Discord's "
            "Traders Lab channel.</p>"
        )

    def _page_videos(self) -> QWidget:
        folder = str(data_dir().parent / "Tom Videos")
        return self._make_page(
            "3. TomTube (videos) — use folder import",
            "<p>YouTube fights third-party downloaders actively. Rather "
            "than building a fragile direct-download path, <b>Tom's Lab "
            "reads audio files you download with any tool</b>.</p>"
            "<p>Recommended flow:</p>"
            "<ol>"
            "<li>Bulk-download Tom's videos with a tool that works "
            "today — JDownloader, 4K Video Downloader, yt-dlp CLI, or "
            "a browser extension.</li>"
            "<li>Point the tool at any folder — the default suggestion "
            f"is <code>{folder}</code>.</li>"
            "<li>In Tom's Lab: <b>File → Import videos from folder…"
            "</b></li>"
            "</ol>"
            "<p>Filenames like <code>Title [abcdef12345].mp3</code> "
            "preserve the 11-char YouTube id so citations deep-link "
            "back to the exact timestamp on youtube.com.</p>"
            "<p style='color:#b00020; font-size:11px;'><b>Note:</b> "
            "bulk-downloading YouTube videos may violate YouTube's "
            "Terms of Service. Doing so is your responsibility — "
            "this app only consumes files you provide.</p>"
            "<p>An experimental in-app downloader exists "
            "(<b>File → Import YouTube directly</b>) but may break "
            "at any time without warning. The folder-import path is "
            "the supported way.</p>"
        )

    # ------------------------------------------------------------------
    def _on_next(self) -> None:
        if self._step >= self._stack.count() - 1:
            self.accept()
            return
        self._step += 1
        self._stack.setCurrentIndex(self._step)
        self._refresh_nav()

    def _on_back(self) -> None:
        if self._step <= 0:
            return
        self._step -= 1
        self._stack.setCurrentIndex(self._step)
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        self._progress.setText(
            f"Step {self._step + 1} of {self._stack.count()}"
        )
        self._back.setEnabled(self._step > 0)
        self._next.setText(
            "Finish" if self._step == self._stack.count() - 1 else "Next →"
        )


def should_show(conn: sqlite3.Connection) -> bool:
    """True if the wizard hasn't been completed/skipped yet AND the DB
    looks empty enough that a new user would benefit from it."""
    if dbmod.get_setting(conn, "first_run_wizard_done", "") == "yes":
        return False
    # Empty-DB heuristic: no messages AND no doc_pages imported yet.
    try:
        n_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        n_doc = conn.execute("SELECT COUNT(*) FROM document_pages").fetchone()[0]
    except Exception:
        return True
    return int(n_msg or 0) == 0 and int(n_doc or 0) == 0


def mark_done(conn: sqlite3.Connection) -> None:
    dbmod.set_setting(conn, "first_run_wizard_done", "yes")
