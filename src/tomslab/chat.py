"""Ask Tom — retrieval-augmented chat over Discord + Tom's PDFs.

Pipeline per question:
  1. Pull top-K conversation windows AND top-K PDF pages via
     ``semantic.mixed_semantic_search``.
  2. Format retrieved context into a compact system prompt with citation
     tags — `[msg:<discord_id>]` for Discord messages and `[doc:<page_id>]`
     for PDF pages.
  3. Call the Gemini chat provider (configurable via Settings) with a
     short system prompt that insists on grounding + citations.
  4. Return the answer + structured list of citation targets so the UI
     can turn them into clickable links.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field

from tomslab import db as dbmod, search as searchmod, semantic, spelling, visual
from tomslab.ai import registry
from tomslab.ai.base import ProviderError, ProviderUnavailable
from tomslab.paths import database_path

log = logging.getLogger(__name__)


K_TOTAL = 14          # max retrieved contexts pulled per question
K_DISCORD_SEM = 5     # Discord messages from semantic search
K_DISCORD_KW = 4      # Discord messages from FTS5 keyword search (merged)
K_DOCS_SEM = 3        # Doc pages from semantic search
K_DOCS_KW = 2         # Doc pages from keyword (LIKE) search
PER_DOC_CAP = 1       # 1 page per document in the top-K doc slice so a single
                      # long PDF (e.g. Market Structure's 72 pages) can't crowd
                      # out shorter authored docs
CONTEXT_CHAR_CAP = 700  # per retrieved snippet


SYSTEM_PROMPT_BASE = """\
You are Tom's Lab — an expert study assistant for trader Tom B's teachings on
Bookmap order flow, volume profile, and auction market theory.

Corpus window: the Discord posts in this database span **December 2021 through
August 2023**. Interpret "recently" and similar time words within that window;
anything after August 2023 is not available here and should be acknowledged
plainly if asked.

Source of Tom's material (authoritative):
- Tom B does **not** have a personal website. Never claim he does, never suggest
  users visit "his site" or "his page".
- All of Tom's authored PDFs (glossary, 60 Structured Trades, Market Structure,
  Opening Context Alignment, Bookmap Settings, Mean Reversion Structured Trade,
  Auction Market Theory 101, Stats by Target) live in the **Bookmap Discord
  server**, in the **"Traders Lab" channel** (exact Discord channel name:
  `traders-lab-tom-b`), in the **pinned messages section**.
- If a user asks where to find one of Tom's documents, say "pinned section of
  the Traders Lab channel in the Bookmap Discord" — do NOT invent a URL.

Tom's charting stack (what he uses to produce the charts you see in the Discord):
- **Investor/RT** (by Linnsoft) — his primary chart and volume-profile tool.
  To replicate Tom's configurations a user needs: Investor/RT **core package**
  + **Profile package** + **DTN MA** subscription (historical backfill service).
- **Bookmap** — for the order-flow heatmap / liquidity visualization that sits
  alongside the Investor/RT charts.
- There IS a legitimate public thread covering Tom's Investor/RT work, curated
  by a Linnsoft community member named Eddy:
  **https://www.linnsoft.com/topic/tom-b-traders-lab** — you may reference this
  link when the user asks about tools, chart replication, or where to learn the
  software side. Do not invent other URLs.

How to answer:
- Synthesize a helpful answer from the retrieved sources. Tom's Discord posts
  are often fragmentary ("no NVPOC there", "watch IBH for absorption") —
  connect them into a coherent explanation using Tom's glossary and PDFs.
- Cite every substantive claim inline using the exact tag next to the source
  header. Three kinds of citations exist:
    • `[msg:916502712684793916]` — a Discord post
    • `[doc:42]`                 — a page of a Tom-authored (or third-party) PDF
    • `[vid:1234]`               — a Tom YouTube transcript chunk (TomTube).
      Video citations are rich: they render as a clickable "▶ open at 14:32
      on YouTube" link, so prefer them when available — the user can jump
      straight to Tom saying it.
  Prefer multiple citations when you're stitching partial evidence.
