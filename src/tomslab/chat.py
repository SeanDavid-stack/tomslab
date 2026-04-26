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
K_DISCORD_SEM = 5     # Discord messages from semantic search (default — overridable in settings)
K_DISCORD_KW = 4      # Discord messages from FTS5 keyword search (merged)
K_DOCS_SEM = 3        # Doc pages from semantic search
K_DOCS_KW = 2         # Doc pages from keyword (LIKE) search

# Default values for per-cohort Discord budgets. The user can override
# both via Settings → Ask Tom. When ``chat_tom_only`` is set, only Tom's
# messages are returned and the budget collapses to k_tom + k_other.
DEFAULT_K_DISCORD_TOM   = 5
DEFAULT_K_DISCORD_OTHER = 4
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
  header. **The prefix (`msg:`, `doc:`, or `vid:`) is NON-NEGOTIABLE** —
  bare numeric citations like `[1234]` or `[p42]` are broken and will
  NOT render for the user. Always write:
    • `[msg:916502712684793916]` — a Discord post
    • `[doc:42]`                 — a page of a Tom-authored (or third-party) PDF
    • `[vid:1234]`               — a Tom YouTube transcript chunk (TomTube).
      Video citations render as a clickable "▶ open at 14:32 on YouTube"
      link — prefer them when available. The user can jump straight to
      Tom saying it.
  Each citation wraps ONE id: write `[vid:1234] [vid:5678]`, NOT `[1234, 5678]`.
  Prefer multiple citations when you're stitching partial evidence.
- Prefer Tom's own words (his Discord posts AND his YouTube transcripts)
  over third-party references when both are present. A `vid:` chunk is
  Tom speaking directly, same tier of authority as his own PDFs.
- **Cite across every source type that's in your context.** If the
  retrieval gave you Discord messages AND PDF pages AND video chunks,
  your answer MUST include at least one citation from EACH type whose
  content is relevant — at minimum one `[msg:...]`, one `[doc:...]`,
  AND one `[vid:...]`. Do not lean 100% on any single type when the
  others are present. Each type carries different evidence:
    • `[msg:...]` — Tom's real-time reasoning during specific market moments. Skipping these hides the live-trading examples users came for.
    • `[doc:...]` — Tom's authored framework definitions. Skipping these means the answer lacks canonical grounding.
    • `[vid:...]` — Tom speaking directly. Skipping these breaks the click-to-YouTube affordance users rely on.
  If a type genuinely has no relevant hit (the retrieved messages/pages/chunks don't address the question), you may omit it — but the default is to cite all three.
- If the retrieved sources only touch the topic indirectly, still answer using
  the closest relevant context and say which part is inferred. Only refuse
  ("The sources don't cover this") when there is truly nothing relevant.
- Keep answers focused. Short paragraphs or bullets. No "Certainly!" preambles.
- When the user uses a Tom-framework abbreviation (VPOC, NVPOC, IB, RTH, etc.)
  treat it as the expanded glossary meaning below — never guess what it could
  mean or invent a different expansion.
- **Attribution of foundational concepts:** Many terms Tom uses come from
  established frameworks that predate his teaching — Auction Market Theory
  (Pete Steidlmayer, 1980s), Market Profile, standard volume-profile
  terminology (VPOC, HVN, LVN, VAH, VAL), and Jim Dalton's "Mind Over
  Markets" vocabulary (initiative vs responsive activity, balanced vs
  unbalanced auctions, context). When discussing these, **credit the
  original source** — write "from Auction Market Theory" or "Steidlmayer's
  Market Profile framework" or "a standard AMT concept Tom builds on"
  rather than implying Tom coined them. Tom's *original* contributions
  are his specific framing, trade triggers, and the integration of these
  tools — call those out separately so the user learns what's Tom's
  innovation vs inherited vocabulary.
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


