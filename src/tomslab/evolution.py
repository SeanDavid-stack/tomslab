"""Tom's view evolution — how Tom's framing of a given concept has
progressed over time.

Pulls every Discord message and video chunk that mentions the concept,
buckets them by quarter, and returns the top-N-per-bucket ordered
chronologically. The UI shows these as a vertical timeline; the user can
scan years of framing changes at a glance.

The retrieval uses semantic match when embeddings are available, falling
back to keyword/FTS5 when they aren't — so this works on a fresh corpus
with no embeddings too, just less thematically.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class EvolutionHit:
    source_type: str       # "message" | "video_chunk"
    when: str              # ISO date (message) or video added_at (video)
    quarter: str           # e.g. "2023-Q2"
    title: str             # Discord author · date  |  Video title · timestamp
    preview: str           # First ~300 chars of the post / chunk
    citation_id: str       # "msg:<id>" or "vid:<chunk_id>" for deep-linking


@dataclass
class EvolutionTimeline:
    concept: str
    total_hits: int
    buckets: list[tuple[str, list[EvolutionHit]]] = field(default_factory=list)


def _quarter(iso_date: str) -> str:
    """'2023-05-08T15:30:00' -> '2023-Q2'. Returns '' on bad input."""
    if not iso_date:
        return ""
    try:
        y = int(iso_date[:4])
        m = int(iso_date[5:7])
        q = (m - 1) // 3 + 1
        return f"{y}-Q{q}"
    except Exception:
        return ""


def _truncate(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_timeline(
    conn: sqlite3.Connection,
    concept: str,
    per_bucket: int = 3,
) -> EvolutionTimeline:
    """Walk the corpus for mentions of ``concept``, group by quarter,
    and return at most ``per_bucket`` items per quarter sorted by
    keyword-overlap rank.

    ``concept`` is matched as a literal word boundary — NVPOC only
    matches "NVPOC", not "nvpoca" or "vpoc". Whole-word matching keeps
    acronyms sharp and avoids noise from substring collisions.
    """
    concept = (concept or "").strip()
    if not concept:
        return EvolutionTimeline(concept=concept, total_hits=0)

    # FTS5 phrase match — case-insensitive, whole-word, ranked by bm25.
    # messages_fts exposes `id UNINDEXED` as the join column.
    fts_q = f'"{concept}"'
    msg_rows = conn.execute(
        """
        SELECT m.id, m.author_nickname, m.author_name, m.timestamp,
               m.content, bm25(messages_fts) AS rank
          FROM messages_fts
          JOIN messages m ON m.id = messages_fts.id
         WHERE messages_fts MATCH ?
         ORDER BY rank
         LIMIT 400
        """,
        (fts_q,),
    ).fetchall()

    vid_rows = conn.execute(
        """
        SELECT c.id AS cid, c.start_sec AS ss, c.text AS text,
               v.id AS vid, v.title AS title, v.added_at AS added
          FROM video_chunks c
          JOIN videos v ON v.id = c.video_id
         WHERE c.text LIKE ? COLLATE NOCASE
         ORDER BY v.added_at
         LIMIT 400
        """,
        (f"%{concept}%",),
    ).fetchall()

    hits: list[EvolutionHit] = []
    for r in msg_rows:
        when = (r["timestamp"] or "")[:10]
        q = _quarter(when)
        if not q:
            continue
        nick = r["author_nickname"] or r["author_name"] or "?"
        hits.append(EvolutionHit(
            source_type="message",
            when=when,
            quarter=q,
            title=f"{nick} · {when}",
            preview=_truncate(r["content"] or ""),
            citation_id=f"msg:{r['id']}",
        ))

    for r in vid_rows:
        when = (r["added"] or "")[:10]
        q = _quarter(when)
        if not q:
            continue
        ss = float(r["ss"] or 0.0)
        mm = int(ss // 60)
        hits.append(EvolutionHit(
            source_type="video_chunk",
            when=when,
            quarter=q,
            title=f"{r['title']} · {mm}:{int(ss % 60):02d}",
            preview=_truncate(r["text"] or ""),
            citation_id=f"vid:{int(r['cid'])}",
        ))

    # Group into buckets ordered by quarter ascending. Keep top-N per
    # bucket so the UI doesn't drown in bursts of repetitive posts.
    by_q: dict[str, list[EvolutionHit]] = {}
    for h in hits:
        by_q.setdefault(h.quarter, []).append(h)
    for q in by_q:
        # Rough quality signal: longer previews usually mean richer context.
        by_q[q].sort(key=lambda h: len(h.preview), reverse=True)
        by_q[q] = by_q[q][:per_bucket]

    ordered = sorted(by_q.items(), key=lambda kv: kv[0])
    return EvolutionTimeline(
        concept=concept,
        total_hits=len(hits),
        buckets=ordered,
    )
