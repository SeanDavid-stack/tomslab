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

from tomslab import db as dbmod
from tomslab import search as searchmod
from tomslab import semantic as semanticmod
from tomslab import visual as visualmod
from tomslab.search import SearchMode


# Shared "non-noise" SQL fragment. A message is kept if ANY of these hold:
#   * it's from Tom (featured speaker)                           -> always kept
#   * it has at least one attachment                             -> charts always kept
#   * its trimmed text is >= 12 chars AND contains a letter      -> substantive
# This kills: single emoji replies ("👍"), one-token reactions ("lol", "ok",
# "yep", "nice"), pure punctuation / price-only numerics, etc.
NOISE_FILTER_SQL = """(
  m.is_featured_speaker = 1
  OR EXISTS (SELECT 1 FROM attachments a WHERE a.message_id = m.id)
  OR (
    LENGTH(TRIM(COALESCE(m.content,''))) >= 12
    AND m.content GLOB '*[A-Za-z]*'
    AND LOWER(TRIM(m.content)) NOT IN (
      'ok','okay','kk','yes','yep','yup','no','nope','nice','great',
      'thanks','thank you','thx','sure','true','correct','right','gotcha',
      'cheers','welcome','yw','np','lol','lolol','haha','hahaha','lmao',
      'lmaoo','rofl','fr','boom','fire','wow','nope.','yup.','right.',
      'correct.','indeed','agreed','same','ditto','bump','this','facts'
    )
  )
)"""


