"""QAbstractListModel backed by SQLite — paginates messages on demand.

Phase 1 just shows messages in reverse-chronological order. Phase 2 will
swap in a search-driven model.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt


PAGE_SIZE = 500
MAX_ROWS = 10_000   # cap for Phase 1 — avoid loading hundreds of thousands


class MessageListModel(QAbstractListModel):
    """Lazy, windowed view onto the `messages` table.

    We load one page at a time. canFetchMore/fetchMore keep Qt's list view
    snappy on very large imports.
    """

    def __init__(self, conn: sqlite3.Connection, parent: Any = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._rows: list[sqlite3.Row] = []
        self._total = 0
        self._loaded = 0
        self.reload()

    # ---- public API ----------------------------------------------------
    def reload(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._loaded = 0
        row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        self._total = min(int(row["n"] or 0), MAX_ROWS)
        self.endResetModel()
        self._load_page()

    def total_in_db(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"] or 0)

    # ---- Qt model API --------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if parent.isValid():
            return False
        return self._loaded < self._total

    def fetchMore(self, parent: QModelIndex) -> None:
        if parent.isValid():
            return
        self._load_page()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return _format_row(row)
        if role == Qt.ItemDataRole.ToolTipRole:
            return row["content"] or ""
        if role == Qt.ItemDataRole.UserRole:
            return row["id"]
        return None

    # ---- internals -----------------------------------------------------
    def _load_page(self) -> None:
        if self._loaded >= self._total:
            return
        take = min(PAGE_SIZE, self._total - self._loaded)
        new = self._conn.execute(
            """
            SELECT id, author_name, author_nickname, timestamp, content,
                   is_featured_speaker,
                   (SELECT COUNT(*) FROM attachments a WHERE a.message_id = m.id) AS n_att
              FROM messages m
             ORDER BY timestamp DESC, id DESC
             LIMIT ? OFFSET ?
            """,
            (take, self._loaded),
        ).fetchall()
        if not new:
            return
        first = len(self._rows)
        last = first + len(new) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._rows.extend(new)
        self._loaded += len(new)
        self.endInsertRows()


def _format_row(row: sqlite3.Row) -> str:
    ts = (row["timestamp"] or "")[:19].replace("T", " ")
    speaker = row["author_nickname"] or row["author_name"] or "?"
    star = "★ " if int(row["is_featured_speaker"] or 0) else "  "
    n_att = int(row["n_att"] or 0)
    att = f"  [📷 {n_att}]" if n_att else ""
    snippet = (row["content"] or "").strip().replace("\n", " ")
    if len(snippet) > 180:
        snippet = snippet[:177] + "…"
    return f"{star}{ts}  {speaker}:{att}  {snippet}"
