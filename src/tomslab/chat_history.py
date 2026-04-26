"""Rolling chat history — auto-saved per Q/A, separate from bookmarks.

Every Ask Tom turn is recorded here automatically so the user can
re-open recent questions from the sidebar without hunting. Explicit
``⭐ Save`` still goes to ``bookmarks`` for permanent keeping.

Design decisions:
  * The user can clear history any time (it's not bookmarks).
  * We trim to ``MAX_ROWS`` on every insert to bound storage.
  * Snapshot the retrieval settings at ask-time so a re-ask can
    faithfully restore the exact configuration the answer was
    generated under.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from tomslab import db as dbmod


MAX_ROWS = 200        # oldest rows beyond this get pruned on insert


@dataclass
class HistoryEntry:
    id: int
    question: str
    answer: str
    citations: list[str]
    primary_provider: str
    tom_only: bool
    k_discord_tom: int | None
    k_discord_other: int | None
    asked_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(
    conn: sqlite3.Connection,
    *,
    question: str,
    answer: str,
    citations: list[str] | None = None,
) -> int:
    """Insert a new (question, answer) row and return its id.

    Snapshots the current retrieval settings so a later re-ask can
    reproduce the same configuration even if the user has since
    changed Tom-only or the K counts."""
    citations = citations or []
    primary = dbmod.get_setting(conn, "ai_provider_chat", "ollama") or "ollama"
    tom_only = (dbmod.get_setting(conn, "chat_tom_only", "0") or "0") == "1"
    try:
        k_tom = int(dbmod.get_setting(conn, "chat_k_discord_tom", "5") or 5)
    except ValueError:
        k_tom = 5
    try:
        k_other = int(dbmod.get_setting(conn, "chat_k_discord_other", "4") or 4)
    except ValueError:
        k_other = 4
    cur = conn.execute(
        """
        INSERT INTO chat_history
          (question, answer, citations, primary_provider,
           tom_only, k_discord_tom, k_discord_other, asked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (question, answer, json.dumps(citations), primary,
         1 if tom_only else 0, k_tom, k_other, _now()),
    )
    conn.commit()
    _prune(conn)
    return int(cur.lastrowid or 0)


def _prune(conn: sqlite3.Connection) -> None:
    """Drop rows older than MAX_ROWS by asked_at."""
    conn.execute(
        "DELETE FROM chat_history WHERE id IN ("
        "  SELECT id FROM chat_history ORDER BY asked_at DESC LIMIT -1 OFFSET ?"
        ")",
        (MAX_ROWS,),
    )
    conn.commit()


def recent(conn: sqlite3.Connection, limit: int = 50) -> list[HistoryEntry]:
    rows = conn.execute(
        "SELECT id, question, answer, citations, primary_provider, "
        "       tom_only, k_discord_tom, k_discord_other, asked_at "
        "  FROM chat_history ORDER BY asked_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out: list[HistoryEntry] = []
    for r in rows:
        try:
            cites = json.loads(r["citations"] or "[]")
        except Exception:
            cites = []
        out.append(HistoryEntry(
            id=int(r["id"]),
            question=r["question"] or "",
            answer=r["answer"] or "",
            citations=list(cites),
            primary_provider=r["primary_provider"] or "",
            tom_only=bool(r["tom_only"]),
            k_discord_tom=r["k_discord_tom"],
            k_discord_other=r["k_discord_other"],
            asked_at=r["asked_at"] or "",
        ))
    return out


def get(conn: sqlite3.Connection, entry_id: int) -> HistoryEntry | None:
    row = conn.execute(
        "SELECT id, question, answer, citations, primary_provider, "
        "       tom_only, k_discord_tom, k_discord_other, asked_at "
        "  FROM chat_history WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if not row:
        return None
    try:
        cites = json.loads(row["citations"] or "[]")
    except Exception:
        cites = []
    return HistoryEntry(
        id=int(row["id"]),
        question=row["question"] or "",
        answer=row["answer"] or "",
        citations=list(cites),
        primary_provider=row["primary_provider"] or "",
        tom_only=bool(row["tom_only"]),
        k_discord_tom=row["k_discord_tom"],
        k_discord_other=row["k_discord_other"],
        asked_at=row["asked_at"] or "",
    )


def clear(conn: sqlite3.Connection) -> int:
    """Wipe all history rows. Returns count deleted."""
    cur = conn.execute("DELETE FROM chat_history")
    conn.commit()
    return cur.rowcount or 0
