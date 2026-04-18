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
class VideoSemanticHit:
    """A semantic hit that points at a timestamped chunk of a YouTube video."""
    chunk_id: int
    video_id: str
    video_title: str
    video_url: str
    start_sec: float
    end_sec: float
    score: float


@dataclass
class MixedSemanticHit:
    source_type: str   # 'message' | 'doc_page' | 'video_chunk'
    score: float
    # populated for 'message'
    message_id: str | None = None
    # populated for 'doc_page'
    doc_page: DocSemanticHit | None = None
    # populated for 'video_chunk'
    video: VideoSemanticHit | None = None


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


class _VideoCache:
    """Cached CLIP-ish matrix for Tom's YouTube chunks (text embeddings)."""
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.signature: tuple | None = None
        self.matrix: np.ndarray | None = None
        self.chunk_ids: list[int] | None = None
        self.meta: list[dict] | None = None   # doc-like metadata per row


_video_cache = _VideoCache()


def invalidate_video_cache() -> None:
    with _video_cache.lock:
        _video_cache.signature = None
        _video_cache.matrix = None
        _video_cache.chunk_ids = None
        _video_cache.meta = None


def _current_video_signature(conn: sqlite3.Connection) -> tuple:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(generated_at) AS t FROM video_chunk_embeddings"
    ).fetchone()
    return (int(row["n"] or 0), row["t"] or "")


def videos_available(conn: sqlite3.Connection) -> bool:
    return _current_video_signature(conn)[0] > 0


def _load_video_matrix(conn: sqlite3.Connection) -> bool:
    sig = _current_video_signature(conn)
    if _video_cache.signature == sig and _video_cache.matrix is not None:
        return True
    if sig[0] == 0:
        _video_cache.signature = sig
        _video_cache.matrix = None
        return False

    rows = conn.execute(
        """
        SELECT ve.chunk_id AS cid, ve.dim AS dim, ve.embedding AS blob,
               c.video_id AS vid, c.start_sec AS ss, c.end_sec AS es,
               v.title AS title, v.url AS url
          FROM video_chunk_embeddings ve
          JOIN video_chunks c ON c.id = ve.chunk_id
          JOIN videos v ON v.id = c.video_id
        """
    ).fetchall()
    if not rows:
        _video_cache.signature = sig
        _video_cache.matrix = None
        return False

    dim = int(rows[0]["dim"])
    n = len(rows)
    mat = np.empty((n, dim), dtype=np.float32)
    cids = [0] * n
    meta: list[dict] = [{}] * n
    for i, r in enumerate(rows):
        v = np.frombuffer(r["blob"], dtype=np.float32)
        if v.size != dim:
            v = v[:dim] if v.size > dim else np.pad(v, (0, dim - v.size))
        mat[i] = v
        cids[i] = int(r["cid"])
        meta[i] = {
            "video_id": r["vid"],
            "start_sec": float(r["ss"] or 0),
            "end_sec": float(r["es"] or 0),
            "title": r["title"] or "",
            "url": r["url"] or "",
        }
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms
    _video_cache.signature = sig
    _video_cache.matrix = mat
    _video_cache.chunk_ids = cids
    _video_cache.meta = meta
    return True


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


_TOM_BOOST = 0.18        # tom_b-authored PDFs jump to the front for definitional queries
_THIRD_PARTY_BOOST = 0.0  # third-party books ride at native cosine (no artificial lift)
_UNKNOWN_DOC_BOOST = 0.02


def _doc_author_boost(author: str) -> float:
    if author == "tom_b":
        return _TOM_BOOST
    if author == "third_party":
        return _THIRD_PARTY_BOOST
    return _UNKNOWN_DOC_BOOST


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
    conn: sqlite3.Connection, query: str, limit: int = 200, doc_boost: float | None = None
) -> list[MixedSemanticHit]:
    """Search Discord windows + PDF pages; merge by adjusted score.

    Boosts are per-doc-author (see ``_doc_author_boost``). Tom-authored
    PDFs get a strong lift so definitional queries about Tom's framework
    surface his pages before Discord chatter or third-party books.
    Pass ``doc_boost`` as a number to override with a flat boost.
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
        cosines = doc_mat @ q
        boosts = np.array(
            [_doc_author_boost(m["author"]) if doc_boost is None else doc_boost
             for m in doc_meta],
            dtype=np.float32,
        )
        scores = cosines + boosts
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

    # ---- video chunks -------------------------------------------------
    with _video_cache.lock:
        _load_video_matrix(conn)
        v_mat = _video_cache.matrix
        v_meta = _video_cache.meta
        v_cids = _video_cache.chunk_ids

    if v_mat is not None and v_meta is not None and v_cids is not None \
            and q.size == v_mat.shape[1]:
        # Tom B's own spoken teaching — give a strong boost, same tier as
        # his authored PDFs, so video chunks surface when relevant.
        vscores = v_mat @ q + 0.18
        k = min(limit, vscores.size)
        top = np.argpartition(-vscores, k - 1)[:k]
        top = top[np.argsort(-vscores[top])]
        for i in top:
            idx = int(i)
            m = v_meta[idx]
            hits.append(MixedSemanticHit(
                source_type="video_chunk",
                score=float(vscores[idx]),
                video=VideoSemanticHit(
                    chunk_id=v_cids[idx],
                    video_id=m["video_id"],
                    video_title=m["title"],
                    video_url=m["url"],
                    start_sec=m["start_sec"],
                    end_sec=m["end_sec"],
                    score=float(vscores[idx]),
                ),
            ))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
