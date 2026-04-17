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


@dataclass
class DocSemanticHit:
    """A semantic hit that points at a page in a reference PDF."""
    page_id: int
    document_id: int
    page_num: int
    filename: str
    title: str
    author: str
    score: float


@dataclass
class MixedSemanticHit:
    source_type: str   # 'message' | 'doc_page'
    score: float
    # populated for 'message'
    message_id: str | None = None
    # populated for 'doc_page'
    doc_page: DocSemanticHit | None = None


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


# ---------------------------------------------------------------------------
# Mixed search: Discord windows + PDF doc pages, merged by score
# ---------------------------------------------------------------------------
class _DocCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.signature: tuple | None = None
        self.matrix: np.ndarray | None = None
        self.page_ids: list[int] | None = None
        self.meta: list[dict] | None = None   # parallel: doc_id, page_num, filename, title, author


_doc_cache = _DocCache()


def invalidate_doc_cache() -> None:
    with _doc_cache.lock:
        _doc_cache.signature = None
        _doc_cache.matrix = None
        _doc_cache.page_ids = None
        _doc_cache.meta = None


def _current_doc_signature(conn: sqlite3.Connection) -> tuple:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(generated_at) AS t FROM document_page_embeddings"
    ).fetchone()
    return (int(row["n"] or 0), row["t"] or "")


def docs_available(conn: sqlite3.Connection) -> bool:
    return _current_doc_signature(conn)[0] > 0


def _load_doc_matrix(conn: sqlite3.Connection) -> bool:
    sig = _current_doc_signature(conn)
    if _doc_cache.signature == sig and _doc_cache.matrix is not None:
        return True
    if sig[0] == 0:
        _doc_cache.signature = sig
        _doc_cache.matrix = None
        return False

    rows = conn.execute(
        """
        SELECT pe.page_id AS pid, pe.dim AS dim, pe.embedding AS blob,
               p.document_id AS did, p.page_num AS pnum,
               d.filename AS fn, d.title AS title, d.author AS author
          FROM document_page_embeddings pe
          JOIN document_pages p ON p.id = pe.page_id
          JOIN documents d ON d.id = p.document_id
        """
    ).fetchall()
    if not rows:
        _doc_cache.signature = sig
        _doc_cache.matrix = None
        return False

    dim = int(rows[0]["dim"])
    n = len(rows)
    mat = np.empty((n, dim), dtype=np.float32)
    page_ids = [0] * n
    meta: list[dict] = [{}] * n
    for i, r in enumerate(rows):
        v = np.frombuffer(r["blob"], dtype=np.float32)
        if v.size != dim:
            v = v[:dim] if v.size > dim else np.pad(v, (0, dim - v.size))
        mat[i] = v
        page_ids[i] = int(r["pid"])
        meta[i] = {
            "doc_id": int(r["did"]),
            "page_num": int(r["pnum"]),
            "filename": r["fn"] or "",
            "title": r["title"] or "",
            "author": r["author"] or "",
        }

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms

    _doc_cache.signature = sig
    _doc_cache.matrix = mat
    _doc_cache.page_ids = page_ids
    _doc_cache.meta = meta
    return True


def mixed_semantic_search(
    conn: sqlite3.Connection, query: str, limit: int = 200, doc_boost: float = 0.05
) -> list[MixedSemanticHit]:
    """Search Discord windows and PDF pages, returning the merged top-scored hits.

    ``doc_boost`` gently prioritises doc pages since they are authored
    definitions of Tom's framework — tiebreakers go to the doc, not the chat.
    """
    query = (query or "").strip()
    if not query:
        return []

    # ensure matrices loaded + warm
    with _cache.lock:
        _load_matrix(conn)
        msg_mat = _cache.matrix
        msg_anchors = _cache.anchor_ids

    with _doc_cache.lock:
        _load_doc_matrix(conn)
        doc_mat = _doc_cache.matrix
        doc_meta = _doc_cache.meta
        doc_page_ids = _doc_cache.page_ids

    if msg_mat is None and doc_mat is None:
        return []

    # Embed the query once using the same provider that built the message index.
    # We assume message + doc indexes share the same model (both Ollama/nomic).
    provider = registry.get_embed_provider(conn)
    qv = provider.embed_texts([query])[0]
    q = np.asarray(qv, dtype=np.float32)
    nrm = float(np.linalg.norm(q))
    if nrm == 0:
        return []
    q = q / nrm

    hits: list[MixedSemanticHit] = []

    if msg_mat is not None and msg_anchors is not None and q.size == msg_mat.shape[1]:
        scores = msg_mat @ q
        k = min(limit, scores.size)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        seen: set[str] = set()
        for i in top:
            mid = msg_anchors[int(i)]
            if not mid or mid in seen:
                continue
            seen.add(mid)
            hits.append(MixedSemanticHit(
                source_type="message",
                score=float(scores[int(i)]),
                message_id=mid,
            ))

    if doc_mat is not None and doc_meta is not None and doc_page_ids is not None \
            and q.size == doc_mat.shape[1]:
        scores = doc_mat @ q + doc_boost
        k = min(limit, scores.size)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        for i in top:
            meta = doc_meta[int(i)]
            hits.append(MixedSemanticHit(
                source_type="doc_page",
                score=float(scores[int(i)]),
                doc_page=DocSemanticHit(
                    page_id=doc_page_ids[int(i)],
                    document_id=meta["doc_id"],
                    page_num=meta["page_num"],
                    filename=meta["filename"],
                    title=meta["title"],
                    author=meta["author"],
                    score=float(scores[int(i)]),
                ),
            ))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
