"""Horizontal, scrollable strip of Tom's glossary concepts.

Clicking a chip emits ``concept_clicked(term)`` so the host can run a
keyword search for that term in the main feed.
"""
from __future__ import annotations

import sqlite3

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)


CHIP_STYLE = """
    QPushButton {
        background: #2B2D31;
        color: #DBDEE1;
        padding: 5px 12px;
        border: 1px solid #3F4147;
        border-radius: 12px;
        font-size: 11px;
    }
    QPushButton:hover {
        background: #3A3320;
        color: #FFC857;
        border: 1px solid #FFC857;
    }
"""


TOP_N_DEFAULT = 12   # default number of chips shown (the rest are behind "Show all")


class ConceptChipBar(QWidget):
    concept_clicked = pyqtSignal(str)       # left-click   → search/ask
    dashboard_requested = pyqtSignal(str)    # right-click  → concept dashboard
    evolution_requested = pyqtSignal(str)    # right-click  → evolution timeline

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._expanded = False
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 4, 10, 4)
        outer.setSpacing(8)

        lbl = QLabel("Tom's glossary:")
        lbl.setStyleSheet("color: #949BA4; font-size: 11px; font-weight: 600;")
        outer.addWidget(lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollBar:horizontal { height: 6px; background: transparent; }"
            "QScrollBar::handle:horizontal { background: #3F4147; border-radius: 3px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {"
            "  width: 0; }"
        )

        self._chips_host = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_host)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(6)
        self._chips_layout.addStretch(1)
        self._scroll.setWidget(self._chips_host)
        outer.addWidget(self._scroll, stretch=1)

    def reload(self) -> None:
        # clear existing chips (keep the trailing stretch)
        while self._chips_layout.count() > 1:
            it = self._chips_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        try:
            rows = self._conn.execute(
                "SELECT name, description FROM concepts ORDER BY name"
            ).fetchall()
        except Exception:
            rows = []

        # Resolve each concept's most-searchable token and its mention count
        # up-front so we can sort by frequency and show the heaviest-hitters
        # first.  26 FTS5 COUNT(*) queries at ~5 ms each is ~130 ms total —
        # fine at startup.
        entries: list[tuple[str, str, str, int]] = []
        for r in rows:
            name = r["name"] or ""
            desc = r["description"] or ""
            abbr = _abbr_from_description(desc)
            label = abbr or name
            search_term = abbr or name
            n = _count_mentions(self._conn, search_term)
            entries.append((name, desc, label, n))

        # Sort: most-mentioned first, ties alphabetically.  Keeps the top
        # chips stable run-to-run.
        entries.sort(key=lambda e: (-e[3], e[2].lower()))

        show_n = len(entries) if self._expanded else min(TOP_N_DEFAULT, len(entries))
        hidden = max(0, len(entries) - show_n)

        for (name, desc, label, n_mentions) in entries[:show_n]:
            display = f"{label}  ·  {_fmt_count(n_mentions)}" if n_mentions else label
            btn = QPushButton(display)
            tooltip = f"<b>{name}</b>"
            if desc:
                tooltip += f"<br>{desc}"
            if n_mentions:
                tooltip += f"<br><br><i>{n_mentions:,} messages mention this</i>"
            btn.setToolTip(tooltip)
            btn.setStyleSheet(CHIP_STYLE)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            search_term = label
            btn.clicked.connect(lambda _checked, t=search_term: self.concept_clicked.emit(t))
            # Right-click → evolution timeline. Using a context-menu hook
            # on the button so the primary left-click search behavior is
            # untouched.
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda _pos, t=search_term: self._show_context_menu(t)
            )
            btn.setToolTip(tooltip + "<br><br><i>Right-click for evolution timeline</i>")
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, btn)

        # Trailing "more" / "collapse" toggle.
        if hidden > 0 and not self._expanded:
            more = QPushButton(f"+ {hidden} more")
            more.setStyleSheet(CHIP_STYLE)
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.clicked.connect(self._on_toggle_expanded)
            more.setToolTip(f"Show all {len(entries)} glossary terms")
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, more)
        elif self._expanded and len(entries) > TOP_N_DEFAULT:
            less = QPushButton("− collapse")
            less.setStyleSheet(CHIP_STYLE)
            less.setCursor(Qt.CursorShape.PointingHandCursor)
            less.clicked.connect(self._on_toggle_expanded)
            less.setToolTip("Show only the most-mentioned terms")
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, less)

    def _on_toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self.reload()

    def _show_context_menu(self, term: str) -> None:
        """Right-click menu on a chip. Two actions: dashboard (all sources
        side-by-side) and evolution (time-grouped). Host wires each via
        the matching signal."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        dash = menu.addAction(f"Show everything on {term}…")
        evo = menu.addAction(f"Show how Tom's framing of {term} evolved…")
        chosen = menu.exec(self.cursor().pos())
        if chosen is dash:
            self.dashboard_requested.emit(term)
        elif chosen is evo:
            self.evolution_requested.emit(term)

    def count(self) -> int:
        return max(0, self._chips_layout.count() - 1)


def _abbr_from_description(desc: str) -> str:
    """Glossary entries are stored as '(ABBR) definition' — pull the ABBR out.

    Falls back to empty string when the description doesn't start with a
    parenthesised abbreviation.
    """
    if not desc or not desc.startswith("("):
        return ""
    end = desc.find(")")
    if end < 0:
        return ""
    return desc[1:end].strip()


def _count_mentions(conn: sqlite3.Connection, term: str) -> int:
    """Count messages that contain the term via FTS5 — very fast at this scale."""
    if not term:
        return 0
    t = term.replace('"', '""').strip()
    if not t:
        return 0
    # Prefix match so "RTH" finds "RTH." etc. Keep it simple.
    fts_q = f'"{t}"*'
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages_fts WHERE messages_fts MATCH ?",
            (fts_q,),
        ).fetchone()
        return int(row["n"] or 0)
    except Exception:
        return 0


def _fmt_count(n: int) -> str:
    if n >= 1000:
        k = n / 1000
        return f"{k:.1f}K" if k < 10 else f"{int(k)}K"
    return str(n)