- Prefer Tom's own words (his Discord posts AND his YouTube transcripts)
  over third-party references when both are present. A `vid:` chunk is
  Tom speaking directly, same tier of authority as his own PDFs.
- If the retrieved sources only touch the topic indirectly, still answer using
  the closest relevant context and say which part is inferred. Only refuse
  ("The sources don't cover this") when there is truly nothing relevant.
- Keep answers focused. Short paragraphs or bullets. No "Certainly!" preambles.
- When the user uses a Tom-framework abbreviation (VPOC, NVPOC, IB, RTH, etc.)
  treat it as the expanded glossary meaning below — never guess what it could
  mean or invent a different expansion.
- If the user's current message includes an attached chart image, your answer
  MUST end with this disclaimer block (verbatim), on its own line:
  ⚠️ **This is an experimental research tool — not financial advice.** Verify
  everything independently. You alone are responsible for your trading decisions.
"""


def _glossary_block(conn: sqlite3.Connection) -> str:
    """Render the concepts table as a compact glossary for the system prompt.

    Seeds the model with the *exact* expansions Tom uses so it never invents
    "Inside Bid Limit" for IBL, and never stalls on NVPOC.
    """
    try:
        rows = conn.execute(
            "SELECT name, description FROM concepts ORDER BY name"
        ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = ["Tom's glossary (authoritative — use these expansions):"]
    for r in rows:
        name = (r["name"] or "").strip()
        desc = (r["description"] or "").strip()
        if not name:
            continue
        # "(ABBR) definition" or just "definition" — keep as-is
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines)


def build_system_prompt(conn: sqlite3.Connection) -> str:
    glossary = _glossary_block(conn)
    if not glossary:
        return SYSTEM_PROMPT_BASE
    return SYSTEM_PROMPT_BASE + "\n" + glossary + "\n"


@dataclass
class RetrievedSource:
    kind: str               # 'message' | 'doc_page'
    citation_id: str        # what goes inside [ ... ] in the answer
    author: str             # display name
    when: str               # date or page label
    text: str               # context snippet
    score: float
    # navigation metadata
    message_id: str | None = None
    doc_page_id: int | None = None
    doc_title: str = ""
    doc_page_num: int = 0


@dataclass
class ChatTurn:
    role: str               # 'user' | 'assistant'
    content: str


@dataclass
class AnswerResult:
    answer: str
    citations: list[str]                   # all [msg:*] or [doc:*] tags found
    sources: list[RetrievedSource]          # what we actually fed into the model
    raw_prompt: str = ""                   # kept for debugging, not displayed
    corrected_question: str = ""           # after spell correction (empty if none)
    corrections: list[tuple[str, str]] = field(default_factory=list)
    provider_used: str = ""                # 'gemini' | 'ollama' — which answered
    fallback_reason: str = ""              # populated when we had to fall back


# Matches citations in any wrapping the LLM might emit:
#   [msg:123]              — ideal form
#   [msg:123, msg:456]     — comma-separated in one bracket (common)
#   (vid:642)              — parenthesised
#   bare "msg:123"         — no wrapping at all
# All four get linkified. Word boundary anchors keep it from matching
# random tokens like "http://site/path:name".
CITATION_RE = re.compile(r"(?<![A-Za-z0-9_])(msg|doc|vid):([A-Za-z0-9_\-]+)")


def _fmt_timestamp(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec % 60)
    return f"{m}:{s:02d}"


def _youtube_link(video_url: str, start_sec: float) -> str:
    if not video_url:
        return ""
    sep = "&" if "?" in video_url else "?"
    return f"{video_url}{sep}t={int(max(0, start_sec))}s"


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def retrieve(conn: sqlite3.Connection, question: str) -> list[RetrievedSource]:
    """Hybrid retrieval: semantic + FTS5 keyword, merged and deduped.

    Messages come from two streams:
      * top K_DISCORD_SEM by semantic cosine (meaning match)
      * top K_DISCORD_KW by FTS5 bm25 (literal token match)
    Doc pages come from two streams too:
      * top K_DOCS_SEM by semantic cosine (author-boosted)
      * top K_DOCS_KW by a keyword-overlap rank over doc text
    The streams are unioned — a hit appearing in both types is kept once.

    If the embedding provider (e.g. Ollama) is unreachable, the semantic
    passes are silently skipped and we fall back to keyword-only retrieval
    so the chat still functions with a thinner but non-empty context.
    """
    # ---- Discord: semantic ----------------------------------------------
    try:
        sem_msg = semantic.semantic_search(conn, question, limit=K_DISCORD_SEM * 2)
    except Exception as exc:
        log.warning("semantic_search unavailable (falling back to keyword): %s", exc)
        sem_msg = []
    sem_msg_ids = [h.message_id for h in sem_msg if h.message_id][:K_DISCORD_SEM]

    # ---- Discord: FTS5 keyword (OR-joined over extracted signal tokens) -
    kw_msg_ids = searchmod.keyword_search_ids_broad(
        conn, question, limit=K_DISCORD_KW * 2
    )
    kw_msg_ids = kw_msg_ids[:K_DISCORD_KW]

    # Merge Discord, preserving order (semantic first, then keyword)
    discord_ids: list[str] = []
    seen_mid: set[str] = set()
    for mid in sem_msg_ids + kw_msg_ids:
        if mid and mid not in seen_mid:
            discord_ids.append(mid)
            seen_mid.add(mid)

    # ---- Docs + videos: semantic (mixed path, per-doc capped) ---------
    try:
        mixed = semantic.mixed_semantic_search(conn, question, limit=200)
    except Exception as exc:
        log.warning("mixed_semantic_search unavailable (keyword-only docs): %s", exc)
        mixed = []
    sem_doc_page_ids: list[int] = []
    video_hits: list = []
    per_doc: dict[int, int] = {}
    per_video: dict[str, int] = {}
    K_VIDEOS = 4        # top video chunks per turn
    PER_VIDEO_CAP = 1   # at most one chunk per video in the top-K
    for h in mixed:
        if h.source_type == "doc_page":
            did = h.doc_page.document_id
            if per_doc.get(did, 0) >= PER_DOC_CAP:
                continue
            per_doc[did] = per_doc.get(did, 0) + 1
            sem_doc_page_ids.append(h.doc_page.page_id)
        elif h.source_type == "video_chunk":
            if len(video_hits) >= K_VIDEOS:
                continue
            vid = h.video.video_id
            if per_video.get(vid, 0) >= PER_VIDEO_CAP:
                continue
            per_video[vid] = per_video.get(vid, 0) + 1
            video_hits.append(h)
        if len(sem_doc_page_ids) >= K_DOCS_SEM and len(video_hits) >= K_VIDEOS:
            break

    # ---- Docs: keyword overlap (surfaces rare literal terms) -----------
    kw_doc_page_ids = searchmod.keyword_search_doc_page_ids(
        conn, question, limit=K_DOCS_KW * 3
    )
    # Respect PER_DOC_CAP here too — need to know which document each page
    # belongs to. One small query.
    if kw_doc_page_ids:
        placeholders = ",".join("?" * len(kw_doc_page_ids))
        rows = conn.execute(
            f"SELECT id, document_id FROM document_pages WHERE id IN ({placeholders})",
            kw_doc_page_ids,
        ).fetchall()
        doc_of = {int(r["id"]): int(r["document_id"]) for r in rows}
    else:
        doc_of = {}
    kept_kw_doc: list[int] = []
    for pid in kw_doc_page_ids:
        did = doc_of.get(pid)
        if did is None:
            continue
        if per_doc.get(did, 0) >= PER_DOC_CAP:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        kept_kw_doc.append(pid)
        if len(kept_kw_doc) >= K_DOCS_KW:
            break

    # Merge doc pages, dedupe
    doc_page_ids: list[int] = []
    seen_pid: set[int] = set()
    for pid in sem_doc_page_ids + kept_kw_doc:
        if pid and pid not in seen_pid:
            doc_page_ids.append(pid)
            seen_pid.add(pid)

    out: list[RetrievedSource] = []

    # Fetch message bodies for discord hits.
    if discord_ids:
        ids = discord_ids
        if ids:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"""
                SELECT m.id, m.author_nickname, m.author_name, m.timestamp, m.content
                  FROM messages m WHERE m.id IN ({placeholders})
                """,
                ids,
            ).fetchall()
            by_id = {r["id"]: r for r in rows}
            for mid in discord_ids:
                r = by_id.get(mid)
                if r is None:
                    continue
                text = (r["content"] or "").strip()
                if not text:
                    continue
                if len(text) > CONTEXT_CHAR_CAP:
                    text = text[: CONTEXT_CHAR_CAP - 1].rstrip() + "…"
                out.append(RetrievedSource(
                    kind="message",
                    citation_id=f"msg:{r['id']}",
                    author=r["author_nickname"] or r["author_name"] or "?",
                    when=(r["timestamp"] or "")[:10],
                    text=text,
                    score=0.0,
                    message_id=r["id"],
                ))

    # ---- Video chunks (Tom's YouTube teaching) ------------------------
    # Add as RetrievedSource rows with citation_id "vid:<chunk_id>" so the
    # model can cite them inline. Keep text snippet bounded like docs.
    for h in video_hits:
        v = h.video
        if v is None:
            continue
        row = conn.execute(
            "SELECT text FROM video_chunks WHERE id = ?",
            (v.chunk_id,),
        ).fetchone()
        if row is None:
            continue
        text = (row["text"] or "").strip()
        if not text:
            continue
        if len(text) > CONTEXT_CHAR_CAP:
            text = text[: CONTEXT_CHAR_CAP - 1].rstrip() + "…"
        when = _fmt_timestamp(v.start_sec)
        title_short = v.video_title[:60] + ("…" if len(v.video_title) > 60 else "")
        out.append(RetrievedSource(
            kind="video_chunk",
            citation_id=f"vid:{v.chunk_id}",
            author=f"Tom video  ·  {title_short}",
            when=when,
            text=text,
            score=h.score,
        ))

    # Fetch doc page text for doc hits
    if doc_page_ids:
        placeholders = ",".join("?" * len(doc_page_ids))
        rows = conn.execute(
            f"""
            SELECT p.id AS pid, p.page_num AS pnum,
                   COALESCE(NULLIF(p.ocr_text,''), p.extracted_text) AS text,
                   d.title AS title, d.filename AS fn
              FROM document_pages p JOIN documents d ON d.id = p.document_id
             WHERE p.id IN ({placeholders})
            """,
            doc_page_ids,
        ).fetchall()
        by_pid = {int(r["pid"]): r for r in rows}
        for pid in doc_page_ids:
            r = by_pid.get(pid)
            if r is None:
                continue
            text = (r["text"] or "").strip()
            if not text:
                continue
            if len(text) > CONTEXT_CHAR_CAP:
                text = text[: CONTEXT_CHAR_CAP - 1].rstrip() + "…"
            out.append(RetrievedSource(
                kind="doc_page",
                citation_id=f"doc:{pid}",
                author=r["title"] or r["fn"],
                when=f"page {r['pnum']}",
                text=text,
                score=0.0,
                doc_page_id=pid,
                doc_title=r["title"] or "",
                doc_page_num=int(r["pnum"]),
            ))

    return out[:K_TOTAL]


# ---------------------------------------------------------------------------
# prompting
# ---------------------------------------------------------------------------
def format_context(sources: list[RetrievedSource]) -> str:
    if not sources:
        return "(No relevant sources retrieved.)"
    parts: list[str] = []
    for s in sources:
        header = f"[{s.citation_id}]  {s.author}  ({s.when})"
        parts.append(f"{header}\n{s.text}")
    return "\n\n---\n\n".join(parts)


def build_user_prompt(question: str, sources: list[RetrievedSource]) -> str:
    return (
        "Retrieved sources:\n\n"
        f"{format_context(sources)}\n\n"
        "==========\n\n"
        f"Question: {question}\n\n"
        "Answer using only the retrieved sources. Cite each claim."
    )


# ---------------------------------------------------------------------------
# top-level
# ---------------------------------------------------------------------------
def preview_corrections(question: str) -> tuple[str, list[tuple[str, str]]]:
    """UI-callable spell-check preview. Returns the corrected question plus
    the list of (original, replacement) pairs. Cheap — no network."""
    return spelling.correct_query(question, str(database_path()))


def ask(
    conn: sqlite3.Connection,
    question: str,
    history: list[ChatTurn] | None = None,
    auto_correct: bool = False,
    attachment_path: str | None = None,
    attachment_paths: list[str] | None = None,
) -> AnswerResult:
    """Run one chat turn.

    If ``auto_correct`` is True the function transparently fixes typos
    before retrieval.  When the UI wants to show a "Did you mean" banner
    it should call :func:`preview_corrections` itself and pass the final
    (user-approved) question with ``auto_correct=False``.

    ``attachment_paths`` takes one or more chart images — any combination
    of higher / lower timeframes, Bookmap screenshots, etc.  They're all
    sent multimodally to the chat provider. The CLIP index is queried
    for visually-similar Tom charts using the FIRST attachment as the
    probe (a single probe is enough to surface precedents; ranking with
    multiple queries quickly becomes noisy).  ``attachment_path``
    remains as a backward-compatible alias for a single image.
    """
    history = history or []

    # Merge the single- and list-style attachment params into one list.
    images: list[str] = list(attachment_paths or [])
    if attachment_path and attachment_path not in images:
        images.insert(0, attachment_path)
    probe_image = images[0] if images else None

    corrected_q = ""
    corrections: list[tuple[str, str]] = []
    if auto_correct:
        corrected_q, corrections = spelling.correct_query(
            question, str(database_path())
        )
    retrieval_q = corrected_q if corrections else question

    # 1) retrieve
    sources = retrieve(conn, retrieval_q)

    # 1a) if the user attached a chart, pull the top visually-similar Tom
    # charts from the CLIP index and splice them into the context so the
    # answer can reference precedents.
    if probe_image:
        try:
            similar = visual.visual_search_by_image(conn, probe_image, limit=4)
        except Exception as exc:
            log.warning("visual_search_by_image failed: %s", exc)
            similar = []
        for h in similar:
            if h.source_type == "attachment" and h.message_id:
                row = conn.execute(
                    "SELECT author_nickname, author_name, timestamp, content "
                    "FROM messages WHERE id = ?",
                    (h.message_id,),
                ).fetchone()
                if not row:
                    continue
                text = (row["content"] or "").strip() or "(chart with no caption)"
                if len(text) > CONTEXT_CHAR_CAP:
                    text = text[: CONTEXT_CHAR_CAP - 1].rstrip() + "…"
                sources.append(RetrievedSource(
                    kind="message",
                    citation_id=f"msg:{h.message_id}",
                    author=(row["author_nickname"] or row["author_name"] or "?") + " · similar chart",
                    when=(row["timestamp"] or "")[:10],
                    text=f"[Tom posted a visually similar chart on {(row['timestamp'] or '')[:10]}.] {text}",
                    score=h.score,
                    message_id=h.message_id,
                ))

    # 2) build message list
    messages: list[dict] = []
    for t in history:
        messages.append({"role": t.role, "content": t.content})
    # Use the corrected question inside the prompt so the model answers the
    # intended question, not the typo.
    prompt_q = retrieval_q
    if images:
        n = len(images)
        many = n > 1
        chart_noun = f"{n} chart screenshots" if many else "a chart screenshot"
        prompt_q = (
            f"I'm attaching {chart_noun}. Decipher through Tom's framework: "
            "identify the market context (balanced/trending/opening type), "
            "call out visible reference prices (IB, VPOC, HVNs, LVNs, VWAP, "
            "naked VPOCs), read the order flow, and lay out a plausible "
            "entry / stop / target consistent with Tom's structured-trade "
            "setups.\n\n"
            + (
                "First, for each attached image, state which TIMEFRAME / "
                "chart type it appears to be (e.g. daily/weekly HTF, "
                "intraday RTH, Bookmap heatmap) and then synthesize across "
                "them. Higher-timeframe context should set the bias; the "
                "lower timeframe / Bookmap is where triggers and entries "
                "live.\n\n"
                if many else
                "First, state which TIMEFRAME / chart type the image "
                "appears to be (HTF daily-weekly, intraday RTH, Bookmap "
                "heatmap, etc.) and call out what that means for the read.\n\n"
            )
            + "**CRITICAL price-handling rules:**\n"
            "1. Read every numeric price (current price, reference levels, "
            "VPOCs, HVNs, LVNs, IB High/Low, target, stop) DIRECTLY FROM "
            "THE ATTACHED IMAGE(S). The tooltip, the axis labels on the "
            "right margin, and the candle prints are your only sources of "
            "truth for numbers.\n"
            "2. The retrieved Tom messages below are from OTHER trading "
            "sessions (often years ago). Their numeric prices belong to "
            "those historical sessions and must NEVER be copied into this "
            "analysis as if they were on the attached chart(s). Use "
            "retrieved messages ONLY for framework concepts (how Tom "
            "defines 'initiative' vs 'responsive', what he looks for at "
            "an NVPOC, etc.), not for price levels.\n"
            "3. If you cannot clearly read a price from the image(s), say "
            "\"not visible from this screenshot\" — do NOT guess a number, "
            "do NOT substitute a number from a retrieved message.\n"
            "4. When you cite a retrieved message, cite it for the CONCEPT "
            "it teaches, not for its price numbers.\n\n"
            "**Ask for more context when appropriate:**\n"
            "If a responsible answer to the user's question needs "
            "information that ISN'T visible in the attached chart(s), "
            "end your answer with a clearly-labelled **\"To go deeper, "
            "please attach:\"** section listing exactly what you'd need "
            "next. Examples:\n"
            "  - an intraday RTH chart if only HTF is attached\n"
            "  - the Bookmap heatmap for current order-flow / absorption\n"
            "  - VWAP overlay if not visible\n"
            "  - the IB High / IB Low range\n"
            "Never invent a trade plan to fill in gaps — surface the gap "
            "and let the user decide whether to attach more.\n\n"
            "Then answer the user's specific question:\n\n" + prompt_q
        )
    messages.append({"role": "user", "content": build_user_prompt(prompt_q, sources)})

    # 3) call provider (with automatic fallback chain)
    provider = registry.get_chat_provider(conn)
    image_paths = images or None
    system = build_system_prompt(conn)

    answer = ""
    provider_used = provider.name
    fallback_reason = ""
    try:
        answer = provider.chat(messages, system=system, image_paths=image_paths)
    except (ProviderError, ProviderUnavailable) as exc:
        # Primary failed — try the fallback chat provider if one is configured.
        fb = registry.get_chat_fallback(conn)
        if fb is None:
            raise RuntimeError(f"Chat provider error: {exc}") from exc
        fallback_reason = str(exc)[:200]
        log.warning(
            "Chat primary (%s) failed — falling back to %s: %s",
            provider.name, fb.name, fallback_reason,
        )
        try:
            answer = fb.chat(messages, system=system, image_paths=image_paths)
            provider_used = fb.name
        except (ProviderError, ProviderUnavailable) as exc2:
            # Both failed. Surface the primary's error since that's the
            # one the user presumably configured.
            raise RuntimeError(
                f"Chat failed on both {provider.name} and {fb.name}: "
                f"{exc} // fallback: {exc2}"
            ) from exc2

    citations = [
        f"{m.group(1)}:{m.group(2)}" for m in CITATION_RE.finditer(answer or "")
    ]
    return AnswerResult(
        answer=answer or "",
        citations=citations,
        sources=sources,
        raw_prompt=messages[-1]["content"],
        corrected_question=corrected_q if corrections else "",
        corrections=corrections,
        provider_used=provider_used,
        fallback_reason=fallback_reason,
    )
