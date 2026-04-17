"""Conversation-window builder.

For every featured-speaker message, we build one window consisting of
N messages before and M after (chronological, same channel). The combined
text of that window is what we'll embed for semantic search in Phase 3.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from tomslab import db as dbmod

ProgressFn = Callable[[int, int], None]


def _noop(_i: int, _n: int) -> None:
    pass


def build_windows(
    conn: sqlite3.Connection,
    featured_speaker: str,
    channel_id: str | None = None,
    progress: ProgressFn = _noop,
) -> int:
    """(Re)build conversation_windows for featured-speaker messages.

    Incremental: windows already present for a given anchor_message_id are
    left alone. Returns the number of NEW windows built.
    """
    before = int(dbmod.get_setting(conn, "conversation_window_before", "3") or 3)
    after = int(dbmod.get_setting(conn, "conversation_window_after", "5") or 5)

    # All messages in this channel, chronologically. Keep it in-memory — even
    # 200K rows is ~15 MB of (id, ts, author, content, image_count) tuples.
    where = "WHERE channel_id = ?" if channel_id else ""
    params: tuple = (channel_id,) if channel_id else ()

    rows = conn.execute(
        f"""
        SELECT m.id, m.timestamp, m.author_name, m.content,
               (SELECT COUNT(*) FROM attachments a WHERE a.message_id = m.id) AS image_count
          FROM messages m
          {where}
          ORDER BY m.timestamp, m.id
        """,
        params,
    ).fetchall()

    if not rows:
        return 0

    existing_anchors: set[str] = {
        r["anchor_message_id"]
        for r in conn.execute(
            "SELECT anchor_message_id FROM conversation_windows"
        ).fetchall()
    }

    anchor_indices = [
        i for i, r in enumerate(rows) if r["author_name"] == featured_speaker
    ]
    total = len(anchor_indices)
    built = 0
    to_insert: list[tuple] = []

    for n, i in enumerate(anchor_indices, start=1):
        anchor = rows[i]
        if anchor["id"] in existing_anchors:
            if n % 500 == 0:
                progress(n, total)
            continue

        lo = max(0, i - before)
        hi = min(len(rows), i + after + 1)
        window_rows = rows[lo:hi]

        text = _combine(window_rows)
        image_count = sum(int(r["image_count"] or 0) for r in window_rows)

        to_insert.append(
            (
                anchor["id"],
                window_rows[0]["id"],
                window_rows[-1]["id"],
                text,
                image_count,
            )
        )
        built += 1

        if len(to_insert) >= 500:
            conn.executemany(
                "INSERT INTO conversation_windows("
                "anchor_message_id, start_message_id, end_message_id, text_combined, image_count"
                ") VALUES (?,?,?,?,?)",
                to_insert,
            )
            to_insert.clear()
            conn.commit()

        if n % 500 == 0 or n == total:
            progress(n, total)

    if to_insert:
        conn.executemany(
            "INSERT INTO conversation_windows("
            "anchor_message_id, start_message_id, end_message_id, text_combined, image_count"
            ") VALUES (?,?,?,?,?)",
            to_insert,
        )
        conn.commit()

    progress(total, total)
    return built


def _combine(window_rows: list[sqlite3.Row]) -> str:
    pieces = []
    for r in window_rows:
        content = (r["content"] or "").strip()
        if not content:
            continue
        pieces.append(f"{r['author_name']}: {content}")
    return "\n".join(pieces)
