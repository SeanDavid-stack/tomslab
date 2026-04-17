"""Search queries for the UI.

Phase 2: keyword (FTS5) only.  Phase 3 will add semantic (embeddings) and
Phase 4 will add visual (CLIP).  This module is the seam.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from enum import Enum


class SearchMode(str, Enum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"   # Phase 3
    VISUAL = "visual"       # Phase 4


@dataclass
class SearchHit:
    message_id: str


# FTS5 special characters that must be quoted to treat as literals.
# We wrap every whitespace-separated token in double quotes so that user
# input like "Tom's" or "ES/NQ" doesn't throw a syntax error.
_TOKEN_RE = re.compile(r'"[^"]*"|\S+')


def build_fts_query(user_text: str) -> str:
    """Turn a raw user query into a safe FTS5 MATCH expression.

    Tokens are AND'd together implicitly (FTS5 default).  Quoted phrases
    in the user's input are preserved; bare tokens become prefix matches
    (trailing ``*``) so typing ``absorpt`` finds ``absorption``.
    """
    text = (user_text or "").strip()
    if not text:
        return ""

    pieces: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            # already quoted: keep as phrase, escape internal quotes
            inner = tok[1:-1].replace('"', '""')
            if inner:
                pieces.append(f'"{inner}"')
        else:
            cleaned = tok.replace('"', '""')
            if len(cleaned) >= 2:
                pieces.append(f'"{cleaned}"*')
            else:
                pieces.append(f'"{cleaned}"')
    return " ".join(pieces)


def count_keyword_hits(conn: sqlite3.Connection, user_text: str) -> int:
    q = build_fts_query(user_text)
    if not q:
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages_fts WHERE messages_fts MATCH ?",
        (q,),
    ).fetchone()
    return int(row["n"] or 0)


def keyword_search_ids(
    conn: sqlite3.Connection,
    user_text: str,
    limit: int,
    offset: int = 0,
) -> list[str]:
    """Return up to `limit` message IDs matching `user_text`, best-scored first.

    We sort by bm25() (built into FTS5) ascending — lower is better.
    """
    q = build_fts_query(user_text)
    if not q:
        return []
    rows = conn.execute(
        """
        SELECT fts.id
          FROM messages_fts fts
         WHERE messages_fts MATCH ?
         ORDER BY bm25(messages_fts) ASC
         LIMIT ? OFFSET ?
        """,
        (q, limit, offset),
    ).fetchall()
    return [r["id"] for r in rows]
