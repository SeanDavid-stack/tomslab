"""QAbstractListModel backed by SQLite — paginates messages on demand.

Supports two modes:
  * Browse  — newest first, used when the search box is empty.
  * Search  — ranked by FTS5 bm25 score against a user query.

The model also joins reply-to parents and attachment metadata so the
delegate can render a full message card in one pass.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt

from tomslab import search as searchmod
from tomslab import semantic as semanticmod
from tomslab import visual as visualmod
from tomslab.search import SearchMode


PAGE_SIZE = 200
MAX_BROWSE_ROWS = 10_000     # cap when no search query is active
MAX_SEARCH_ROWS = 2_000      # cap when searching


ROLE_MESSAGE = Qt.ItemDataRole.UserRole + 1


@dataclass
class AttachmentRow:
    id: str
    filename: str
    local_path: str
    file_size: int


@dataclass
class MessageRow:
    id: str
    author_name: str
    author_nickname: str
    timestamp: str
    content: str
    is_featured_speaker: bool
    is_pinned: bool
    reply_to_id: Optional[str]
    reply_to_author: Optional[str]
    reply_to_snippet: Optional[str]
    attachments: list[AttachmentRow] = field(default_factory=list)


class MessageListModel(QAbstractListModel):
    def __init__(self, conn: sqlite3.Connection, parent: Any = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._rows: list[MessageRow] = []
        self._total = 0                 # rows reachable via this model (capped)
        self._total_full = 0             # true match count (uncapped, search only)
        self._loaded = 0
        self._query: str = ""
        self._mode: SearchMode = SearchMode.KEYWORD
        self._search_ids: list[str] = []   # pre-resolved IDs in search mode
        self._last_error: str = ""
        self.reload()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def set_query(self, query: str, mode: SearchMode | None = None) -> None:
        query = (query or "").strip()
        if mode is None:
            mode = self._mode
        if query == self._query and mode == self._mode:
            return
        self._query = query
        self._mode = mode
        self.reload()

    def set_mode(self, mode: SearchMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        if self._query:
            self.reload()

    def current_query(self) -> str:
        return self._query

    def current_mode(self) -> SearchMode:
        return self._mode

    def last_error(self) -> str:
        return self._last_error

    def reload(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._loaded = 0
        self._search_ids = []
        self._total_full = 0
        self._last_error = ""

        if self._query:
            if self._mode == SearchMode.SEMANTIC:
                try:
                    self._search_ids = semanticmod.semantic_search_ids(
                        self._conn, self._query, limit=MAX_SEARCH_ROWS
                    )
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._search_ids = []
                self._total_full = len(self._search_ids)
            elif self._mode == SearchMode.VISUAL:
                try:
                    self._search_ids = visualmod.visual_search_message_ids(
                        self._conn, self._query, limit=MAX_SEARCH_ROWS
                    )
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._search_ids = []
                self._total_full = len(self._search_ids)
            else:
                self._total_full = searchmod.count_keyword_hits(self._conn, self._query)
                self._search_ids = searchmod.keyword_search_ids(
                    self._conn, self._query, limit=MAX_SEARCH_ROWS
                )
            self._total = len(self._search_ids)
        else:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
            total = int(row["n"] or 0)
            self._total_full = total
            self._total = min(total, MAX_BROWSE_ROWS)

        self.endResetModel()
        self._load_page()

    def total_in_db(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"] or 0)

    def total_matches(self) -> int:
        """True total matches (uncapped)."""
        return self._total_full

    def total_loadable(self) -> int:
        """Capped total the model will actually expose through fetchMore."""
        return self._total

    # ------------------------------------------------------------------
    # Qt model API
    # ------------------------------------------------------------------
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
        if role == ROLE_MESSAGE:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            # Used as a fallback (e.g. accessibility) — the delegate ignores this.
            speaker = row.author_nickname or row.author_name or "?"
            snippet = (row.content or "").strip().replace("\n", " ")[:160]
            return f"{speaker}: {snippet}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.content or ""
        return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _load_page(self) -> None:
        if self._loaded >= self._total:
            return
        take = min(PAGE_SIZE, self._total - self._loaded)
        if self._query:
            batch_ids = self._search_ids[self._loaded : self._loaded + take]
            message_rows = self._fetch_by_ids(batch_ids, preserve_order=True)
        else:
            message_rows = self._fetch_browse(offset=self._loaded, limit=take)

        if not message_rows:
            return

        first = len(self._rows)
        last = first + len(message_rows) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._rows.extend(message_rows)
        self._loaded += len(message_rows)
        self.endInsertRows()

    def _fetch_browse(self, offset: int, limit: int) -> list[MessageRow]:
        rows = self._conn.execute(
            """
            SELECT m.id, m.author_name, m.author_nickname, m.timestamp, m.content,
                   m.is_featured_speaker, m.is_pinned, m.reply_to_message_id AS reply_id,
                   parent.author_nickname AS reply_author_nick,
                   parent.author_name     AS reply_author_name,
                   SUBSTR(COALESCE(parent.content, ''), 1, 140) AS reply_snippet
              FROM messages m
              LEFT JOIN messages parent ON parent.id = m.reply_to_message_id
             ORDER BY m.timestamp DESC, m.id DESC
             LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return self._hydrate(rows)

    def _fetch_by_ids(self, ids: list[str], preserve_order: bool) -> list[MessageRow]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"""
            SELECT m.id, m.author_name, m.author_nickname, m.timestamp, m.content,
                   m.is_featured_speaker, m.is_pinned, m.reply_to_message_id AS reply_id,
                   parent.author_nickname AS reply_author_nick,
                   parent.author_name     AS reply_author_name,
                   SUBSTR(COALESCE(parent.content, ''), 1, 140) AS reply_snippet
              FROM messages m
              LEFT JOIN messages parent ON parent.id = m.reply_to_message_id
             WHERE m.id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        hydrated = self._hydrate(rows)
        if preserve_order:
            by_id = {r.id: r for r in hydrated}
            hydrated = [by_id[i] for i in ids if i in by_id]
        return hydrated

    def _hydrate(self, rows: list[sqlite3.Row]) -> list[MessageRow]:
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        attachments_by_msg: dict[str, list[AttachmentRow]] = {i: [] for i in ids}
        placeholders = ",".join("?" * len(ids))
        att_rows = self._conn.execute(
            f"""
            SELECT id, message_id, filename, local_path, file_size
              FROM attachments
             WHERE message_id IN ({placeholders})
             ORDER BY id
            """,
            ids,
        ).fetchall()
        for a in att_rows:
            attachments_by_msg.setdefault(a["message_id"], []).append(
                AttachmentRow(
                    id=a["id"],
                    filename=a["filename"] or "",
                    local_path=a["local_path"] or "",
                    file_size=int(a["file_size"] or 0),
                )
            )

        out: list[MessageRow] = []
        for r in rows:
            reply_author = None
            if r["reply_id"]:
                reply_author = r["reply_author_nick"] or r["reply_author_name"] or ""
            out.append(
                MessageRow(
                    id=r["id"],
                    author_name=r["author_name"] or "",
                    author_nickname=r["author_nickname"] or "",
                    timestamp=r["timestamp"] or "",
                    content=r["content"] or "",
                    is_featured_speaker=bool(r["is_featured_speaker"]),
                    is_pinned=bool(r["is_pinned"]),
                    reply_to_id=r["reply_id"],
                    reply_to_author=reply_author,
                    reply_to_snippet=(r["reply_snippet"] or None),
                    attachments=attachments_by_msg.get(r["id"], []),
                )
            )
        return out
