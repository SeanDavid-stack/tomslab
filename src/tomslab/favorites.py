"""User-curated list of high-signal Discord authors.

Right-click an avatar in the Feed → Save / Unsave favorite. The list is
read to put a gold ★ badge on favorite authors' avatars and to power a
'Favorites only' filter chip on the Feed.

Intentionally tiny — no notes, no tags. Favoriting is a binary 'I find
this person's insights worth reading'. The DB schema matches: one row
per author, stored by author_name (stable Discord handle) with
author_nickname cached for display.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def is_favorite(conn: sqlite3.Connection, author_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM favorite_authors WHERE author_name = ?",
        (author_name,),
    ).fetchone()
    return row is not None


def add_favorite(
    conn: sqlite3.Connection,
    author_name: str,
    author_nickname: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO favorite_authors"
        " (author_name, author_nickname, added_at) VALUES (?, ?, ?)",
        (
            author_name,
            author_nickname or author_name,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def remove_favorite(conn: sqlite3.Connection, author_name: str) -> None:
    conn.execute(
        "DELETE FROM favorite_authors WHERE author_name = ?",
        (author_name,),
    )
    conn.commit()


def all_favorites(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT author_name, author_nickname, added_at
          FROM favorite_authors
         ORDER BY LOWER(COALESCE(author_nickname, author_name))
        """
    ).fetchall()
    return [dict(r) for r in rows]


def favorite_name_set(conn: sqlite3.Connection) -> set[str]:
    """Cheap probe used by the delegate to decide whether to draw the
    gold ★ on an avatar. Returns author_name set."""
    return {r["author_name"] for r in conn.execute(
        "SELECT author_name FROM favorite_authors"
    )}
