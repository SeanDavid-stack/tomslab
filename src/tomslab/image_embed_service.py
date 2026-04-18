"""Batch CLIP-embed every attachment that isn't yet in image_embeddings.

Resumable — re-running picks up unfinished work.  Runs in-process; the
UI layer wraps this in a QThread.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable

from tomslab import visual

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]

DEFAULT_BATCH = 32   # Vit-B-32 on a 3080 Ti handles 32 comfortably


def _noop(_d: int, _t: int, _s: str) -> None:
    pass


def pending_items(
    conn: sqlite3.Connection, model_tag: str, limit: int
) -> list[tuple[str, str]]:
    rows = conn.execute(visual.pending_items_sql(), (model_tag, limit)).fetchall()
    return [(r["id"], r["path"]) for r in rows]


def pending_doc_page_items(
    conn: sqlite3.Connection, model_tag: str, limit: int
) -> list[tuple[int, str]]:
    rows = conn.execute(
        """
        SELECT p.id AS id, p.rendered_path AS path
          FROM document_pages p
         WHERE p.rendered_path IS NOT NULL AND p.rendered_path != ''
           AND NOT EXISTS (
                SELECT 1 FROM doc_page_image_embeddings de
                 WHERE de.page_id = p.id AND de.model = ?
           )
         ORDER BY p.id
         LIMIT ?
        """,
        (model_tag, limit),
    ).fetchall()
    return [(int(r["id"]), r["path"]) for r in rows]


def embed_pending(
    conn: sqlite3.Connection,
    progress: ProgressFn = _noop,
    batch_size: int = DEFAULT_BATCH,
) -> int:
    bundle = visual.ensure_loaded(conn)
    model_tag = f"{bundle.model_name}:{bundle.pretrained}"
    total_att = visual.pending_count(conn, model_tag)
    total_doc = visual.pending_doc_page_count(conn, model_tag)
    total = total_att + total_doc
    if total == 0:
        progress(0, 0, "All attachments and doc pages already embedded.")
        return 0

    progress(0, total, f"CLIP-embedding with {model_tag} ({bundle.dim}-dim) on {bundle.device}…")

    done = 0
    dim = bundle.dim

    # ---- 1) attachments ----------------------------------------------------
    while True:
        items = pending_items(conn, model_tag, batch_size)
        if not items:
            break

        pairs = visual.embed_image_paths(bundle, items)
        if not pairs:
            log.warning("attachment batch of %d produced no embeddings; stopping",
                        len(items))
            break

        now = datetime.now(timezone.utc).isoformat()
        rows = [(aid, model_tag, dim, arr.tobytes(), now) for aid, arr in pairs]
        conn.executemany(
            "INSERT OR REPLACE INTO image_embeddings("
            "attachment_id, model, dim, embedding, generated_at"
            ") VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        done += len(pairs)
        progress(done, total, f"{done:,} / {total:,}  (charts)")

    # ---- 2) doc page images ------------------------------------------------
    while True:
        items = pending_doc_page_items(conn, model_tag, batch_size)
        if not items:
            break

        # reuse the same embed path — it takes (id, path) pairs.
        id_as_str = [(str(i), p) for i, p in items]
        pairs = visual.embed_image_paths(bundle, id_as_str)
        if not pairs:
            log.warning("doc-page batch of %d produced no embeddings; stopping",
                        len(items))
            break

        now = datetime.now(timezone.utc).isoformat()
        rows = [(int(sid), model_tag, dim, arr.tobytes(), now) for sid, arr in pairs]
        conn.executemany(
            "INSERT OR REPLACE INTO doc_page_image_embeddings("
            "page_id, model, dim, embedding, generated_at"
            ") VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        done += len(pairs)
        progress(done, total, f"{done:,} / {total:,}  (doc pages)")

    progress(total, total, "Done")
    return done