def _synthesize_doc_row(r: sqlite3.Row) -> "MessageRow":
    """Turn a document_pages row into a feed-friendly MessageRow."""
    author_label = r["author"] or "unknown"
    if author_label == "tom_b":
        author_nick = f"Tom B  · {r['title']}"
        is_featured = True
    elif author_label == "third_party":
        author_nick = f"Reference: {r['title']}"
        is_featured = False
    else:
        author_nick = r["title"] or r["fn"] or "Document"
        is_featured = False

    text = (r["text"] or "").strip()
    meta = {
        "doc_id": int(r["did"]),
        "page_num": int(r["pnum"]),
        "filename": r["fn"] or "",
        "title": r["title"] or "",
        "author": author_label,
        "doc_type": r["dtype"] or "",
        "rendered_path": r["rpath"] or "",
    }
    # attach the rendered page image as an "attachment" so the message
    # delegate renders the page thumbnail inline.
    attachments: list[AttachmentRow] = []
    if r["rpath"]:
        attachments.append(AttachmentRow(
            id=f"doc-{r['did']}-p{r['pnum']}",
            filename=f"page_{int(r['pnum']):04d}.png",
            local_path=r["rpath"],
            file_size=0,
        ))
    return MessageRow(
        id=f"doc:{int(r['pid'])}",
        author_name=author_label,
        author_nickname=author_nick,
        timestamp=f"📄 page {int(r['pnum'])}",
        content=text,
        is_featured_speaker=is_featured,
        is_pinned=False,
        reply_to_id=None,
        reply_to_author=None,
        reply_to_snippet=None,
        attachments=attachments,
        doc_meta=meta,
    )


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
    # If the row represents a PDF doc page rather than a Discord message
    # we set this to a non-None dict. The delegate renders it with a
    # "📄 Document" styling and no reply/pinned/star affordances.
    doc_meta: Optional[dict] = None


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
        self._search_ids: list[str] = []   # pre-resolved typed IDs in search mode
        self._mixed_hits: list = []        # MixedSemanticHit for semantic mode
        self._last_error: str = ""
        # When True the model is showing a windowed timeline around a
        # specific message (see ``show_window_around``). ``set_query`` and
        # ``reload`` clear it so the next user search/browse resets the view.
        self._is_window: bool = False
        self._hide_noise: bool = (
            dbmod.get_setting(conn, "hide_feed_noise", "1") == "1"
        )
        self.reload()

    def set_hide_noise(self, hide: bool) -> None:
        if hide == self._hide_noise:
            return
        self._hide_noise = hide
        dbmod.set_setting(self._conn, "hide_feed_noise", "1" if hide else "0")
        self.reload()

    def hide_noise(self) -> bool:
        return self._hide_noise

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def set_query(self, query: str, mode: SearchMode | None = None) -> None:
        query = (query or "").strip()
        if mode is None:
            mode = self._mode
        if query == self._query and mode == self._mode and not self._is_window:
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
        self._is_window = False

        # Note: the noise filter below only applies to BROWSE mode. For
        # SEARCH modes, the search layer already filters out trivially-short
        # rows (see search.MIN_CONTENT_LEN_FOR_SEARCH). Applying a second
        # filter would suppress results users explicitly asked for.
        if self._query:
            if self._mode == SearchMode.SEMANTIC:
                try:
                    self._mixed_hits = semanticmod.mixed_semantic_search(
                        self._conn, self._query, limit=MAX_SEARCH_ROWS
                    )
                    self._search_ids = [
                        f"msg:{h.message_id}" if h.source_type == "message"
                        else f"doc:{h.doc_page.page_id}"
                        for h in self._mixed_hits
                    ]
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._mixed_hits = []
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
            # Browse mode. Respect the noise filter when active.
            if self._hide_noise:
                row = self._conn.execute(
                    f"SELECT COUNT(*) AS n FROM messages m WHERE {NOISE_FILTER_SQL}"
                ).fetchone()
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

    def show_window_around(self, message_id: str, context: int = 100) -> int | None:
        """Replace the timeline with a window centered on ``message_id`` and
        return the row index of the target, or ``None`` if not found.

        Used when "Show in timeline" targets a message older than the
        ``MAX_BROWSE_ROWS`` cap or one hidden by the noise filter — the
        model enters a synthetic typed-id mode so the cap doesn't apply
        and the target itself is always present.
        """
        row = self._conn.execute(
            "SELECT timestamp FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return None
        ts = row["timestamp"] or ""

        newer = self._conn.execute(
            """
            SELECT id FROM messages
             WHERE (timestamp > ? OR (timestamp = ? AND id > ?))
             ORDER BY timestamp ASC, id ASC
             LIMIT ?
            """,
            (ts, ts, message_id, context),
        ).fetchall()
        older = self._conn.execute(
            """
            SELECT id FROM messages
             WHERE (timestamp < ? OR (timestamp = ? AND id < ?))
             ORDER BY timestamp DESC, id DESC
             LIMIT ?
            """,
            (ts, ts, message_id, context),
        ).fetchall()

        newer_ids = [r["id"] for r in newer]
        newer_ids.reverse()
        older_ids = [r["id"] for r in older]

        typed_ids = (
            [f"msg:{i}" for i in newer_ids]
            + [f"msg:{message_id}"]
            + [f"msg:{i}" for i in older_ids]
        )

        self.beginResetModel()
        self._rows = []
        self._loaded = 0
        self._search_ids = typed_ids
        self._mixed_hits = []
        self._total_full = len(typed_ids)
        self._total = len(typed_ids)
        self._query = ""
        self._mode = SearchMode.KEYWORD
        self._last_error = ""
        self._is_window = True
        self.endResetModel()
        self._load_page()

        return len(newer_ids)

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
        return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _load_page(self) -> None:
        if self._loaded >= self._total:
            return
        take = min(PAGE_SIZE, self._total - self._loaded)
        if self._search_ids:
            batch_ids = self._search_ids[self._loaded : self._loaded + take]
            message_rows = self._fetch_mixed_by_typed_ids(batch_ids)
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

    def _fetch_mixed_by_typed_ids(self, typed_ids: list[str]) -> list[MessageRow]:
        """Fetch rows whose ids are either 'msg:<id>' or 'doc:<page_id>'.

        Message IDs hit the regular messages path; doc IDs hit document_pages
        and get turned into a synthetic MessageRow with ``doc_meta`` set so
        the delegate renders them with a doc-style card.
        """
        msg_ids: list[str] = []
        doc_ids: list[int] = []
        order: list[tuple[str, str]] = []   # (kind, raw-id)
        for t in typed_ids:
            if t.startswith("msg:"):
                mid = t[4:]
                if mid:
                    msg_ids.append(mid)
                    order.append(("msg", mid))
            elif t.startswith("doc:"):
                try:
                    did = int(t[4:])
                except ValueError:
                    continue
                doc_ids.append(did)
                order.append(("doc", str(did)))
            else:
                # fallback: treat bare ids as message ids (keyword/visual path)
                if t:
                    msg_ids.append(t)
                    order.append(("msg", t))

        msg_by_id: dict[str, MessageRow] = {}
        if msg_ids:
            for r in self._fetch_by_ids(msg_ids, preserve_order=False):
                msg_by_id[r.id] = r

        doc_by_id: dict[str, MessageRow] = {}
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            rows = self._conn.execute(
                f"""
                SELECT p.id AS pid, p.page_num AS pnum, p.rendered_path AS rpath,
                       COALESCE(NULLIF(p.ocr_text,''), p.extracted_text) AS text,
                       d.id AS did, d.title AS title, d.filename AS fn,
                       d.author AS author, d.doc_type AS dtype
                  FROM document_pages p
                  JOIN documents d ON d.id = p.document_id
                 WHERE p.id IN ({placeholders})
                """,
                doc_ids,
            ).fetchall()
            for r in rows:
                doc_by_id[str(int(r["pid"]))] = _synthesize_doc_row(r)

        out: list[MessageRow] = []
        for kind, raw in order:
            if kind == "msg":
                row = msg_by_id.get(raw)
                if row:
                    out.append(row)
            else:
                row = doc_by_id.get(raw)
                if row:
                    out.append(row)
        return out

    def _fetch_browse(self, offset: int, limit: int) -> list[MessageRow]:
        where = f"WHERE {NOISE_FILTER_SQL}" if self._hide_noise else ""
        rows = self._conn.execute(
            f"""
            SELECT m.id, m.author_name, m.author_nickname, m.timestamp, m.content,
                   m.is_featured_speaker, m.is_pinned, m.reply_to_message_id AS reply_id,
                   parent.author_nickname AS reply_author_nick,
                   parent.author_name     AS reply_author_name,
                   SUBSTR(COALESCE(parent.content, ''), 1, 140) AS reply_snippet
              FROM messages m
              LEFT JOIN messages parent ON parent.id = m.reply_to_message_id
             {where}
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
