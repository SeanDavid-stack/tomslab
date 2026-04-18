"""CLIP (joint image-text) embeddings + visual search.

Uses open_clip — a clean, maintained re-implementation of OpenAI's
original CLIP.  Default is ViT-B-32/openai which produces 512-dim
embeddings shared by both images and text.

Model loading is deferred until first use so importing this module
never blocks app startup.
"""
from __future__ import annotations

import io
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from tomslab import db as dbmod

log = logging.getLogger(__name__)

try:
    import open_clip  # type: ignore
    import torch      # type: ignore
    from PIL import Image
except ImportError:
    open_clip = None  # type: ignore
    torch = None      # type: ignore
    Image = None      # type: ignore


# ---------------------------------------------------------------------------
# Model singleton
# ---------------------------------------------------------------------------
@dataclass
class CLIPBundle:
    model_name: str
    pretrained: str
    device: str
    dim: int
    model: object
    preprocess: object
    tokenizer: object


_model: CLIPBundle | None = None
_load_lock = threading.Lock()


def _device() -> str:
    if torch is None:
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_clip(
    model_name: str = "ViT-B-32",
    pretrained: str = "openai",
) -> CLIPBundle:
    global _model
    if open_clip is None or torch is None:
        raise RuntimeError(
            "open_clip / torch not installed — Phase 4 visual features unavailable"
        )
    with _load_lock:
        if (
            _model is not None
            and _model.model_name == model_name
            and _model.pretrained == pretrained
        ):
            return _model

        log.info("Loading CLIP %s/%s on %s", model_name, pretrained, _device())
        # quick_gelu=True matches the OpenAI pretrained weights exactly
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, quick_gelu=(pretrained == "openai")
        )
        tokenizer = open_clip.get_tokenizer(model_name)
        device = _device()
        model = model.to(device).eval()

        # discover dim with a tiny probe
        with torch.no_grad():
            probe = tokenizer(["probe"]).to(device)
            feats = model.encode_text(probe)
            dim = int(feats.shape[-1])

        _model = CLIPBundle(
            model_name=model_name,
            pretrained=pretrained,
            device=device,
            dim=dim,
            model=model,
            preprocess=preprocess,
            tokenizer=tokenizer,
        )
        log.info("CLIP ready: dim=%d device=%s", dim, device)
        return _model


def clip_model_tag(conn: sqlite3.Connection) -> str:
    name = dbmod.get_setting(conn, "clip_model", "ViT-B-32") or "ViT-B-32"
    pre = dbmod.get_setting(conn, "clip_pretrained", "openai") or "openai"
    return f"{name}:{pre}"


def ensure_loaded(conn: sqlite3.Connection) -> CLIPBundle:
    name = dbmod.get_setting(conn, "clip_model", "ViT-B-32") or "ViT-B-32"
    pre = dbmod.get_setting(conn, "clip_pretrained", "openai") or "openai"
    return load_clip(name, pre)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def is_image_path(path: str) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _load_image(path: str) -> "Image.Image | None":
    try:
        img = Image.open(path)
        img.load()
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception as exc:
        log.debug("Failed to read image %s: %s", path, exc)
        return None


def embed_image_paths(
    bundle: CLIPBundle, items: list[tuple[str, str]]
) -> list[tuple[str, np.ndarray]]:
    """Embed (id, path) pairs. Unreadable images are silently skipped.

    Returns (id, normalised 1-D float32 embedding) tuples for successes only.
    """
    tensors = []
    ids: list[str] = []
    for aid, path in items:
        img = _load_image(path)
        if img is None:
            continue
        try:
            tensors.append(bundle.preprocess(img))
            ids.append(aid)
        except Exception as exc:
            log.warning("preprocess failed for %s: %s", path, exc)

    if not tensors:
        return []

    batch = torch.stack(tensors).to(bundle.device)
    with torch.no_grad():
        feats = bundle.model.encode_image(batch)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    arr = feats.detach().cpu().numpy().astype("float32")
    return [(ids[i], arr[i]) for i in range(len(ids))]


def embed_text(bundle: CLIPBundle, text: str) -> np.ndarray:
    tokens = bundle.tokenizer([text]).to(bundle.device)
    with torch.no_grad():
        feats = bundle.model.encode_text(tokens)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].detach().cpu().numpy().astype("float32")


