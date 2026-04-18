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


class ConceptChipBar(QWidget):
    concept_clicked = pyqtSignal(str)   # emits the term/abbreviation string

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
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

        for i, r in enumerate(rows):
            name = r["name"] or ""
            desc = r["description"] or ""
            abbr = _abbr_from_description(desc)
            label = abbr or name
            btn = QPushButton(label)
            btn.setToolTip(f"<b>{name}</b><br>{desc}" if desc else name)
            btn.setStyleSheet(CHIP_STYLE)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # Use the more searchable token: abbreviation if present, else name.
            search_term = abbr or name
            btn.clicked.connect(lambda _checked, t=search_term: self.concept_clicked.emit(t))
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, btn)

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