DEEP_DIVE_ADDITION = """\

=== DEEP DIVE MODE ENABLED ===

The user has requested a *research briefing*, not a quick answer. You
have a much wider source pool than usual — typically 60-90 sources
across Discord posts, Tom's PDFs, and YouTube transcripts.

Write a thorough, well-organised piece that:

1. **Opens with a 2-3 sentence executive summary** of Tom's framework
   on this topic.
2. **Uses Markdown headings** (## and ###) to structure the major
   sub-topics. Typical sections: Definition / Setup conditions /
   Entry & exit / Examples from Tom's teaching / Common mistakes /
   Related concepts.
3. **Quotes Tom's own words directly** from Discord and video
   transcripts when a phrase is memorable or precise. Use
   > blockquote formatting for quotes longer than one sentence.
4. **Cites every claim** with the appropriate `[msg:id]`, `[doc:id]`,
   or `[vid:chunk_id]` tag. **Use video citations liberally** —
   Tom's spoken teaching is first-tier evidence on par with his
   PDFs. When video chunks are in your context, cite them.
5. **Shows evolution of thinking over time** where relevant — if
   Tom's approach to a concept shifted between 2022 and 2024, note
   that with the earlier and later sources.
6. **Ends with a short bullet list of "Key takeaways"** —
   3-5 concrete, actionable points.

Target length: 600-1200 words. Don't pad. Cut sections that don't
apply to the specific question. A shorter, sharper briefing beats a
bloated one.
"""