# ---------------------------------------------------------------------------
# Visual search — cosine over cached image embedding matrix
# ---------------------------------------------------------------------------
@dataclass
class VisualHit:
    attachment_id: str
    message_id: str
    local_path: str
    filename: str
    score: float
    source_type: str = "attachment"   # 'attachment' | 'doc_page'
    doc_page_id: int | None = None
    doc_title: str = ""
    doc_page_num: int = 0


class _VisualCache:
    """Cached CLIP matrix covering both chart attachments AND PDF page images.

    Each row's metadata carries source_type so search results carry whichever
    origin they came from.
    """
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.signature: tuple | None = None
        self.matrix: np.ndarray | None = None
        self.attachment_ids: list[str] | None = None
        self.message_ids: list[str] | None = None
        self.local_paths: list[str] | None = None
        self.filenames: list[str] | None = None
        # parallel arrays for doc-page pathway
        self.source_types: list[str] | None = None
        self.doc_page_ids: list[int] | None = None
        self.doc_titles: list[str] | None = None
        self.doc_page_nums: list[int] | None = None


_cache = _VisualCache()


def invalidate_cache() -> None:
    with _cache.lock:
        _cache.signature = None
        _cache.matrix = None
        _cache.attachment_ids = None
        _cache.message_ids = None
        _cache.local_paths = None
        _cache.filenames = None
        _cache.source_types = None
        _cache.doc_page_ids = None
        _cache.doc_titles = None
        _cache.doc_page_nums = None


def _current_signature(conn: sqlite3.Connection) -> tuple:
    r_att = conn.execute(
        "SELECT COUNT(*) AS n, MAX(generated_at) AS t FROM image_embeddings"
    ).fetchone()
    r_doc = conn.execute(
        "SELECT COUNT(*) AS n, MAX(generated_at) AS t FROM doc_page_image_embeddings"
    ).fetchone()
    return (
        int(r_att["n"] or 0),
        r_att["t"] or "",
        int(r_doc["n"] or 0),
        r_doc["t"] or "",
    )


def available(conn: sqlite3.Connection) -> bool:
    sig = _current_signature(conn)
    return sig[0] > 0 or sig[2] > 0


def _load_matrix(conn: sqlite3.Connection) -> bool:
    sig = _current_signature(conn)
    if _cache.signature == sig and _cache.matrix is not None:
        return True
    if sig[0] == 0 and sig[2] == 0:
        _cache.signature = sig
        _cache.matrix = None
        return False

    att_rows = conn.execute(
        """
        SELECT ie.dim AS dim, ie.embedding AS blob,
               'attachment' AS src,
               a.id AS aid, a.message_id AS mid, a.local_path AS path, a.filename AS fn,
               NULL AS pid, NULL AS title, NULL AS pnum
          FROM image_embeddings ie
          JOIN attachments a ON a.id = ie.attachment_id
        """
    ).fetchall()

    doc_rows = conn.execute(
        """
        SELECT de.dim AS dim, de.embedding AS blob,
               'doc_page' AS src,
               NULL AS aid, NULL AS mid, p.rendered_path AS path,
               ('page_' || printf('%04d', p.page_num) || '.png') AS fn,
               p.id AS pid, d.title AS title, p.page_num AS pnum
          FROM doc_page_image_embeddings de
          JOIN document_pages p ON p.id = de.page_id
          JOIN documents d ON d.id = p.document_id
        """
    ).fetchall()

    rows = list(att_rows) + list(doc_rows)
    if not rows:
        _cache.signature = sig
        _cache.matrix = None
        return False

    dim = int(rows[0]["dim"])
    n = len(rows)
    mat = np.empty((n, dim), dtype=np.float32)
    aids = [""] * n
    mids = [""] * n
    paths = [""] * n
    fns = [""] * n
    srcs = [""] * n
    pids = [0] * n
    titles = [""] * n
    pnums = [0] * n
    for i, r in enumerate(rows):
        v = np.frombuffer(r["blob"], dtype=np.float32)
        if v.size != dim:
            v = v[:dim] if v.size > dim else np.pad(v, (0, dim - v.size))
        mat[i] = v
        srcs[i] = r["src"] or ""
        aids[i] = r["aid"] or ""
        mids[i] = r["mid"] or ""
        paths[i] = r["path"] or ""
        fns[i] = r["fn"] or ""
        pids[i] = int(r["pid"]) if r["pid"] is not None else 0
        titles[i] = r["title"] or ""
        pnums[i] = int(r["pnum"]) if r["pnum"] is not None else 0

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms

    _cache.signature = sig
    _cache.matrix = mat
    _cache.attachment_ids = aids
    _cache.message_ids = mids
    _cache.local_paths = paths
    _cache.filenames = fns
    _cache.source_types = srcs
    _cache.doc_page_ids = pids
    _cache.doc_titles = titles
    _cache.doc_page_nums = pnums
    return True


