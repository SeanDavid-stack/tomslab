"""User bookmarks.

Two distinct kinds of bookmark, both user-initiated:

- **message bookmark**: the user clicked the star on any row in the Feed
  (or Ask-Tom popover). Stored in the long-existing ``bookmarks`` table
  (one row per saved Discord message).
- **saved answer**: the user saved an Ask-Tom chat answer. Stored in a
  separate ``chat_bookmarks`` table because the content isn't a Discord
  message — it's a (question, answer, citations) triple.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations TEXT,
    note TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_bookmarks_created ON chat_bookmarks(created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


@dataclass
class MessageBookmark:
    id: int
    message_id: str
    note: str
    tags: str
    created_at: str


@dataclass
class ChatBookmark:
    id: int
    question: str
    answer: str
    citations: str
    note: str
    created_at: str


# ---------------------------------------------------------------------------
# message bookmarks
# ---------------------------------------------------------------------------
def all_message_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT message_id FROM bookmarks").fetchall()
    return {r["message_id"] for r in rows}


def toggle_message(conn: sqlite3.Connection, message_id: str) -> bool:
    """Toggle a message bookmark. Returns True if now bookmarked."""
    row = conn.execute(
        "SELECT id FROM bookmarks WHERE message_id = ? LIMIT 1", (message_id,)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM bookmarks WHERE id = ?", (row["id"],))
        conn.commit()
        return False
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO bookmarks(message_id, note, tags, created_at) VALUES (?,?,?,?)",
        (message_id, "", "", now),
    )
    conn.commit()
    return True


def list_message_bookmarks(conn: sqlite3.Connection) -> list[MessageBookmark]:
    rows = conn.execute(
        "SELECT id, message_id, note, tags, created_at FROM bookmarks "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [
        MessageBookmark(
            id=r["id"],
            message_id=r["message_id"],
            note=r["note"] or "",
            tags=r["tags"] or "",
            created_at=r["created_at"] or "",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# chat bookmarks
# ---------------------------------------------------------------------------
def save_chat_answer(
    conn: sqlite3.Connection,
    question: str,
    answer: str,
    citations: list[str] | None = None,
) -> int:
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    cits = ",".join(citations or [])
    cur = conn.execute(
        "INSERT INTO chat_bookmarks(question, answer, citations, created_at) "
        "VALUES (?,?,?,?)",
        (question or "", answer or "", cits, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_chat_bookmarks(conn: sqlite3.Connection) -> list[ChatBookmark]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, question, answer, citations, note, created_at "
        "FROM chat_bookmarks ORDER BY created_at DESC"
    ).fetchall()
    return [
        ChatBookmark(
            id=r["id"],
            question=r["question"] or "",
            answer=r["answer"] or "",
            citations=r["citations"] or "",
            note=r["note"] or "",
            created_at=r["created_at"] or "",
        )
        for r in rows
    ]


def delete_chat_bookmark(conn: sqlite3.Connection, bookmark_id: int) -> None:
    conn.execute("DELETE FROM chat_bookmarks WHERE id = ?", (bookmark_id,))
    conn.commit()
