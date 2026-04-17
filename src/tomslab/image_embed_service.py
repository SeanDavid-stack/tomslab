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


def embed_pending(
    conn: sqlite3.Connection,
    progress: ProgressFn = _noop,
    batch_size: int = DEFAULT_BATCH,
) -> int:
    bundle = visual.ensure_loaded(conn)
    model_tag = f"{bundle.model_name}:{bundle.pretrained}"
    total = visual.pending_count(conn, model_tag)
    if total == 0:
        progress(0, 0, "All attachments already embedded.")
        return 0

    progress(0, total, f"CLIP-embedding with {model_tag} ({bundle.dim}-dim) on {bundle.device}…")

    done = 0
    dim = bundle.dim
    while True:
        items = pending_items(conn, model_tag, batch_size)
        if not items:
            break

        pairs = visual.embed_image_paths(bundle, items)
        if not pairs:
            # Nothing embeddable in this batch — mark them all so we don't retry forever.
            # Use a zero-length sentinel embedding (dim==0) we'll ignore at search time.
            # Simpler: just break. Leaving them pending means a later retry might work
            # after the user fixes paths, so we'd rather break on full-batch failure.
            log.warning("batch of %d produced no embeddings; stopping", len(items))
            break

        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (aid, model_tag, dim, arr.tobytes(), now)
            for aid, arr in pairs
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO image_embeddings("
            "attachment_id, model, dim, embedding, generated_at"
            ") VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        done += len(pairs)
        progress(done, total, f"{done:,} / {total:,}")

    progress(total, total, "Done")
    return done
