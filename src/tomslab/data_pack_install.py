"""Install a Tom's Lab data pack (.tar.zst) produced by
``packaging/build_data_pack.py``.

Pack layout:

    data/
      tomslab.db
      charts/<shard>/<id>.webp
      doc_images/<doc_id>/page_<n>.webp

Path columns in the packed DB use the sentinel ``{DATA}`` (see
``SENTINEL`` below) in place of the user's real data dir. On install we
rewrite those to absolute paths under the local ``data_dir()``.

The install is "backup-first, then extract":

  1. Rename the current ``data/`` to ``data.backup-<timestamp>/``.
  2. Extract the archive into a fresh ``data/``.
  3. Rewrite path sentinels in the new DB.

If any step fails we undo the rename so the user's old data is intact.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

import zstandard as zstd

from tomslab import __version__ as APP_VERSION
from tomslab.paths import app_root, data_dir

log = logging.getLogger(__name__)


SENTINEL = "{DATA}"
MANIFEST_ARCNAME = "data/manifest.json"


class IncompatibleAppVersion(Exception):
    """Raised when a pack requires a newer app than the one running."""


class PackHashMismatch(Exception):
    """Raised when the archive's SHA-256 doesn't match the manifest."""


def _parse_version(v: str) -> tuple[int, ...]:
    """Turn '1.2.3' into (1, 2, 3). Missing / non-numeric segments count
    as 0 so we don't blow up on pre-release tags like '1.2.0-rc1'.
    """
    out: list[int] = []
    for part in (v or "").strip().split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class PackManifest:
    release_date: str
    app_version_min: str
    total_raw_bytes: int
    compressed_bytes: int
    sha256: str
    counts: dict[str, int]

    @classmethod
    def from_dict(cls, d: dict) -> "PackManifest":
        return cls(
            release_date=str(d.get("release_date", "")),
            app_version_min=str(d.get("app_version_min", "")),
            total_raw_bytes=int(d.get("total_raw_bytes", 0)),
            compressed_bytes=int(d.get("compressed_bytes", 0)),
            sha256=str(d.get("sha256", "")),
            counts=dict(d.get("counts") or {}),
        )


def _open_zstd_tar(pack_path: Path) -> tuple[tarfile.TarFile, object, object]:
    """Open ``pack_path`` for streaming tar read. Returns ``(tar, zreader, fh)``
    — caller must close all three."""
    fh = pack_path.open("rb")
    dctx = zstd.ZstdDecompressor()
    zreader = dctx.stream_reader(fh)
    tar = tarfile.open(fileobj=zreader, mode="r|")
    return tar, zreader, fh


def read_manifest(pack_path: Path) -> PackManifest | None:
    """Peek the manifest out of a .tar.zst without fully extracting.

    Looks for a ``data/manifest.json`` inside the archive; if the PM's
    build skipped embedding one, falls back to a sibling
    ``<pack>.manifest.json`` next to the archive on disk.
    """
    # 1. sibling file (produced by build_data_pack.py)
    sibling = pack_path.with_suffix(".manifest.json")
    if sibling.exists():
        try:
            return PackManifest.from_dict(json.loads(sibling.read_text("utf-8")))
        except Exception:
            pass

    # 2. embedded manifest — stream through the archive looking for the entry
    try:
        tar, zreader, fh = _open_zstd_tar(pack_path)
    except Exception:
        return None
    try:
        for member in tar:
            if member.name == MANIFEST_ARCNAME and member.isfile():
                f = tar.extractfile(member)
                if f is None:
                    continue
                return PackManifest.from_dict(json.loads(f.read().decode("utf-8")))
    finally:
        try:
            tar.close()
        finally:
            try:
                zreader.close()
            finally:
                fh.close()
    return None


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------


def _safe_resolve(dest_root: Path, member_name: str) -> Path | None:
    """Defence against tar traversal — refuse members that escape ``data/``."""
    # Archive puts everything under "data/". Strip that leading segment so
    # we extract into dest_root directly.
    parts = member_name.replace("\\", "/").split("/")
    if not parts or parts[0] != "data":
        return None
    rel = "/".join(parts[1:])
    if not rel:
        return None
    candidate = (dest_root / rel).resolve()
    try:
        candidate.relative_to(dest_root.resolve())
    except ValueError:
        return None
    return candidate


def _extract_pack(pack_path: Path, dest_data: Path,
                  progress: Callable[[int, int, str], None] | None = None) -> int:
    """Stream-extract the archive into ``dest_data`` (which should be the
    empty, already-created new data dir). Returns number of files written."""
    tar, zreader, fh = _open_zstd_tar(pack_path)
    n = 0
    try:
        for member in tar:
            if not member.isfile():
                continue
            target = _safe_resolve(dest_data, member.name)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1 << 20)
            n += 1
            # Fire often enough that a UI consumer can paint smooth
            # progress and that signals queued onto the main thread
            # (when the caller runs us on a QThread) don't bunch up.
            # Every 50 files keeps the cost negligible while making the
            # status bar feel live throughout a 10 GB extract.
            if progress is not None and (n % 50 == 0):
                progress(n, 0, f"Extracting… ({n:,} files)")
    finally:
        try:
            tar.close()
        finally:
            try:
                zreader.close()
            finally:
                fh.close()
    return n


# ---------------------------------------------------------------------------
# Path sentinel rewrite
# ---------------------------------------------------------------------------


def _rewrite_sentinel(db_path: Path, real_data_dir: Path) -> None:
    """Rewrite ``{DATA}/…`` paths in ``attachments.local_path`` and
    ``document_pages.rendered_path`` to absolute paths under ``real_data_dir``.

    Uses forward slashes — the app reads these through ``pathlib.Path``
    which handles both on Windows. Forward slashes avoid Windows-specific
    escape headaches when shipping a pack built on a different OS.
    """
    prefix = str(real_data_dir).replace("\\", "/").rstrip("/") + "/"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE attachments SET local_path = REPLACE(local_path, ?, ?) "
            "WHERE local_path LIKE ?",
            (SENTINEL + "/", prefix, SENTINEL + "/%"),
        )
        conn.execute(
            "UPDATE document_pages SET rendered_path = REPLACE(rendered_path, ?, ?) "
            "WHERE rendered_path LIKE ?",
            (SENTINEL + "/", prefix, SENTINEL + "/%"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Personal-data preservation
# ---------------------------------------------------------------------------


def _merge_personal_data(backup_db: Path, new_db: Path) -> dict[str, int]:
    """Copy user-personal rows from the pre-install DB into the newly
    extracted DB so a data-pack update doesn't wipe bookmarks + favorites.

    Preserved:
      * ``favorite_authors`` — user-curated high-signal traders.
      * ``bookmarks`` — saved messages / Ask Tom answers. Only rows whose
        ``message_id`` still exists in the new corpus; otherwise the
        message link would dangle.

    Intentionally NOT preserved: ``chat_history`` is a rolling
    transient log and isn't worth the extra merge complexity.

    Returns a ``{'favorite_authors': n, 'bookmarks': n}`` count map.
    Missing source tables (schema mismatch, fresh install with no prior
    data) fall through silently and contribute zero.

    Implementation note: the previous version used ``ATTACH DATABASE``
    against the new DB. That triggered a hard process crash on Windows
    in some configurations (no Python traceback, app vanished). The
    new flow opens the two DBs as two separate connections — read all
    rows out of the backup, close it, then write them into the new DB.
    Slower but completely robust.
    """
    counts = {"bookmarks": 0, "favorite_authors": 0}
    if not backup_db.exists():
        log.info("merge: backup DB %s does not exist — skipping", backup_db)
        return counts

    # ---- read favorites + bookmarks out of the backup ----------------
    fav_rows: list[tuple] = []
    bm_rows: list[tuple] = []
    try:
        log.info("merge: opening backup DB %s read-only", backup_db)
        src = sqlite3.connect(f"file:{backup_db.as_posix()}?mode=ro", uri=True)
        try:
            try:
                fav_rows = src.execute(
                    "SELECT author_name, author_nickname, added_at "
                    "  FROM favorite_authors"
                ).fetchall()
            except sqlite3.OperationalError:
                # Pre-favorites schema in the backup — table missing.
                fav_rows = []
            try:
                bm_rows = src.execute(
                    "SELECT message_id, note, tags, created_at "
                    "  FROM bookmarks"
                ).fetchall()
            except sqlite3.OperationalError:
                bm_rows = []
        finally:
            src.close()
    except Exception as exc:
        log.warning("merge: could not read backup DB (%s) — skipping merge",
                    type(exc).__name__)
        return counts

    log.info("merge: read %d favorites + %d bookmarks from backup",
             len(fav_rows), len(bm_rows))

    if not fav_rows and not bm_rows:
        # Empty source — nothing to merge. Skip the second connection
        # entirely to keep the path as short as possible.
        return counts

    # ---- write into the new DB ---------------------------------------
    try:
        dst = sqlite3.connect(str(new_db))
        try:
            if fav_rows:
                try:
                    dst.executemany(
                        "INSERT OR IGNORE INTO favorite_authors "
                        "  (author_name, author_nickname, added_at) "
                        "VALUES (?, ?, ?)",
                        fav_rows,
                    )
                    counts["favorite_authors"] = len(fav_rows)
                except sqlite3.OperationalError as exc:
                    log.warning("merge: favorite_authors insert failed: %s", exc)

            if bm_rows:
                # Filter to bookmarks whose message_id still exists in
                # the new corpus, so we never leave dangling refs.
                kept: list[tuple] = []
                for mid, note, tags, created_at in bm_rows:
                    row = dst.execute(
                        "SELECT 1 FROM messages WHERE id = ? LIMIT 1",
                        (mid,),
                    ).fetchone()
                    if row is not None:
                        kept.append((mid, note, tags, created_at))
                if kept:
                    try:
                        dst.executemany(
                            "INSERT INTO bookmarks "
                            "  (message_id, note, tags, created_at) "
                            "VALUES (?, ?, ?, ?)",
                            kept,
                        )
                        counts["bookmarks"] = len(kept)
                    except sqlite3.OperationalError as exc:
                        log.warning("merge: bookmarks insert failed: %s", exc)

            dst.commit()
        finally:
            dst.close()
    except Exception as exc:
        log.warning("merge: could not write to new DB (%s)",
                    type(exc).__name__)
        # counts stays at whatever we'd populated; install still succeeds.

    log.info("merge: preserved %d favorites + %d bookmarks",
             counts["favorite_authors"], counts["bookmarks"])
    return counts


# ---------------------------------------------------------------------------
# Top-level install
# ---------------------------------------------------------------------------


@dataclass
class InstallResult:
    backup_dir: Path | None
    extracted_files: int
    manifest: PackManifest | None
    merged: dict[str, int] | None = None


def install_pack(
    pack_path: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> InstallResult:
    """Install ``pack_path`` as the active Tom's Lab data directory.

    Caller is responsible for having stopped all workers and closed the
    app-side SQLite connection — this function operates purely on the
    filesystem.

    Atomic-ish:
      * Current ``data/`` is renamed to ``data.backup-<timestamp>/`` *before*
        extraction starts, so even a half-written extract never destroys the
        user's existing data.
      * On failure, the new (partial) ``data/`` is removed and the backup
        is renamed back to ``data/``.
    """
    current_data = data_dir()
    root = app_root()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / f"data.backup-{ts}"

    manifest = read_manifest(pack_path)

    # Pre-flight: version compatibility. A pack built for a newer app
    # may have DB schema the running app doesn't understand, so refuse
    # with a clear message instead of corrupting state on extract.
    if manifest and manifest.app_version_min:
        if _parse_version(APP_VERSION) < _parse_version(manifest.app_version_min):
            raise IncompatibleAppVersion(
                f"This data pack requires Tom's Lab "
                f"v{manifest.app_version_min} or newer — you're running "
                f"v{APP_VERSION}. Update the app before installing this pack."
            )

    # Pre-flight: SHA-256 verification. Drive / network downloads
    # corrupt silently sometimes; verifying now catches it before we
    # destructively rename data/ and start extracting garbage.
    if manifest and manifest.sha256:
        if progress is not None:
            progress(0, 0, "Verifying pack integrity (SHA-256)…")
        actual = _sha256_file(pack_path)
        if actual.lower() != manifest.sha256.lower():
            raise PackHashMismatch(
                f"SHA-256 mismatch — the download is corrupted or has been "
                f"tampered with.\n\nExpected: {manifest.sha256}\nActual:   {actual}\n\n"
                f"Re-download the pack and retry. Nothing has been changed."
            )

    # 1. Backup (rename is atomic on Windows/macOS/Linux within a volume).
    if current_data.exists():
        if progress is not None:
            progress(0, 0, f"Backing up current data → {backup_dir.name}")
        current_data.rename(backup_dir)
    else:
        backup_dir = None

    # 2. Fresh empty data dir.
    current_data.mkdir(parents=True, exist_ok=True)

    try:
        log.info("install_pack: starting extract from %s", pack_path)
        if progress is not None:
            progress(0, 0, "Extracting data pack…")
        n = _extract_pack(pack_path, current_data, progress=progress)
        log.info("install_pack: extracted %d files into %s", n, current_data)

        db_path = current_data / "tomslab.db"
        if not db_path.exists():
            raise RuntimeError(
                "Pack does not contain data/tomslab.db — wrong archive?"
            )

        log.info("install_pack: rewriting path sentinels in %s", db_path)
        if progress is not None:
            progress(0, 0, "Rewriting paths…")
        _rewrite_sentinel(db_path, current_data)
        log.info("install_pack: sentinel rewrite done")

        # Preserve the user's bookmarks + favorites across pack updates.
        # On a truly fresh install backup_dir is None and this is a no-op.
        merged: dict[str, int] | None = None
        if backup_dir is not None:
            log.info("install_pack: starting merge from %s", backup_dir)
            if progress is not None:
                progress(0, 0, "Preserving bookmarks + favorites…")
            try:
                merged = _merge_personal_data(backup_dir / "tomslab.db", db_path)
                log.info("install_pack: merge done %s", merged)
            except Exception:
                # Preservation is best-effort. A merge failure must not
                # sink the whole install — the freshly-extracted corpus
                # is still valid without the user's private rows.
                log.exception("install_pack: merge raised; continuing without it")
                merged = None

        log.info("install_pack: success (n=%d, merged=%s)", n, merged)
        return InstallResult(
            backup_dir=backup_dir,
            extracted_files=n,
            manifest=manifest,
            merged=merged,
        )
    except Exception:
        # Roll back: wipe the partial new data/ and put the backup back.
        try:
            shutil.rmtree(current_data, ignore_errors=True)
        except Exception:
            pass
        if backup_dir is not None and backup_dir.exists():
            try:
                backup_dir.rename(current_data)
            except Exception:
                # Last-ditch: at least leave the backup_dir where the
                # user can rename it by hand.
                pass
        raise
