"""Batch-embed all conversation_windows that don't yet have an embedding.

Resumable — run it any time, it picks up where it left off.  Stores
vectors as raw float32 bytes in window_embeddings.embedding.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Callable

import numpy as np

from tomslab.ai.base import AIProvider, ProviderError

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]   # (done, total, status)

BATCH_SIZE = 200          # nomic-embed-text runs fine at this size; Gemini caps at ~100
MAX_TEXT_CHARS = 8000     # cap per window to keep request payloads sane


def _noop(_d: int, _t: int, _s: str) -> None:
    pass


def pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM conversation_windows w
         WHERE NOT EXISTS (SELECT 1 FROM window_embeddings we WHERE we.window_id = w.id)
        """
    ).fetchone()
    return int(row["n"] or 0)


def pending_doc_pages_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM document_pages p
         WHERE (COALESCE(p.ocr_text,'') || COALESCE(p.extracted_text,'')) != ''
           AND NOT EXISTS (
                SELECT 1 FROM document_page_embeddings pe WHERE pe.page_id = p.id
           )
        """
    ).fetchone()
    return int(row["n"] or 0)


def embed_pending(
    conn: sqlite3.Connection,
    provider: AIProvider,
    progress: ProgressFn = _noop,
    batch_size: int = BATCH_SIZE,
    rate_limit_rpm: int | None = None,
) -> int:
    """Embed every window that hasn't been embedded yet. Returns #embedded."""
    if not provider.supports_embed():
        raise ProviderError(f"{provider.name} does not support embeddings")

    model = provider.embedding_model_name()
    dim = provider.embedding_dim()

    total_pending = pending_count(conn)
    if total_pending == 0:
        progress(0, 0, "Nothing to embed.")
        return 0

    progress(0, total_pending, f"Embedding with {provider.name}/{model} ({dim}-dim)…")

    done = 0
    rate_gap = 60.0 / rate_limit_rpm if rate_limit_rpm else 0.0
    last_call = 0.0

    while True:
        rows = conn.execute(
            """
            SELECT w.id AS id, w.text_combined AS text
              FROM conversation_windows w
             WHERE NOT EXISTS (SELECT 1 FROM window_embeddings we WHERE we.window_id = w.id)
             ORDER BY w.id
             LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        if not rows:
            break

        texts = [(r["text"] or "")[:MAX_TEXT_CHARS] for r in rows]

        if rate_gap:
            wait = rate_gap - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)
        last_call = time.monotonic()

        vectors = provider.embed_texts(texts)
        if len(vectors) != len(rows):
            raise ProviderError(
                f"provider returned {len(vectors)} embeddings for {len(rows)} inputs"
            )

        now = datetime.now(timezone.utc).isoformat()
        payload = []
        for r, v in zip(rows, vectors):
            arr = np.asarray(v, dtype=np.float32)
            if arr.size != dim:
                dim = arr.size  # tolerate dynamic dim discovery
            payload.append((int(r["id"]), model, int(arr.size), arr.tobytes(), now))

        conn.executemany(
            "INSERT OR REPLACE INTO window_embeddings("
            "window_id, model, dim, embedding, generated_at"
            ") VALUES (?,?,?,?,?)",
            payload,
        )
        conn.commit()
        done += len(rows)
        progress(done, total_pending, f"{done:,} / {total_pending:,}")

    progress(total_pending, total_pending, "Done")
    return done


def embed_pending_doc_pages(
    conn: sqlite3.Connection,
    provider: AIProvider,
    progress: ProgressFn = _noop,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed every document page whose text exists but no embedding row does."""
    if not provider.supports_embed():
        raise ProviderError(f"{provider.name} does not support embeddings")

    model = provider.embedding_model_name()
    dim = provider.embedding_dim()

    total = pending_doc_pages_count(conn)
    if total == 0:
        progress(0, 0, "No doc pages need embedding.")
        return 0

    progress(0, total, f"Embedding doc pages with {provider.name}/{model}…")

    done = 0
    while True:
        rows = conn.execute(
            """
            SELECT p.id AS id,
                   COALESCE(NULLIF(p.ocr_text,''), p.extracted_text) AS text
              FROM document_pages p
             WHERE (COALESCE(p.ocr_text,'') || COALESCE(p.extracted_text,'')) != ''
               AND NOT EXISTS (
                    SELECT 1 FROM document_page_embeddings pe WHERE pe.page_id = p.id
               )
             ORDER BY p.id
             LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        if not rows:
            break

        texts = [(r["text"] or "")[:MAX_TEXT_CHARS] for r in rows]
        vectors = provider.embed_texts(texts)
        if len(vectors) != len(rows):
            raise ProviderError(
                f"provider returned {len(vectors)} embeddings for {len(rows)} inputs"
            )

        now = datetime.now(timezone.utc).isoformat()
        payload = []
        for r, v in zip(rows, vectors):
            arr = np.asarray(v, dtype=np.float32)
            if arr.size != dim:
                dim = arr.size
            payload.append((int(r["id"]), model, int(arr.size), arr.tobytes(), now))

        conn.executemany(
            "INSERT OR REPLACE INTO document_page_embeddings("
            "page_id, model, dim, embedding, generated_at"
            ") VALUES (?,?,?,?,?)",
            payload,
        )
        conn.commit()
        done += len(rows)
        progress(done, total, f"{done:,} / {total:,}")

    progress(total, total, "Done")
    return done


def pending_video_chunks_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM video_chunks c
         WHERE COALESCE(c.text, '') != ''
           AND NOT EXISTS (
                SELECT 1 FROM video_chunk_embeddings ve WHERE ve.chunk_id = c.id
           )
        """
    ).fetchone()
    return int(row["n"] or 0)


def embed_pending_video_chunks(
    conn: sqlite3.Connection,
    provider: AIProvider,
    progress: ProgressFn = _noop,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed every transcript chunk that hasn't been embedded yet. This is
    what makes Ask Tom able to surface video timestamps — without it the
    TomTube corpus is invisible to semantic search."""
    if not provider.supports_embed():
        raise ProviderError(f"{provider.name} does not support embeddings")

    model = provider.embedding_model_name()
    dim = provider.embedding_dim()

    total = pending_video_chunks_count(conn)
    if total == 0:
        progress(0, 0, "No video chunks need embedding.")
        return 0

    progress(0, total, f"Embedding video chunks with {provider.name}/{model}…")

    done = 0
    while True:
        rows = conn.execute(
            """
            SELECT c.id AS id, c.text AS text
              FROM video_chunks c
             WHERE COALESCE(c.text, '') != ''
               AND NOT EXISTS (
                    SELECT 1 FROM video_chunk_embeddings ve WHERE ve.chunk_id = c.id
               )
             ORDER BY c.id
             LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        if not rows:
            break

        texts = [(r["text"] or "")[:MAX_TEXT_CHARS] for r in rows]
        vectors = provider.embed_texts(texts)
        if len(vectors) != len(rows):
            raise ProviderError(
                f"provider returned {len(vectors)} embeddings for {len(rows)} inputs"
            )

        now = datetime.now(timezone.utc).isoformat()
        payload = []
        for r, v in zip(rows, vectors):
            arr = np.asarray(v, dtype=np.float32)
            if arr.size != dim:
                dim = arr.size
            payload.append((int(r["id"]), model, int(arr.size), arr.tobytes(), now))

        conn.executemany(
            "INSERT OR REPLACE INTO video_chunk_embeddings("
            "chunk_id, model, dim, embedding, generated_at"
            ") VALUES (?,?,?,?,?)",
            payload,
        )
        conn.commit()
        done += len(rows)
        progress(done, total, f"{done:,} / {total:,}")

    progress(total, total, "Done")
    return done
