"""Chart-vs-non-chart classifier for Discord attachments.

Two-stage filter to reduce the ~25 GB junk footprint of a full
Discord export down to the actual trading charts worth keeping
in Tom's Lab's visual index.

Stage 1 (cheap, fast): size + format rules. Files <50 KB, GIFs,
and exact-content duplicates are auto-flagged 'auto_discard' with
no CLIP cost.

Stage 2 (CLIP, slower): zero-shot classify surviving images against
two groups of text prompts ("chart-like" vs "not-a-chart"). The
probability assigned to the chart group becomes ``chart_score``.
Thresholds auto-decide the obvious cases; the middle band is left
for human review via the Review UI.

Outputs are written back onto the ``attachments`` table via the
columns added in ``db._apply_column_migrations``:
  - ``chart_score``     REAL  — probability (0..1) it's a chart
  - ``chart_decision``  TEXT  — 'auto_keep' | 'auto_discard' | 'keep' | 'discard' | NULL
  - ``classified_at``   TEXT  — ISO timestamp of the decision

No files are deleted here. Purging (moving to ``_discarded/``) is
a separate explicit user action.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tomslab import visual

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]

# Any image under this size is almost certainly an emoji, avatar,
# Discord UI sprite, or thumbnail — not a trading chart.
MIN_CHART_SIZE_BYTES = 50 * 1024

# Scoring thresholds. Everything above AUTO_KEEP is auto-kept;
# everything below AUTO_DISCARD is auto-discarded; the band between
# is flagged for human review (chart_decision stays NULL).
AUTO_KEEP_THRESHOLD    = 0.70
AUTO_DISCARD_THRESHOLD = 0.30

CHART_PROMPTS: dict[str, list[str]] = {
    "chart": [
        "a trading chart with candlesticks",
        "a Bookmap liquidity heatmap",
        "a volume profile chart",
        "a market depth display",
        "an order flow chart",
        "a financial price chart with technical indicators",
        "a screenshot of a trading platform",
        "a candlestick chart with moving averages",
    ],
    "not_chart": [
        "a selfie",
        "a meme",
        "a photograph of a person",
        "a screenshot of text",
        "a cat",
        "a food photo",
        "a cartoon",
        "a logo",
        "a blank image",
        "an emoji",
        "a screenshot of a chat message",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pending_classify_count(conn: sqlite3.Connection) -> int:
    """Number of attachments that have no chart decision yet."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM attachments
         WHERE local_path IS NOT NULL AND local_path != ''
           AND chart_decision IS NULL
        """
    ).fetchone()
    return int(row["n"] or 0)


def apply_cheap_prefilter(
    conn: sqlite3.Connection,
    *,
    progress: ProgressFn | None = None,
) -> dict[str, int]:
    """Stage 1 — mark obvious discards without touching CLIP.

    Rules (applied only to rows where ``chart_decision IS NULL``):
      * ``file_size`` < ``MIN_CHART_SIZE_BYTES`` → auto_discard
      * filename ends in ``.gif``                → auto_discard
      * content_hash already decided 'keep' elsewhere → mirror
        (dedup — we never score the same content twice)

    Returns counts: ``{"small": N, "gif": N, "dedup_keep": N, "dedup_discard": N}``.
    """
    counts = {"small": 0, "gif": 0, "dedup_keep": 0, "dedup_discard": 0}

    # small files
    cur = conn.execute(
        """
        UPDATE attachments
           SET chart_decision = 'auto_discard',
               chart_score    = 0.0,
               classified_at  = ?
         WHERE chart_decision IS NULL
           AND local_path IS NOT NULL AND local_path != ''
           AND file_size IS NOT NULL
           AND file_size < ?
        """,
        (_now(), MIN_CHART_SIZE_BYTES),
    )
    counts["small"] = cur.rowcount or 0

    # gifs
    cur = conn.execute(
        """
        UPDATE attachments
           SET chart_decision = 'auto_discard',
               chart_score    = 0.0,
               classified_at  = ?
         WHERE chart_decision IS NULL
           AND local_path IS NOT NULL AND local_path != ''
           AND lower(filename) LIKE '%.gif'
        """,
        (_now(),),
    )
    counts["gif"] = cur.rowcount or 0

    # dedup: mirror the decision from any already-classified duplicate
    # (same content_hash). This matters because DCE stores each message's
    # attachments independently even when Tom re-posts the same chart,
    # and Discord avatars get attached to every message the user posts.
    cur = conn.execute(
        """
        UPDATE attachments AS a
           SET chart_decision = (
                SELECT b.chart_decision FROM attachments b
                 WHERE b.content_hash = a.content_hash
                   AND b.chart_decision IN ('keep','auto_keep')
                 LIMIT 1
               ),
               chart_score = (
                SELECT b.chart_score FROM attachments b
                 WHERE b.content_hash = a.content_hash
                   AND b.chart_decision IN ('keep','auto_keep')
                 LIMIT 1
               ),
               classified_at = ?
         WHERE a.chart_decision IS NULL
           AND a.content_hash IS NOT NULL AND a.content_hash != ''
           AND EXISTS (
                SELECT 1 FROM attachments b
                 WHERE b.content_hash = a.content_hash
                   AND b.chart_decision IN ('keep','auto_keep')
               )
        """,
        (_now(),),
    )
    counts["dedup_keep"] = cur.rowcount or 0

    cur = conn.execute(
        """
        UPDATE attachments AS a
           SET chart_decision = (
                SELECT b.chart_decision FROM attachments b
                 WHERE b.content_hash = a.content_hash
                   AND b.chart_decision IN ('discard','auto_discard')
                 LIMIT 1
               ),
               chart_score = 0.0,
               classified_at = ?
         WHERE a.chart_decision IS NULL
           AND a.content_hash IS NOT NULL AND a.content_hash != ''
           AND EXISTS (
                SELECT 1 FROM attachments b
                 WHERE b.content_hash = a.content_hash
                   AND b.chart_decision IN ('discard','auto_discard')
               )
        """,
        (_now(),),
    )
    counts["dedup_discard"] = cur.rowcount or 0

    conn.commit()
    if progress:
        progress(
            f"Cheap filter: -{counts['small']} tiny, -{counts['gif']} gif, "
            f"±{counts['dedup_keep'] + counts['dedup_discard']} dedup",
            1, 1,
        )
    log.info(
        "chart classifier pre-filter: small=%d gif=%d dedup_keep=%d dedup_discard=%d",
        counts["small"], counts["gif"], counts["dedup_keep"], counts["dedup_discard"],
    )
    return counts


def classify_remaining_with_clip(
    conn: sqlite3.Connection,
    *,
    progress: ProgressFn | None = None,
    batch_commit: int = 100,
    limit: int | None = None,
) -> dict[str, int]:
    """Stage 2 — score every still-undecided attachment with CLIP.

    Writes ``chart_score`` for every processed row. Auto-decides
    rows at the extremes (> AUTO_KEEP or < AUTO_DISCARD). Rows in
    the middle band keep ``chart_decision = NULL`` and surface in
    the Review UI.

    Returns counts: ``{"scored": N, "auto_keep": N, "auto_discard": N, "review": N, "unreadable": N}``.
    """
    bundle = visual.ensure_loaded(conn)

    sql = (
        "SELECT id, local_path, content_hash FROM attachments "
        "WHERE chart_decision IS NULL "
        "  AND local_path IS NOT NULL AND local_path != '' "
        "  AND (lower(filename) LIKE '%.png' "
        "    OR lower(filename) LIKE '%.jpg' "
        "    OR lower(filename) LIKE '%.jpeg' "
        "    OR lower(filename) LIKE '%.webp' "
        "    OR lower(filename) LIKE '%.bmp') "
        "ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    total = len(rows)

    counts = {"scored": 0, "auto_keep": 0, "auto_discard": 0,
              "review": 0, "unreadable": 0}

    if total == 0:
        log.info("chart classifier: nothing to score (all attachments decided)")
        return counts

    log.info("chart classifier: scoring %d images with CLIP", total)
    now = _now()

    for i, r in enumerate(rows, start=1):
        aid = r["id"]
        path = r["local_path"]
        if not path or not Path(path).exists():
            conn.execute(
                "UPDATE attachments SET chart_decision = 'auto_discard', "
                "    chart_score = 0.0, classified_at = ? WHERE id = ?",
                (now, aid),
            )
            counts["unreadable"] += 1
            counts["auto_discard"] += 1
            continue

        probs = visual.classify_image(bundle, path, CHART_PROMPTS)
        score = float(probs.get("chart", 0.0)) if probs else 0.0

        if not probs:
            decision = "auto_discard"
            counts["unreadable"] += 1
            counts["auto_discard"] += 1
        elif score >= AUTO_KEEP_THRESHOLD:
            decision = "auto_keep"
            counts["auto_keep"] += 1
        elif score <= AUTO_DISCARD_THRESHOLD:
            decision = "auto_discard"
            counts["auto_discard"] += 1
        else:
            decision = None
            counts["review"] += 1
        counts["scored"] += 1

        conn.execute(
            "UPDATE attachments SET chart_score = ?, chart_decision = ?, "
            "    classified_at = ? WHERE id = ?",
            (score, decision, now, aid),
        )

        if i % batch_commit == 0:
            conn.commit()
            if progress:
                progress(
                    f"Scoring charts ({counts['auto_keep']} kept, "
                    f"{counts['auto_discard']} discarded, "
                    f"{counts['review']} for review)",
                    i, total,
                )

    conn.commit()
    if progress:
        progress("Scoring complete", total, total)
    log.info(
        "chart classifier done: auto_keep=%d auto_discard=%d review=%d unreadable=%d",
        counts["auto_keep"], counts["auto_discard"],
        counts["review"], counts["unreadable"],
    )
    return counts


def run_full_classification(
    conn: sqlite3.Connection,
    *,
    progress: ProgressFn | None = None,
) -> dict:
    """Convenience wrapper: pre-filter then CLIP-score the remainder."""
    if progress:
        progress("Applying size + format pre-filter", 0, 1)
    cheap = apply_cheap_prefilter(conn, progress=progress)
    if progress:
        progress("Loading CLIP model", 0, 1)
    clip_counts = classify_remaining_with_clip(conn, progress=progress)
    return {"prefilter": cheap, "clip": clip_counts}
