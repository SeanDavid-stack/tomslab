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

from tomslab import db as dbmod, search as searchmod, semantic, spelling
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


SYSTEM_PROMPT = """\
You are Tom's Lab — an expert study assistant for trader Tom B's teachings on
Bookmap order flow, volume profile, and auction market theory.

How to answer:
- Synthesize a helpful answer from the retrieved sources. Tom's Discord posts
  are often fragmentary ("no NVPOC there", "watch IBH for absorption") —
  connect them into a coherent explanation using Tom's glossary and PDFs.
- Cite every substantive claim inline using the exact tag next to the source
  header, e.g. [msg:916502712684793916] for Discord, [doc:42] for a PDF page.
  Prefer multiple citations when you're stitching partial evidence.
- Prefer Tom's own words over third-party references when both are present.
- If the retrieved sources only touch the topic indirectly, still answer using
  the closest relevant context and say which part is inferred. Only refuse
  ("The sources don't cover this") when there is truly nothing relevant.
- Keep answers focused. Short paragraphs or bullets. No "Certainly!" preambles.
"""


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


CITATION_RE = re.compile(r"\[(msg|doc):([\w:\-]+)\]")


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
    """
    # ---- Discord: semantic ----------------------------------------------
    sem_msg = semantic.semantic_search(conn, question, limit=K_DISCORD_SEM * 2)
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

    # ---- Docs: semantic (existing mixed path, per-doc capped) -----------
    mixed = semantic.mixed_semantic_search(conn, question, limit=200)
    sem_doc_page_ids: list[int] = []
    per_doc: dict[int, int] = {}
    for h in mixed:
        if h.source_type != "doc_page":
            continue
        did = h.doc_page.document_id
        if per_doc.get(did, 0) >= PER_DOC_CAP:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        sem_doc_page_ids.append(h.doc_page.page_id)
        if len(sem_doc_page_ids) >= K_DOCS_SEM:
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
) -> AnswerResult:
    """Run one chat turn.

    If ``auto_correct`` is True the function transparently fixes typos
    before retrieval.  When the UI wants to show a "Did you mean" banner
    it should call :func:`preview_corrections` itself and pass the final
    (user-approved) question with ``auto_correct=False``.
    """
    history = history or []

    corrected_q = ""
    corrections: list[tuple[str, str]] = []
    if auto_correct:
        corrected_q, corrections = spelling.correct_query(
            question, str(database_path())
        )
    retrieval_q = corrected_q if corrections else question

    # 1) retrieve
    sources = retrieve(conn, retrieval_q)

    # 2) build message list
    messages: list[dict] = []
    for t in history:
        messages.append({"role": t.role, "content": t.content})
    # Use the corrected question inside the prompt so the model answers the
    # intended question, not the typo.
    prompt_q = retrieval_q
    messages.append({"role": "user", "content": build_user_prompt(prompt_q, sources)})

    # 3) call provider
    provider = registry.get_chat_provider(conn)
    try:
        answer = provider.chat(messages, system=SYSTEM_PROMPT)
    except (ProviderError, ProviderUnavailable) as exc:
        raise RuntimeError(f"Chat provider error: {exc}") from exc

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
    )
