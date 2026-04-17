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


class _VisualCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.signature: tuple | None = None
        self.matrix: np.ndarray | None = None
        self.attachment_ids: list[str] | None = None
        self.message_ids: list[str] | None = None
        self.local_paths: list[str] | None = None
        self.filenames: list[str] | None = None


_cache = _VisualCache()


def invalidate_cache() -> None:
    with _cache.lock:
        _cache.signature = None
        _cache.matrix = None
        _cache.attachment_ids = None
        _cache.message_ids = None
        _cache.local_paths = None
        _cache.filenames = None


def _current_signature(conn: sqlite3.Connection) -> tuple:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(generated_at) AS t FROM image_embeddings"
    ).fetchone()
    return (int(row["n"] or 0), row["t"] or "")


def available(conn: sqlite3.Connection) -> bool:
    return _current_signature(conn)[0] > 0


def _load_matrix(conn: sqlite3.Connection) -> bool:
    sig = _current_signature(conn)
    if _cache.signature == sig and _cache.matrix is not None:
        return True
    if sig[0] == 0:
        _cache.signature = sig
        _cache.matrix = None
        return False

    rows = conn.execute(
        """
        SELECT ie.attachment_id AS aid, ie.dim AS dim, ie.embedding AS blob,
               a.message_id AS mid, a.local_path AS path, a.filename AS fn
          FROM image_embeddings ie
          JOIN attachments a ON a.id = ie.attachment_id
        """
    ).fetchall()
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
    for i, r in enumerate(rows):
        v = np.frombuffer(r["blob"], dtype=np.float32)
        if v.size != dim:
            v = v[:dim] if v.size > dim else np.pad(v, (0, dim - v.size))
        mat[i] = v
        aids[i] = r["aid"] or ""
        mids[i] = r["mid"] or ""
        paths[i] = r["path"] or ""
        fns[i] = r["fn"] or ""

    # embeddings are already L2-normalised at insert time, but re-normalise defensively
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms

    _cache.signature = sig
    _cache.matrix = mat
    _cache.attachment_ids = aids
    _cache.message_ids = mids
    _cache.local_paths = paths
    _cache.filenames = fns
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

    return [
        VisualHit(
            attachment_id=aids[int(i)],
            message_id=mids[int(i)],
            local_path=paths[int(i)],
            filename=fns[int(i)],
            score=float(scores[int(i)]),
        )
        for i in top_idx
    ]


def visual_search_message_ids(
    conn: sqlite3.Connection, query: str, limit: int = 120
) -> list[str]:
    """Dedup-by-message version of visual_search, for the main feed's Visual mode."""
    seen: set[str] = set()
    out: list[str] = []
    for h in visual_search(conn, query, limit=limit * 2):
        if not h.message_id or h.message_id in seen:
            continue
        seen.add(h.message_id)
        out.append(h.message_id)
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
