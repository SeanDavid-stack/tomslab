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
    SEMANTIC = "semantic"
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


def keyword_search_ids_broad(
    conn: sqlite3.Connection, user_text: str, limit: int
) -> list[str]:
    """Broad FTS5 match used by the chat retriever.

    Unlike :func:`keyword_search_ids` (which ANDs every token — right for
    the search bar), this OR-joins only the *extracted* keywords so
    stopwords in a question like "what does Tom mean by absorption"
    don't eliminate otherwise strong hits.
    """
    kws = extract_keywords(user_text)
    if not kws:
        return []
    pieces: list[str] = []
    for kw in kws:
        cleaned = kw.replace('"', '""')
        if len(cleaned) >= 2:
            pieces.append(f'"{cleaned}"*')
        else:
            pieces.append(f'"{cleaned}"')
    q = " OR ".join(pieces)
    try:
        rows = conn.execute(
            """
            SELECT fts.id
              FROM messages_fts fts
             WHERE messages_fts MATCH ?
             ORDER BY bm25(messages_fts) ASC
             LIMIT ?
            """,
            (q, limit),
        ).fetchall()
    except Exception:
        return []
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# Keyword retrieval for doc pages (no FTS5 table for docs, so LIKE is fine
# at this scale — 591 rows scanned in < 5 ms).
# ---------------------------------------------------------------------------
_STOPWORDS: set[str] = {
    "a", "an", "and", "or", "is", "are", "was", "were", "be", "been", "being",
    "the", "of", "to", "for", "in", "on", "at", "by", "with", "from",
    "this", "that", "these", "those", "it", "its", "as", "but", "if", "then",
    "do", "does", "did", "doing", "done",
    "have", "has", "had", "having",
    "what", "when", "where", "why", "how", "who", "whom", "which",
    "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "our", "their", "so", "than", "too", "very",
    "about", "into", "like", "mean", "means", "use", "using", "used", "get",
    "go", "tom", "toms", "tom's",   # drop "tom" — every query is about him
}


def extract_keywords(user_text: str) -> list[str]:
    """Tokenise a user question into probable signal words.

    Lower-cased, deduped, stop-worded, length >= 2.  Short acronyms
    like "IB" / "VA" / "RTH" are preserved because they're the whole
    point of a trading-concept query.
    """
    if not user_text:
        return []
    # Letters/digits/hyphens only — apostrophes are stripped at the regex level
    # so "Tom's" becomes "Tom" and "VPOC'" becomes "VPOC".
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", user_text)
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        low = tok.lower()
        # strip possessive 's if present
        if low.endswith("s") and len(low) > 3 and low[-2] == "'":
            low = low[:-2]
        if len(low) < 2:
            continue
        if low in _STOPWORDS:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(tok)
    return out


def keyword_search_doc_page_ids(
    conn: sqlite3.Connection,
    user_text: str,
    limit: int = 10,
) -> list[int]:
    """Rank document_pages by how many of the query's keywords they contain.

    Scans all 591 pages — fine at this scale, query is under 5 ms.
    Uses a single prepared statement with parameterised LIKE patterns.
    """
    kws = extract_keywords(user_text)
    if not kws:
        return []

    # Build the SCORE expression and the WHERE filter separately. Both use
    # the same combined-text expression so a term that hits anywhere in the
    # page (OCR or extracted) counts.
    combined = (
        "LOWER(COALESCE(ocr_text,'') || ' ' || COALESCE(extracted_text,''))"
    )
    per_term = f"(CASE WHEN {combined} LIKE ? THEN 1 ELSE 0 END)"
    score_expr = " + ".join([per_term] * len(kws))
    where_expr = " OR ".join([f"{combined} LIKE ?"] * len(kws))

    patterns = [f"%{kw.lower()}%" for kw in kws]

    sql = (
        "SELECT id AS pid, (" + score_expr + ") AS score "
        "FROM document_pages "
        "WHERE " + where_expr + " "
        "ORDER BY score DESC LIMIT ?"
    )
    rows = conn.execute(sql, patterns + patterns + [limit]).fetchall()
    return [int(r["pid"]) for r in rows if int(r["score"] or 0) > 0]