def build_system_prompt(
    conn: sqlite3.Connection, *, deep: bool = False
) -> str:
    glossary = _glossary_block(conn)
    prompt = SYSTEM_PROMPT_BASE
    if glossary:
        prompt = prompt + "\n" + glossary + "\n"
    if deep:
        prompt = prompt + DEEP_DIVE_ADDITION
    return prompt


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
# Case-insensitive match so "[Doc:123]" / "[MSG:abc]" / "[Vid:4]" from
# less-disciplined LLMs still get picked up. Normalisation to lowercase
# happens downstream so the sources panel renders the correct icons.
CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_])(msg|doc|vid):([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)


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
# Deep Dive budget multipliers. 3× across the board produces a
# "research briefing"-scale context pool without blowing past common
# LLM context windows (30-60 sources × 700 chars ≈ 20-40K chars).
DEEP_MULTIPLIER = 3
DEEP_K_VIDEOS = 15
DEEP_PER_VIDEO_CAP = 2   # allow two chunks per video so long lectures
                         # can surface multiple moments, not just one.
DEEP_K_DOCS_SEM = 10
DEEP_K_DOCS_KW = 6
DEEP_PER_DOC_CAP = 3     # multi-page evidence from the same playbook
                         # is fine in deep mode.


def retrieve(
    conn: sqlite3.Connection, question: str, *, deep: bool = False,
) -> list[RetrievedSource]:
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

    ``deep=True`` unlocks a 3× budget for every source type and relaxes
    the per-video / per-doc caps so the AI sees a much wider context
    pool. Used by the Deep Dive button to produce long, structured
    research briefings instead of quick answers.
    """
    # ---- Read per-cohort retrieval budget from settings -----------------
    # chat_tom_only        — "1" to return only Tom B's messages
    # chat_k_discord_tom   — top-K Tom messages to keep
    # chat_k_discord_other — top-K community messages to keep
    # sources_sort_order   — "newest" or "oldest"; applied to the final
    #                        Discord list before it's handed to the LLM
    #                        so the answer's grounding order matches the
    #                        sources panel.
    tom_only = (dbmod.get_setting(conn, "chat_tom_only", "0") or "0") == "1"
    # Videos-only mode short-circuits Discord + PDF retrieval and
    # redirects the whole context window at video transcripts. Deep
    # dives in this mode pull even more chunks since there's no
    # competition from other source types for LLM context space.
    videos_only = (
        dbmod.get_setting(conn, "chat_videos_only", "0") or "0"
    ) == "1"
    # Mirror toggle: Discord-only mode — skips videos + PDFs, returns
    # only Discord conversation windows. If both toggles are on
    # (nonsensical combo) videos_only wins by convention so the user
    # always gets exactly one source class.
    discord_only = (
        dbmod.get_setting(conn, "chat_discord_only", "0") or "0"
    ) == "1"
    if videos_only:
        discord_only = False
    try:
        k_tom = int(dbmod.get_setting(
            conn, "chat_k_discord_tom", str(DEFAULT_K_DISCORD_TOM)
        ) or DEFAULT_K_DISCORD_TOM)
    except ValueError:
        k_tom = DEFAULT_K_DISCORD_TOM
    try:
        k_other = int(dbmod.get_setting(
            conn, "chat_k_discord_other", str(DEFAULT_K_DISCORD_OTHER)
        ) or DEFAULT_K_DISCORD_OTHER)
    except ValueError:
        k_other = DEFAULT_K_DISCORD_OTHER
    # Apply Deep Dive multipliers. These override the per-cohort settings
    # — Deep Dive is an explicit user action for a wide-context briefing.
    # Deep Dive is designed as a Tom-framework briefing, so community
    # messages get zero budget regardless of the user's Tom-only toggle.
    #
    # Provider-aware scaling: llama3.1:8b has only 8K token context;
    # pouring 54 sources × 700 chars into it truncates to garbage. We
    # detect the current primary chat provider and scale Deep Dive's
    # budgets to fit. Gemini (1M context) gets the full treatment.
    if deep:
        tom_only = True
        primary = (dbmod.get_setting(conn, "ai_provider_chat", "ollama")
                   or "ollama").lower()
        if primary == "gemini":
            k_tom = max(k_tom * DEEP_MULTIPLIER, 30)
        else:
            # Ollama (or any small-context local provider): tight cap.
            # 15 Tom msgs + 8 videos + 7 docs ≈ 30 sources × 700 chars
            # ≈ 5K tokens, safely inside 8K.
            k_tom = 15
        k_other = 0
    # Videos-only: zero out Discord budgets entirely. The whole context
    # budget shifts to video chunks.
    if videos_only:
        k_tom = 0
        k_other = 0
    # Discord-only doesn't touch the Tom/Other Discord budgets here —
    # those are what get USED in this mode. The video + doc budgets
    # get zeroed further down in the per-source loop.
    sort_order = (dbmod.get_setting(conn, "sources_sort_order", "newest")
                  or "newest").lower()

    # Budget: when 'Tom only' is on, give Tom the combined budget so the
    # user isn't silently penalised for switching mode.
    if tom_only:
        k_tom_eff, k_other_eff = k_tom + k_other, 0
    else:
        k_tom_eff, k_other_eff = k_tom, k_other

    # Over-fetch 3× so after we partition by author cohort we still have
    # enough per bucket to fill the budget. Can't filter at SQL level for
    # the semantic path (it's a numpy cosine over a cached matrix).
    over_sem = max((k_tom_eff + k_other_eff) * 3, 12)
    over_kw  = max((k_tom_eff + k_other_eff) * 3, 10)

    # ---- Discord: semantic ----------------------------------------------
    try:
        sem_msg = semantic.semantic_search(conn, question, limit=over_sem)
    except Exception as exc:
        log.warning("semantic_search unavailable (falling back to keyword): %s", exc)
        sem_msg = []
    sem_msg_ids_raw = [h.message_id for h in sem_msg if h.message_id]

    # ---- Discord: FTS5 keyword (OR-joined over extracted signal tokens) -
    kw_msg_ids_raw = searchmod.keyword_search_ids_broad(
        conn, question, limit=over_kw
    )

    # Merge preserving rank (semantic first, then keyword), dedupe.
    ranked_ids: list[str] = []
    seen_mid: set[str] = set()
    for mid in sem_msg_ids_raw + kw_msg_ids_raw:
        if mid and mid not in seen_mid:
            ranked_ids.append(mid)
            seen_mid.add(mid)

    # Tag each candidate by cohort so we can apply the budget per group.
    is_tom: dict[str, bool] = {}
    ts_of: dict[str, str] = {}
    if ranked_ids:
        placeholders = ",".join("?" * len(ranked_ids))
        rows = conn.execute(
            f"SELECT id, is_featured_speaker, timestamp "
            f"  FROM messages WHERE id IN ({placeholders})",
            ranked_ids,
        ).fetchall()
        for r in rows:
            is_tom[r["id"]] = bool(r["is_featured_speaker"])
            ts_of[r["id"]] = r["timestamp"] or ""

    # Walk in rank order, filling Tom and other budgets independently.
    tom_ids: list[str] = []
    other_ids: list[str] = []
    for mid in ranked_ids:
        if is_tom.get(mid, False):
            if len(tom_ids) < k_tom_eff:
                tom_ids.append(mid)
        else:
            if tom_only:
                continue
            if len(other_ids) < k_other_eff:
                other_ids.append(mid)
        if len(tom_ids) >= k_tom_eff and len(other_ids) >= k_other_eff:
            break

    discord_ids = tom_ids + other_ids

    # Apply sort toggle to the final list so both the LLM context AND the
    # sources panel render in the same order. Falls back to original
    # rank order if timestamps are missing.
    if discord_ids:
        reverse = (sort_order != "oldest")
        discord_ids = sorted(
            discord_ids,
            key=lambda m: ts_of.get(m, ""),
            reverse=reverse,
        )

    # ---- Docs + videos: semantic (mixed path, per-doc capped) ---------
    mixed_limit = 600 if deep else 200
    try:
        mixed = semantic.mixed_semantic_search(conn, question, limit=mixed_limit)
    except Exception as exc:
        log.warning("mixed_semantic_search unavailable (keyword-only docs): %s", exc)
        mixed = []
    sem_doc_page_ids: list[int] = []
    video_hits: list = []
    per_doc: dict[int, int] = {}
    per_video: dict[str, int] = {}
    if deep:
        primary_for_deep = (dbmod.get_setting(conn, "ai_provider_chat", "ollama")
                            or "ollama").lower()
        if primary_for_deep == "gemini":
            K_VIDEOS       = DEEP_K_VIDEOS
            PER_VIDEO_CAP  = DEEP_PER_VIDEO_CAP
            k_docs_sem_eff = DEEP_K_DOCS_SEM
            k_docs_kw_eff  = DEEP_K_DOCS_KW
            per_doc_cap    = DEEP_PER_DOC_CAP
        else:
            # Scaled-down Deep Dive for small-context Ollama.
            K_VIDEOS       = 8
            PER_VIDEO_CAP  = 1
            k_docs_sem_eff = 5
            k_docs_kw_eff  = 2
            per_doc_cap    = 2
    else:
        # Normal mode — bump videos from 4 to 6 so the LLM has more
        # spoken-teaching context available. Users strongly prefer
        # video citations (they're clickable timestamps) and a small
        # boost here meaningfully improves answer variety.
        K_VIDEOS       = 6
        PER_VIDEO_CAP  = 1
        k_docs_sem_eff = K_DOCS_SEM
        k_docs_kw_eff  = K_DOCS_KW
        per_doc_cap    = PER_DOC_CAP
    # In videos-only mode redirect the full context budget at videos
    # and disable doc retrieval. 24 chunks normal, 40 in deep mode —
    # enough to cover a topic across multiple videos without blowing
    # even llama3.1:8b's 8K context.
    if videos_only:
        K_VIDEOS = 40 if deep else 24
        PER_VIDEO_CAP = 3 if deep else 2
        k_docs_sem_eff = 0
        k_docs_kw_eff = 0
    # Discord-only: reroute the full context budget at Discord windows,
    # zero out video + doc retrieval. Symmetrical with videos_only.
    # Bump Tom's cohort generously; community stays at its configured
    # value so Tom's framing dominates but community context is still
    # represented for live-trading / chart-reading color.
    if discord_only:
        K_VIDEOS = 0
        PER_VIDEO_CAP = 0
        k_docs_sem_eff = 0
        k_docs_kw_eff = 0
        k_tom = max(k_tom, 15 if not deep else 30)
    for h in mixed:
        if h.source_type == "doc_page":
            # Skip all doc_page hits entirely in videos-only mode AND
            # respect the semantic doc cap so the outer break logic
            # works correctly at k_docs_sem_eff==0.
            if k_docs_sem_eff <= 0:
                continue
            if len(sem_doc_page_ids) >= k_docs_sem_eff:
                continue
            did = h.doc_page.document_id
            if per_doc.get(did, 0) >= per_doc_cap:
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
        if len(sem_doc_page_ids) >= k_docs_sem_eff and len(video_hits) >= K_VIDEOS:
            break

    # ---- Docs: keyword overlap (surfaces rare literal terms) -----------
    kw_doc_page_ids = searchmod.keyword_search_doc_page_ids(
        conn, question, limit=k_docs_kw_eff * 3
    )
    # Respect per_doc_cap here too — need to know which document each page
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
        if per_doc.get(did, 0) >= per_doc_cap:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        kept_kw_doc.append(pid)
        if len(kept_kw_doc) >= k_docs_kw_eff:
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

    # NOTE: we intentionally do NOT truncate ``out`` to K_TOTAL here.
    # Each source type is already capped by its own budget
    # (k_tom + k_other for Discord, K_VIDEOS for videos, K_DOCS_SEM +
    # K_DOCS_KW for docs). A global cap applied after messages were
    # appended first meant that setting Tom:19 would fill all 14 slots
    # with messages and silently drop every video + PDF from the answer.
    # Modern chat models comfortably accept ~30 sources × 700 chars
    # of context, so the per-category caps are the only governor we need.
    return out


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


def build_user_prompt(
    question: str,
    sources: list[RetrievedSource],
    sort_order: str = "newest",
) -> str:
    if (sort_order or "").lower() == "oldest":
        order_hint = (
            "The Discord posts above are listed OLDEST-first. When you walk "
            "through Tom's evolution on this topic, present and cite the "
            "evidence in that same chronological order — earlier dates "
            "before later dates. Do NOT lead with the most recent post."
        )
    else:
        order_hint = (
            "The Discord posts above are listed NEWEST-first. Present and "
            "cite the evidence in that same order — most recent first, "
            "older context after."
        )
    return (
        "Retrieved sources:\n\n"
        f"{format_context(sources)}\n\n"
        "==========\n\n"
        f"Question: {question}\n\n"
        "Answer using only the retrieved sources. Cite each claim. "
        f"{order_hint}"
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
    *,
    deep: bool = False,
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
    sources = retrieve(conn, retrieval_q, deep=deep)

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
    sort_order = (dbmod.get_setting(conn, "sources_sort_order", "newest")
                  or "newest").lower()
    messages.append({
        "role": "user",
        "content": build_user_prompt(prompt_q, sources, sort_order),
    })

    # 3) call provider (with automatic fallback chain)
    provider = registry.get_chat_provider(conn)
    image_paths = images or None
    system = build_system_prompt(conn, deep=deep)

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

    # Belt-and-suspenders: some LLMs (llama3.1:8b especially) strip the
    # kind prefix when all sources are of a single type — e.g. writes
    # "[10102]" instead of "[vid:10102]" when Videos-only mode filtered
    # the context. Detect bare-numeric bracketed citations and remap to
    # the only kind that's plausible given the retrieved sources.
    answer = _patch_bare_citations(answer or "", sources)

    citations = [
        f"{m.group(1).lower()}:{m.group(2)}"
        for m in CITATION_RE.finditer(answer or "")
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


# ---------------------------------------------------------------------------
# answer post-processing
# ---------------------------------------------------------------------------
_BARE_CITATION_RE = re.compile(r"\[(\d+)\]")


def _patch_bare_citations(
    answer: str, sources: list["RetrievedSource"]
) -> str:
    """Repair `[1234]` → `[vid:1234]` (or `[doc:1234]`) when the LLM
    dropped the kind prefix.

    Strategy: build a set of ids per kind from the retrieved sources.
    For each bare `[NNN]` in the answer, if NNN matches exactly one
    kind, substitute in that prefix. If it's ambiguous (same id
    appears in multiple kinds) or matches none, leave untouched.
    """
    if not answer or "[" not in answer:
        return answer
    ids_by_kind: dict[str, set[str]] = {"msg": set(), "doc": set(), "vid": set()}
    for s in sources:
        # citation_id is "kind:raw"; split it.
        if ":" not in s.citation_id:
            continue
        kind, raw = s.citation_id.split(":", 1)
        if kind in ids_by_kind:
            ids_by_kind[kind].add(raw)

    def _repl(m: re.Match) -> str:
        raw = m.group(1)
        matching = [k for k, ids in ids_by_kind.items() if raw in ids]
        if len(matching) == 1:
            return f"[{matching[0]}:{raw}]"
        return m.group(0)

    return _BARE_CITATION_RE.sub(_repl, answer)
