"""Semantic (embedding-cosine) search over conversation_windows.

For our scale (~18K windows × 768 dims ≈ 55 MB) a single in-memory numpy
sweep is under 100ms — way simpler than wiring sqlite-vss.  The cache
invalidates when the number of embeddings or the active model changes.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import numpy as np

from tomslab import db as dbmod
from tomslab.ai import registry


@dataclass
class SemanticHit:
    message_id: str
    score: float


class _Cache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.signature: tuple | None = None
        self.window_ids: np.ndarray | None = None     # int64 array of window ids
        self.anchor_ids: list[str] | None = None      # message id per row, parallel to window_ids
        self.matrix: np.ndarray | None = None          # (N, D) float32, L2-normalised


_cache = _Cache()


def invalidate_cache() -> None:
    with _cache.lock:
        _cache.signature = None
        _cache.window_ids = None
        _cache.anchor_ids = None
        _cache.matrix = None


def _current_signature(conn: sqlite3.Connection) -> tuple:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(generated_at) AS t FROM window_embeddings"
    ).fetchone()
    return (int(row["n"] or 0), row["t"] or "")


def _load_matrix(conn: sqlite3.Connection) -> bool:
    sig = _current_signature(conn)
    if _cache.signature == sig and _cache.matrix is not None:
        return True
    if sig[0] == 0:
        _cache.signature = sig
        _cache.matrix = None
        _cache.window_ids = None
        _cache.anchor_ids = None
        return False

    rows = conn.execute(
        """
        SELECT we.window_id AS wid, we.dim AS dim, we.embedding AS blob,
               w.anchor_message_id AS mid
          FROM window_embeddings we
          JOIN conversation_windows w ON w.id = we.window_id
        """
    ).fetchall()
    if not rows:
        _cache.signature = sig
        _cache.matrix = None
        return False

    dim = int(rows[0]["dim"])
    n = len(rows)
    mat = np.empty((n, dim), dtype=np.float32)
    ids = np.empty(n, dtype=np.int64)
    anchors: list[str] = [""] * n
    for i, r in enumerate(rows):
        v = np.frombuffer(r["blob"], dtype=np.float32)
        if v.size != dim:
            # shouldn't happen within a single model, but be defensive
            v = v[:dim] if v.size > dim else np.pad(v, (0, dim - v.size))
        mat[i] = v
        ids[i] = int(r["wid"])
        anchors[i] = r["mid"] or ""

    # L2-normalise rows — cosine becomes a single dot product
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms

    _cache.signature = sig
    _cache.matrix = mat
    _cache.window_ids = ids
    _cache.anchor_ids = anchors
    return True


def available(conn: sqlite3.Connection) -> bool:
    """True if we have at least one embedding to search."""
    return _current_signature(conn)[0] > 0


def semantic_search(
    conn: sqlite3.Connection, query: str, limit: int = 200
) -> list[SemanticHit]:
    query = (query or "").strip()
    if not query:
        return []
    with _cache.lock:
        if not _load_matrix(conn):
            return []
        mat = _cache.matrix
        anchors = _cache.anchor_ids
        assert mat is not None and anchors is not None

    provider = registry.get_embed_provider(conn)
    qv = provider.embed_texts([query])[0]
    q = np.asarray(qv, dtype=np.float32)
    if q.size != mat.shape[1]:
        raise RuntimeError(
            f"query dim {q.size} doesn't match index dim {mat.shape[1]} — "
            "did the embed model change? Rebuild embeddings."
        )
    nrm = float(np.linalg.norm(q))
    if nrm == 0:
        return []
    q = q / nrm

    scores = mat @ q  # shape (N,)
    k = min(limit, scores.size)
    if k <= 0:
        return []
    top_idx = np.argpartition(-scores, k - 1)[:k]
    top_idx = top_idx[np.argsort(-scores[top_idx])]

    seen: set[str] = set()
    hits: list[SemanticHit] = []
    for i in top_idx:
        mid = anchors[int(i)]
        if not mid or mid in seen:
            continue
        seen.add(mid)
        hits.append(SemanticHit(message_id=mid, score=float(scores[int(i)])))
    return hits


def semantic_search_ids(
    conn: sqlite3.Connection, query: str, limit: int = 200
) -> list[str]:
    return [h.message_id for h in semantic_search(conn, query, limit=limit)]