def visual_search(
    conn: sqlite3.Connection, query: str, limit: int = 120
) -> list[VisualHit]:
    query = (query or "").strip()
    if not query:
        return []
    with _cache.lock:
        if not _load_matrix(conn):
            return []
        mat = _cache.matrix
        aids = _cache.attachment_ids
        mids = _cache.message_ids
        paths = _cache.local_paths
        fns = _cache.filenames

    bundle = ensure_loaded(conn)
    qv = embed_text(bundle, query)
    if qv.size != mat.shape[1]:
        raise RuntimeError(
            f"query dim {qv.size} ≠ index dim {mat.shape[1]}; rebuild image embeddings"
        )
    # embed_text already normalises
    scores = mat @ qv
    k = min(limit, scores.size)
    top_idx = np.argpartition(-scores, k - 1)[:k]
    top_idx = top_idx[np.argsort(-scores[top_idx])]

    hits: list[VisualHit] = []
    srcs = _cache.source_types or []
    pids = _cache.doc_page_ids or []
    titles = _cache.doc_titles or []
    pnums = _cache.doc_page_nums or []
    for i in top_idx:
        idx = int(i)
        src = srcs[idx] if idx < len(srcs) else "attachment"
        hits.append(
            VisualHit(
                attachment_id=aids[idx],
                message_id=mids[idx],
                local_path=paths[idx],
                filename=fns[idx],
                score=float(scores[idx]),
                source_type=src,
                doc_page_id=pids[idx] if idx < len(pids) and pids[idx] else None,
                doc_title=titles[idx] if idx < len(titles) else "",
                doc_page_num=pnums[idx] if idx < len(pnums) else 0,
            )
        )
    return hits


def visual_search_message_ids(
    conn: sqlite3.Connection, query: str, limit: int = 120
) -> list[str]:
    """Return typed ids for the main feed's Visual mode. Emits either
    'msg:<discord_id>' for attachment hits or 'doc:<page_id>' for PDF
    page hits — matching the schema used by the mixed semantic search.
    """
    seen: set[str] = set()
    out: list[str] = []
    for h in visual_search(conn, query, limit=limit * 2):
        if h.source_type == "doc_page" and h.doc_page_id:
            tid = f"doc:{h.doc_page_id}"
        elif h.message_id:
            tid = f"msg:{h.message_id}"
        else:
            continue
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
        if len(out) >= limit:
            break
    return out


_EXT_WHERE = "(" + " OR ".join(
    f"lower(a.filename) LIKE '%{ext}'" for ext in sorted(IMAGE_EXTENSIONS)
) + ")"


def pending_count(conn: sqlite3.Connection, model_tag: str) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM attachments a
         WHERE a.local_path IS NOT NULL AND a.local_path != ''
           AND {_EXT_WHERE}
           AND NOT EXISTS (
                SELECT 1 FROM image_embeddings ie
                 WHERE ie.attachment_id = a.id AND ie.model = ?
           )
        """,
        (model_tag,),
    ).fetchone()
    return int(row["n"] or 0)


def pending_doc_page_count(conn: sqlite3.Connection, model_tag: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM document_pages p
         WHERE p.rendered_path IS NOT NULL AND p.rendered_path != ''
           AND NOT EXISTS (
                SELECT 1 FROM doc_page_image_embeddings de
                 WHERE de.page_id = p.id AND de.model = ?
           )
        """,
        (model_tag,),
    ).fetchone()
    return int(row["n"] or 0)


def pending_items_sql() -> str:
    """Return the SELECT body that the pipeline uses — shared so filters stay in sync."""
    return f"""
        SELECT a.id AS id, a.local_path AS path
          FROM attachments a
         WHERE a.local_path IS NOT NULL AND a.local_path != ''
           AND {_EXT_WHERE}
           AND NOT EXISTS (
                SELECT 1 FROM image_embeddings ie
                 WHERE ie.attachment_id = a.id AND ie.model = ?
           )
         ORDER BY a.id
         LIMIT ?
        """
