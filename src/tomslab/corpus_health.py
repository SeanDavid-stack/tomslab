"""Corpus health audit — what does the AI actually have access to?

Produces a structured report of:
  * how many items exist in each table
  * how many of those have embeddings built (per-model-tag)
  * which categories fall below a minimum coverage bar
  * optional canary retrieval test — fires a handful of known-good
    queries against Ask Tom's retrieval pipeline and verifies each
    returns hits from every source type (messages, docs, videos)

Invoked by Help → Corpus Health Check in the UI. Runs entirely on
the local DB — no network, no AI calls (except the canary test which
exercises the embedding provider).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from tomslab import db as dbmod

log = logging.getLogger(__name__)


# Any category below this coverage triggers a warning.
MIN_COVERAGE_PCT = 95


@dataclass
class CategoryCoverage:
    name: str
    total: int
    embedded: int
    warning: str = ""            # non-empty if something is off
    detail: str = ""             # human-readable suggestion

    @property
    def pct(self) -> float:
        if self.total <= 0:
            return 100.0
        return (self.embedded / self.total) * 100.0

    @property
    def status(self) -> str:
        if self.total == 0:
            return "empty"
        if self.pct >= MIN_COVERAGE_PCT:
            return "ok"
        if self.pct >= 50:
            return "warn"
        return "critical"


@dataclass
class CanaryResult:
    query: str
    n_messages: int
    n_doc_pages: int
    n_video_chunks: int
    passed: bool
    note: str = ""


@dataclass
class CorpusHealthReport:
    categories: list[CategoryCoverage]
    canaries: list[CanaryResult] = field(default_factory=list)
    summary: str = ""
    overall_ok: bool = False


# Queries we run against the live retrieval to verify every source
# pipeline is wired up end-to-end. Pick topics Tom definitely covers
# in all three source types (Discord, PDFs, YouTube).
CANARY_QUERIES = [
    "What is VPOC?",
    "How does Tom use the volume profile?",
    "What is mean reversion in Tom's framework?",
    "How does Tom approach the opening?",
]


def audit(conn: sqlite3.Connection) -> list[CategoryCoverage]:
    """Count items vs embedded items in every retrieval category.

    The AI can only retrieve items that have embeddings in the current
    model's index. A large gap between total and embedded = silent data
    loss at query time.
    """
    # Embedding model tags — same lookups the semantic + visual modules use.
    from tomslab import visual
    try:
        clip_tag = visual.clip_model_tag(conn)
    except Exception:
        clip_tag = "ViT-B-32:openai"

    categories: list[CategoryCoverage] = []

    # --- Discord windows (what the AI actually retrieves) --------------
    n_msgs = int(conn.execute(
        "SELECT COUNT(*) FROM messages"
    ).fetchone()[0] or 0)
    n_windows = int(conn.execute(
        "SELECT COUNT(*) FROM conversation_windows"
    ).fetchone()[0] or 0)
    n_win_embedded = int(conn.execute(
        "SELECT COUNT(*) FROM window_embeddings"
    ).fetchone()[0] or 0)
    cat = CategoryCoverage(
        name="Discord conversation windows",
        total=n_windows,
        embedded=n_win_embedded,
    )
    if n_windows == 0 and n_msgs > 0:
        cat.warning = "Windows not built"
        cat.detail = (
            f"{n_msgs:,} messages exist but aren't bundled into "
            "retrievable windows. Re-run ingest to build them."
        )
    elif cat.pct < MIN_COVERAGE_PCT and n_windows > 0:
        missing = n_windows - n_win_embedded
        cat.warning = f"{missing:,} windows un-embedded"
        cat.detail = (
            "Run File → Process → Build text embeddings to embed the "
            "remaining windows. Ask Tom can't semantically retrieve "
            "them until this finishes."
        )
    categories.append(cat)

    # --- PDF doc pages -------------------------------------------------
    n_pages = int(conn.execute(
        "SELECT COUNT(*) FROM document_pages "
        " WHERE rendered_path IS NOT NULL AND rendered_path != ''"
    ).fetchone()[0] or 0)
    n_page_text_embeds = int(conn.execute(
        "SELECT COUNT(*) FROM document_page_embeddings"
    ).fetchone()[0] or 0)
    cat = CategoryCoverage(
        name="PDF page text embeddings",
        total=n_pages,
        embedded=n_page_text_embeds,
    )
    if cat.pct < MIN_COVERAGE_PCT and n_pages > 0:
        missing = n_pages - n_page_text_embeds
        cat.warning = f"{missing:,} pages un-embedded"
        cat.detail = "Run File → Process → Build text embeddings."
    categories.append(cat)

    # --- Video chunks -------------------------------------------------
    n_videos = int(conn.execute(
        "SELECT COUNT(*) FROM videos "
        " WHERE transcript_status = 'transcribed'"
    ).fetchone()[0] or 0)
    n_chunks = int(conn.execute(
        "SELECT COUNT(*) FROM video_chunks"
    ).fetchone()[0] or 0)
    n_chunk_embeds = int(conn.execute(
        "SELECT COUNT(*) FROM video_chunk_embeddings"
    ).fetchone()[0] or 0)
    cat = CategoryCoverage(
        name="YouTube video chunks",
        total=n_chunks,
        embedded=n_chunk_embeds,
    )
    cat.detail = f"{n_videos:,} videos transcribed so far."
    if cat.pct < MIN_COVERAGE_PCT and n_chunks > 0:
        missing = n_chunks - n_chunk_embeds
        cat.warning = f"{missing:,} chunks un-embedded"
        cat.detail += (
            f" Run File → Process → Build text embeddings to cover "
            "the remaining chunks."
        )
    categories.append(cat)

    # --- CLIP image embeddings (Gallery visual search) -----------------
    # Only count attachments the user DIDN'T discard.
    n_keep_imgs = int(conn.execute(
        "SELECT COUNT(*) FROM attachments a "
        " WHERE a.local_path IS NOT NULL AND a.local_path != '' "
        "   AND (a.chart_decision IS NULL "
        "        OR a.chart_decision IN ('keep','auto_keep')) "
        "   AND (lower(a.filename) LIKE '%.png' "
        "     OR lower(a.filename) LIKE '%.jpg' "
        "     OR lower(a.filename) LIKE '%.jpeg' "
        "     OR lower(a.filename) LIKE '%.webp' "
        "     OR lower(a.filename) LIKE '%.bmp')"
    ).fetchone()[0] or 0)
    # Count only embeddings for attachments the user hasn't discarded.
    # Stale embeddings for discarded images still exist in the DB from
    # before the classifier ran — we don't want them inflating the
    # coverage ratio (previously showed >100%).
    n_clip_embeds = int(conn.execute(
        """
        SELECT COUNT(*)
          FROM image_embeddings ie
          JOIN attachments a ON a.id = ie.attachment_id
         WHERE ie.model = ?
           AND (a.chart_decision IS NULL
                OR a.chart_decision IN ('keep','auto_keep'))
        """,
        (clip_tag,),
    ).fetchone()[0] or 0)
    # Also tally stale vectors so we can surface them for cleanup.
    n_stale_embeds = int(conn.execute(
        """
        SELECT COUNT(*)
          FROM image_embeddings ie
          JOIN attachments a ON a.id = ie.attachment_id
         WHERE ie.model = ?
           AND a.chart_decision IN ('discard','auto_discard')
        """,
        (clip_tag,),
    ).fetchone()[0] or 0)
    cat = CategoryCoverage(
        name=f"Image CLIP embeddings ({clip_tag})",
        total=n_keep_imgs,
        embedded=n_clip_embeds,
    )
    if n_stale_embeds > 0:
        cat.detail = (
            f"{n_stale_embeds:,} stale CLIP vectors for discarded "
            "images also exist (from before you classified). They "
            "won't appear in Gallery search but bloat the DB — the "
            "shipping data-pack step strips them automatically."
        )
    if cat.pct < MIN_COVERAGE_PCT and n_keep_imgs > 0:
        missing = n_keep_imgs - n_clip_embeds
        cat.warning = f"{missing:,} images un-embedded"
        cat.detail = (
            "Run File → Process → Build image (CLIP) embeddings. "
            "Without this the Gallery visual search can't find charts."
        )
    categories.append(cat)

    # --- Chart classifier coverage -------------------------------------
    n_total_imgs = int(conn.execute(
        "SELECT COUNT(*) FROM attachments "
        " WHERE local_path IS NOT NULL AND local_path != ''"
    ).fetchone()[0] or 0)
    n_classified = int(conn.execute(
        "SELECT COUNT(*) FROM attachments "
        " WHERE local_path IS NOT NULL AND local_path != '' "
        "   AND chart_decision IS NOT NULL"
    ).fetchone()[0] or 0)
    cat = CategoryCoverage(
        name="Chart classifier decisions",
        total=n_total_imgs,
        embedded=n_classified,
    )
    if cat.pct < MIN_COVERAGE_PCT and n_total_imgs > 0:
        missing = n_total_imgs - n_classified
        cat.warning = f"{missing:,} images unclassified"
        cat.detail = (
            "Run File → Process → Classify Discord images to decide "
            "keep vs discard. Un-scored images pollute the Gallery."
        )
    categories.append(cat)

    return categories


def run_canary(conn: sqlite3.Connection, query: str) -> CanaryResult:
    """Fire one query through the LIVE retrieval pipeline Ask Tom uses
    and count hits by source type. Uses ``chat.retrieve()`` directly
    so the canary tests what the AI actually sees — not a raw
    ``mixed_semantic_search`` call which is dominated by videos and
    undercounts docs.

    A 'passed' canary returns at least 1 hit from each of
    (messages, doc_pages, video_chunks). If any is zero, Ask Tom is
    silently skipping that source type on this topic.
    """
    try:
        from tomslab import chat as chatmod
        sources = chatmod.retrieve(conn, query)
    except Exception as exc:
        return CanaryResult(
            query=query, n_messages=0, n_doc_pages=0, n_video_chunks=0,
            passed=False,
            note=f"chat.retrieve failed: {exc}",
        )

    n_msg = sum(1 for s in sources if s.kind == "message")
    n_doc = sum(1 for s in sources if s.kind == "doc_page")
    n_vid = sum(1 for s in sources if s.kind == "video_chunk")

    passed = n_msg > 0 and n_doc > 0 and n_vid > 0
    note = ""
    if not passed:
        missing = []
        if n_msg == 0:
            missing.append("messages")
        if n_doc == 0:
            missing.append("PDF pages")
        if n_vid == 0:
            missing.append("video chunks")
        note = f"Ask Tom returned no {', '.join(missing)} for this query"
    return CanaryResult(
        query=query,
        n_messages=n_msg,
        n_doc_pages=n_doc,
        n_video_chunks=n_vid,
        passed=passed,
        note=note,
    )


def full_report(
    conn: sqlite3.Connection,
    *,
    include_canaries: bool = True,
    progress: Callable[[str], None] | None = None,
) -> CorpusHealthReport:
    if progress:
        progress("Counting corpus rows…")
    cats = audit(conn)

    canaries: list[CanaryResult] = []
    if include_canaries:
        for i, q in enumerate(CANARY_QUERIES, 1):
            if progress:
                progress(f"Running canary {i}/{len(CANARY_QUERIES)}: {q}")
            canaries.append(run_canary(conn, q))

    problems = sum(1 for c in cats if c.status in ("critical", "warn"))
    canary_fails = sum(1 for c in canaries if not c.passed)

    if problems == 0 and canary_fails == 0:
        summary = (
            f"✅ All {len(cats)} categories meet the {MIN_COVERAGE_PCT}% "
            f"coverage bar. All {len(canaries)} canary queries returned "
            f"hits from every source type. Ask Tom has full access to "
            f"your corpus."
        )
        overall_ok = True
    else:
        bits = []
        if problems:
            bits.append(f"{problems} category with low coverage")
        if canary_fails:
            bits.append(f"{canary_fails} canary query missing a source type")
        summary = (
            f"⚠️ Issues found: {' · '.join(bits)}. See the details "
            f"below and run the suggested action for each."
        )
        overall_ok = False

    return CorpusHealthReport(
        categories=cats,
        canaries=canaries,
        summary=summary,
        overall_ok=overall_ok,
    )
