"""Move discarded chart attachments into a `_discarded/` sibling folder.

This is the only step that touches the filesystem. The classifier only
tags rows in SQLite; the review UI only updates tags; purge is what
actually frees disk space.

Design:
  - We *move* files rather than delete them, so the user can recover
    by dragging things back out of ``_discarded/`` in File Explorer.
  - ``attachments.local_path`` is updated to the new location so Tom's
    Lab still knows where the file is (rather than going stale).
  - We group discarded files under ``_discarded/`` next to the export
    root (inferred from the common parent of the original ``local_path``
    values), preserving the relative path so it's easy to find them.
  - Errors are collected and returned rather than aborting the whole
    purge on the first unreadable file.

To actually free disk, the user manually empties ``_discarded/``
from File Explorer once they're confident.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


def _infer_export_root(paths: list[Path]) -> Path | None:
    """Longest common parent directory of the given paths, or None."""
    if not paths:
        return None
    try:
        common = Path(paths[0]).parent
        for p in paths[1:]:
            # walk up until prefix matches
            parts_a = common.parts
            parts_b = p.parent.parts
            n = 0
            for a, b in zip(parts_a, parts_b):
                if a != b:
                    break
                n += 1
            if n == 0:
                return None
            common = Path(*parts_a[:n])
        return common
    except Exception:
        return None


def purge_discarded(conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str]]]:
    """Move every ``chart_decision IN ('discard','auto_discard')`` file
    into ``<export_root>/_discarded/<relative-path>``.

    Returns ``(moved_count, errors)``. Errors are ``[(path, reason), ...]``.
    """
    rows = conn.execute(
        """
        SELECT id, local_path FROM attachments
         WHERE chart_decision IN ('discard','auto_discard')
           AND local_path IS NOT NULL AND local_path != ''
        """
    ).fetchall()
    if not rows:
        return 0, []

    paths = [Path(r["local_path"]) for r in rows if r["local_path"]]
    root = _infer_export_root(paths)
    if root is None:
        # Fall back to per-file sibling _discarded folder
        log.info("purge: no common export root; using per-file sibling folders")

    moved = 0
    errors: list[tuple[str, str]] = []

    for r in rows:
        aid = r["id"]
        src = Path(r["local_path"])
        if not src.exists():
            errors.append((str(src), "source file missing"))
            continue
        try:
            if root is not None:
                rel = src.relative_to(root)
                dst = root / "_discarded" / rel
            else:
                dst = src.parent / "_discarded" / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            # If destination already exists (e.g. second purge), add suffix
            if dst.exists():
                stem = dst.stem
                suffix = dst.suffix
                i = 1
                while True:
                    candidate = dst.with_name(f"{stem}__{i}{suffix}")
                    if not candidate.exists():
                        dst = candidate
                        break
                    i += 1
            shutil.move(str(src), str(dst))
            conn.execute(
                "UPDATE attachments SET local_path = ? WHERE id = ?",
                (str(dst), aid),
            )
            moved += 1
        except Exception as exc:
            errors.append((str(src), f"{type(exc).__name__}: {exc}"))

    conn.commit()
    log.info(
        "purge: moved %d files into _discarded/ (%d errors)",
        moved, len(errors),
    )
    return moved, errors
