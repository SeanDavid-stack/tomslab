"""Build a shippable Tom's Lab data pack (.tar.zst) from a populated data dir.

The PM runs ingest + classify + embed + purge on their machine, ending up
with roughly 8 GB of SQLite + Discord chart exports + PDF page renders.
This script turns that into a ~2–3 GB archive a user can download and
install via ``File → Install data pack…``.

What the pack contains at runtime-layout:

    data/
      tomslab.db            # vacuumed, private tables cleared
      charts/<shard>/<id>.webp
      doc_images/<doc_id>/page_<n>.webp

Paths in ``attachments.local_path`` and ``document_pages.rendered_path``
are rewritten to the form ``{DATA}/charts/…`` — the install flow in the
app replaces the ``{DATA}`` sentinel with the user's real ``data_dir()``
once the archive is extracted. This avoids baking in the PM's absolute
paths.

Usage:

    python packaging/build_data_pack.py \
        --data-dir "D:/Toms Lab/data" \
        --out-dir  "D:/Toms Lab/dist" \
        --app-version 1.2.0

``--data-dir`` defaults to ``$TOMSLAB_DATA_DIR`` or ``./data`` below the
repo root. Re-running on the same data dir is safe: already-WebP images
are copied as-is, the DB is always worked on via a fresh copy.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import zstandard as zstd
except ImportError as exc:
    sys.stderr.write(
        "This script needs the 'zstandard' package (pip install zstandard).\n"
    )
    raise

try:
    from PIL import Image
except ImportError:
    sys.stderr.write(
        "This script needs Pillow (pip install Pillow). It's already in the "
        "app's requirements.txt, so running from the same venv should work.\n"
    )
    raise


LOG = logging.getLogger("build_data_pack")

SENTINEL = "{DATA}"
WEBP_QUALITY = 85
WEBP_METHOD = 6
ZSTD_LEVEL = 19
REENCODE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
PASSTHROUGH_EXTS = {".webp", ".gif"}

# chart_decision values that mean "ship this chart" (auto_keep, keep, NULL).
# Anything else (discard, auto_discard, pending…) is dropped from the pack.
KEEP_DECISIONS = ("keep", "auto_keep")


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------


def _human(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:,.1f} {u}"
        x /= 1024
    return f"{n} B"


@dataclass
class _SizeTally:
    src_bytes: int = 0
    dst_bytes: int = 0
    n_converted: int = 0
    n_copied: int = 0
    n_skipped_missing: int = 0

    def ratio(self) -> float:
        if self.src_bytes <= 0:
            return 1.0
        return self.dst_bytes / self.src_bytes


@dataclass
class BuildStats:
    charts: _SizeTally = field(default_factory=_SizeTally)
    doc_pages: _SizeTally = field(default_factory=_SizeTally)
    db_before_bytes: int = 0
    db_after_bytes: int = 0
    counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DB prep
# ---------------------------------------------------------------------------


def _prep_db(src_db: Path, dst_db: Path) -> None:
    """Copy tomslab.db to dst, clear PM-private rows, VACUUM before and after."""
    LOG.info("Copying DB %s -> %s", src_db, dst_db)
    shutil.copy2(src_db, dst_db)
    # Best-effort: clear any WAL/SHM sidecars that came along with shutil — we
    # want the packed DB to be a clean, single-file snapshot.
    for suffix in ("-wal", "-shm"):
        sidecar = dst_db.with_name(dst_db.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    conn = sqlite3.connect(str(dst_db))
    try:
        conn.execute("PRAGMA journal_mode = DELETE;")
        conn.commit()
        LOG.info("Pre-scrub VACUUM")
        conn.execute("VACUUM;")

        # Private / non-useful-to-end-user rows.
        for tbl in ("chat_history", "bookmarks", "imports"):
            try:
                n = conn.execute(f"DELETE FROM {tbl}").rowcount
                LOG.info("Cleared %s rows from %s", n, tbl)
            except sqlite3.OperationalError as exc:
                # Table doesn't exist on very-old DBs — harmless.
                LOG.warning("Skip %s: %s", tbl, exc)

        conn.commit()
        LOG.info("Post-scrub VACUUM")
        conn.execute("VACUUM;")
        conn.commit()
    finally:
        conn.close()


def _table_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    out: dict[str, int] = {}
    try:
        for name in (
            "messages", "attachments", "documents", "document_pages",
            "videos", "video_chunks", "window_embeddings",
            "image_embeddings", "document_page_embeddings",
            "video_chunk_embeddings", "doc_page_image_embeddings",
        ):
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
                out[name] = int(row[0] or 0)
            except sqlite3.OperationalError:
                out[name] = 0
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Image re-encode
# ---------------------------------------------------------------------------


def _shard(key: str) -> str:
    """2-char shard from a stable key — keeps any one dir under ~100K files."""
    h = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()
    return h[:2]


def _encode_worker(src_str: str, dst_str: str) -> tuple[int, int, bool, str | None]:
    """ProcessPoolExecutor entry point. Converts exceptions to an error
    string so the parent can log-and-continue without losing the pool.

    Returns ``(src_bytes, dst_bytes, converted, error_or_None)``.
    Module-level so Windows ``spawn`` can pickle it.
    """
    try:
        s, d, conv = _reencode_one(Path(src_str), Path(dst_str))
        return (s, d, conv, None)
    except Exception as exc:
        return (0, 0, False, f"{type(exc).__name__}: {exc}")


def _reencode_one(src: Path, dst: Path) -> tuple[int, int, bool]:
    """Re-encode src → dst (WebP q85) or passthrough-copy if already WebP/GIF.

    Returns ``(src_bytes, dst_bytes, converted)``. ``converted=False`` means
    a straight copy was performed (or the output already existed and
    matched the source size — idempotent re-run).
    """
    src_bytes = src.stat().st_size
    ext = src.suffix.lower()

    dst.parent.mkdir(parents=True, exist_ok=True)

    if ext in PASSTHROUGH_EXTS:
        if not dst.exists() or dst.stat().st_size != src_bytes:
            shutil.copy2(src, dst)
        return src_bytes, dst.stat().st_size, False

    if ext in REENCODE_EXTS:
        # Idempotent: if dst already exists from a prior run, trust it.
        if dst.exists() and dst.stat().st_size > 0:
            return src_bytes, dst.stat().st_size, False
        with Image.open(src) as im:
            # WebP needs RGB/RGBA. Some PNGs come in as P (palette) or
            # CMYK — convert to RGB to avoid "cannot write mode X as WebP".
            if im.mode not in ("RGB", "RGBA"):
                if "A" in im.mode or im.mode == "P":
                    im = im.convert("RGBA")
                else:
                    im = im.convert("RGB")
            im.save(dst, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
        return src_bytes, dst.stat().st_size, True

    # Unknown extension — copy as-is, log a warning once.
    shutil.copy2(src, dst)
    return src_bytes, dst.stat().st_size, False


def _target_path_charts(staging_data: Path, aid: str, filename: str, src_ext: str) -> tuple[Path, str]:
    """Return (absolute staging path, sentinel-relative path) for a chart."""
    ext = ".webp" if src_ext.lower() in REENCODE_EXTS else src_ext.lower()
    safe_aid = aid or "unknown"
    shard = _shard(safe_aid)
    name = f"{safe_aid}{ext}"
    rel = f"charts/{shard}/{name}"
    return staging_data / rel, f"{SENTINEL}/{rel}"


def _target_path_doc_page(staging_data: Path, doc_id: int, page_num: int, src_ext: str) -> tuple[Path, str]:
    ext = ".webp" if src_ext.lower() in REENCODE_EXTS else src_ext.lower()
    rel = f"doc_images/{int(doc_id)}/page_{int(page_num):04d}{ext}"
    return staging_data / rel, f"{SENTINEL}/{rel}"


def _default_worker_count() -> int:
    """All physical cores minus one — leaves a core for the main process
    (which is already busy doing DB writes and driving the pool) and the
    OS. Capped at 16 because beyond that Pillow pool-saturation and DB
    write contention dominate."""
    n = os.cpu_count() or 4
    return max(1, min(16, n - 1))


def _reencode_attachments(
    conn: sqlite3.Connection,
    staging_data: Path,
    stats: _SizeTally,
    workers: int | None = None,
) -> None:
    """Walk keeper attachments, copy/reencode into staging/, rewrite local_path.

    The re-encode step is pure CPU (Pillow WebP at method=6). Running it
    in a process pool is 6-8x on a modern desktop — single-process mode
    only does ~2 files/sec, pool mode matches the CPU count. DB writes
    stay on the main process because sqlite3 handles don't cross
    processes safely.
    """
    rows = conn.execute(
        """
        SELECT id, local_path, filename, chart_decision
          FROM attachments
         WHERE local_path IS NOT NULL AND local_path != ''
        """
    ).fetchall()

    # Phase 1 (main process): pre-filter. Drop discards + missing files
    # up front so the worker pool only receives real work.
    tasks: list[tuple[str, str, str, str]] = []  # (aid, src, dst, sentinel_rel)
    for aid, local_path, filename, decision in rows:
        decision_norm = (decision or "").strip().lower()
        if decision_norm and decision_norm not in KEEP_DECISIONS:
            conn.execute(
                "UPDATE attachments SET local_path = NULL WHERE id = ?", (aid,)
            )
            continue
        src = Path(local_path)
        if not src.is_file():
            stats.n_skipped_missing += 1
            conn.execute(
                "UPDATE attachments SET local_path = NULL WHERE id = ?", (aid,)
            )
            continue
        dst, sentinel_rel = _target_path_charts(
            staging_data, aid, filename or src.name, src.suffix
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        tasks.append((aid, str(src), str(dst), sentinel_rel))
    conn.commit()

    if not tasks:
        return

    n_workers = workers or _default_worker_count()
    LOG.info(
        "Re-encoding %d attachments with %d workers", len(tasks), n_workers
    )
    t0 = time.time()
    last_log = t0
    done = 0

    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {
            ex.submit(_encode_worker, src, dst): (aid, sentinel_rel)
            for (aid, src, dst, sentinel_rel) in tasks
        }
        for fut in as_completed(futures):
            aid, sentinel_rel = futures[fut]
            s, d, converted, err = fut.result()
            done += 1

            if err:
                LOG.warning("Skipping attachment %s: %s", aid, err)
                conn.execute(
                    "UPDATE attachments SET local_path = NULL WHERE id = ?",
                    (aid,),
                )
                continue

            stats.src_bytes += s
            stats.dst_bytes += d
            if converted:
                stats.n_converted += 1
            else:
                stats.n_copied += 1

            conn.execute(
                "UPDATE attachments SET local_path = ? WHERE id = ?",
                (sentinel_rel, aid),
            )

            if done % 500 == 0:
                conn.commit()

            now = time.time()
            if now - last_log > 3.0:
                LOG.info(
                    "  attachments: %d/%d (%.1f/s, saved %s so far)",
                    done, len(tasks),
                    done / max(now - t0, 1e-6),
                    _human(max(stats.src_bytes - stats.dst_bytes, 0)),
                )
                last_log = now

    conn.commit()


def _reencode_doc_pages(
    conn: sqlite3.Connection,
    staging_data: Path,
    stats: _SizeTally,
    workers: int | None = None,
) -> None:
    rows = conn.execute(
        """
        SELECT id, document_id, page_num, rendered_path
          FROM document_pages
         WHERE rendered_path IS NOT NULL AND rendered_path != ''
        """
    ).fetchall()

    tasks: list[tuple[int, str, str, str]] = []
    for row in rows:
        page_id, doc_id, page_num, rendered_path = row
        src = Path(rendered_path)
        if not src.is_file():
            stats.n_skipped_missing += 1
            conn.execute(
                "UPDATE document_pages SET rendered_path = NULL WHERE id = ?",
                (page_id,),
            )
            continue
        dst, sentinel_rel = _target_path_doc_page(
            staging_data, doc_id, page_num, src.suffix
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        tasks.append((page_id, str(src), str(dst), sentinel_rel))
    conn.commit()

    if not tasks:
        return

    n_workers = workers or _default_worker_count()
    LOG.info(
        "Re-encoding %d doc pages with %d workers", len(tasks), n_workers
    )

    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {
            ex.submit(_encode_worker, src, dst): (page_id, sentinel_rel)
            for (page_id, src, dst, sentinel_rel) in tasks
        }
        for fut in as_completed(futures):
            page_id, sentinel_rel = futures[fut]
            s, d, converted, err = fut.result()

            if err:
                LOG.warning("Skipping doc page %s: %s", page_id, err)
                conn.execute(
                    "UPDATE document_pages SET rendered_path = NULL WHERE id = ?",
                    (page_id,),
                )
                continue

            stats.src_bytes += s
            stats.dst_bytes += d
            if converted:
                stats.n_converted += 1
            else:
                stats.n_copied += 1

            conn.execute(
                "UPDATE document_pages SET rendered_path = ? WHERE id = ?",
                (sentinel_rel, page_id),
            )

    conn.commit()


# ---------------------------------------------------------------------------
# Tarball
# ---------------------------------------------------------------------------


def _iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _write_tar_zst(staging_data: Path, out_path: Path, level: int = ZSTD_LEVEL) -> int:
    """Stream-compress staging_data/ into out_path (.tar.zst). Returns raw bytes."""
    cctx = zstd.ZstdCompressor(level=level, threads=-1)
    raw_total = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as outf:
        with cctx.stream_writer(outf) as zstream:
            with tarfile.open(mode="w|", fileobj=zstream) as tf:
                # Top-level "data/" so extraction into the user's app_root
                # naturally produces app_root/data/.
                for p in _iter_files(staging_data):
                    arcname = "data/" + str(p.relative_to(staging_data)).replace("\\", "/")
                    tf.add(p, arcname=arcname, recursive=False)
                    raw_total += p.stat().st_size
    return raw_total


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _build_manifest(
    *,
    app_version: str,
    release_date: str,
    pack_path: Path,
    raw_bytes: int,
    counts: dict[str, int],
) -> dict:
    compressed = pack_path.stat().st_size
    return {
        "format": "tomslab-data-pack/1",
        "app_version_min": app_version,
        "release_date": release_date,
        "archive_filename": pack_path.name,
        "total_raw_bytes": raw_bytes,
        "compressed_bytes": compressed,
        "compression_ratio": round(raw_bytes / compressed, 2) if compressed else 0,
        "sha256": _sha256(pack_path),
        "sentinel": SENTINEL,
        "counts": counts,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build(
    data_dir: Path,
    out_dir: Path,
    app_version: str,
    release_date: str | None = None,
    workers: int | None = None,
) -> dict:
    data_dir = data_dir.resolve()
    out_dir = out_dir.resolve()
    src_db = data_dir / "tomslab.db"
    if not src_db.exists():
        raise FileNotFoundError(f"No tomslab.db at {src_db}")

    release_date = release_date or date.today().isoformat()
    pack_name = f"tomslab-data-{release_date}.tar.zst"
    out_pack = out_dir / pack_name

    stats = BuildStats(db_before_bytes=src_db.stat().st_size)

    with tempfile.TemporaryDirectory(prefix="tomslab-pack-") as tmp_str:
        tmp = Path(tmp_str)
        staging_data = tmp / "data"
        staging_data.mkdir(parents=True, exist_ok=True)

        dst_db = staging_data / "tomslab.db"
        _prep_db(src_db, dst_db)

        # Re-encode pass. Mutates dst_db's local_path / rendered_path columns.
        conn = sqlite3.connect(str(dst_db))
        try:
            _reencode_attachments(conn, staging_data, stats.charts, workers=workers)
            _reencode_doc_pages(conn, staging_data, stats.doc_pages, workers=workers)

            # Final VACUUM so the path-rewrites don't leave loose pages.
            conn.commit()
            conn.execute("VACUUM;")
            conn.commit()
        finally:
            conn.close()

        stats.db_after_bytes = dst_db.stat().st_size
        stats.counts = _table_counts(dst_db)

        LOG.info("Writing %s (zstd level %d)…", out_pack, ZSTD_LEVEL)
        raw = _write_tar_zst(staging_data, out_pack, level=ZSTD_LEVEL)

    manifest = _build_manifest(
        app_version=app_version,
        release_date=release_date,
        pack_path=out_pack,
        raw_bytes=raw,
        counts=stats.counts,
    )
    manifest_path = out_pack.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # ---- Report ----
    print()
    print("=" * 60)
    print(f"Data pack built: {out_pack}")
    print("=" * 60)
    print(f"DB            : {_human(stats.db_before_bytes)} -> {_human(stats.db_after_bytes)}")
    print(
        f"Charts        : {_human(stats.charts.src_bytes)} -> {_human(stats.charts.dst_bytes)}"
        f"  ({stats.charts.n_converted} re-encoded, {stats.charts.n_copied} copied,"
        f" {stats.charts.n_skipped_missing} missing)"
    )
    print(
        f"Doc pages     : {_human(stats.doc_pages.src_bytes)} -> {_human(stats.doc_pages.dst_bytes)}"
        f"  ({stats.doc_pages.n_converted} re-encoded, {stats.doc_pages.n_copied} copied,"
        f" {stats.doc_pages.n_skipped_missing} missing)"
    )
    raw_total = (
        stats.db_after_bytes + stats.charts.dst_bytes + stats.doc_pages.dst_bytes
    )
    print(f"Raw pack (uncompressed): {_human(raw_total)}")
    print(f"Final .tar.zst         : {_human(out_pack.stat().st_size)}")
    print(f"Ratio                  : {manifest['compression_ratio']}x")
    print(f"SHA-256                : {manifest['sha256']}")
    print()
    print("Manifest (paste into the GitHub release notes):")
    print(json.dumps(manifest, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a shippable Tom's Lab data pack.")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("TOMSLAB_DATA_DIR") or "./data",
        help="Source data directory (populated by the PM's machine). "
             "Defaults to $TOMSLAB_DATA_DIR or ./data.",
    )
    parser.add_argument(
        "--out-dir",
        default="./dist",
        help="Where to write <tomslab-data-YYYY-MM-DD>.tar.zst + manifest.json.",
    )
    parser.add_argument(
        "--app-version",
        required=True,
        help="Minimum app version this pack is compatible with (e.g. 1.2.0).",
    )
    parser.add_argument(
        "--release-date",
        default=None,
        help="Override release date (YYYY-MM-DD). Default: today (UTC).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel Pillow encoder processes. "
             "Default: min(16, os.cpu_count() - 1).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        build(
            data_dir=Path(args.data_dir),
            out_dir=Path(args.out_dir),
            app_version=args.app_version,
            release_date=args.release_date,
            workers=args.workers,
        )
    except Exception as exc:
        LOG.error("Build failed: %s", exc, exc_info=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
