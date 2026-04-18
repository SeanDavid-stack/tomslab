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

from tomslab import db as dbmod, semantic
from tomslab.ai import registry
from tomslab.ai.base import ProviderError, ProviderUnavailable

log = logging.getLogger(__name__)


K_TOTAL = 12          # max retrieved contexts pulled per question
K_DISCORD = 7         # of which we want at least this many from Discord
K_DOCS = 5            # and this many from Tom's PDFs, when possible
PER_DOC_CAP = 1       # 1 page per document in the top-K doc slice so a single
                      # long PDF (e.g. Market Structure's 72 pages) can't crowd
                      # out shorter authored docs
CONTEXT_CHAR_CAP = 700  # per retrieved snippet


SYSTEM_PROMPT = """\
You are Tom's Lab — an expert study assistant for trader Tom B's teachings on
Bookmap order flow, volume profile, and auction market theory.

Rules you MUST follow:
- Ground every factual claim in the retrieved sources listed below.
- Cite inline using the exact tag next to the source header, e.g. [msg:916502712684793916] for Discord messages and [doc:42] for PDF pages. Use multiple citations when appropriate.
- If the sources don't answer the question, say so plainly — do NOT invent or rely on outside knowledge about trading.
- Prefer Tom's own words over third-party references when both are present.
- Keep answers focused and concise. Use short paragraphs or bullet points. No preambles like "Certainly!" or "Based on the sources…".
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


CITATION_RE = re.compile(r"\[(msg|doc):([\w:\-]+)\]")


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------
def retrieve(conn: sqlite3.Connection, question: str) -> list[RetrievedSource]:
    # Pull each source type independently so a strong boost for one type
    # can't starve the other. We still merge on display order by score.
    msg_hits_only = semantic.semantic_search(conn, question, limit=K_DISCORD * 2)
    mixed = semantic.mixed_semantic_search(conn, question, limit=200)

    discord = [h for h in msg_hits_only][:K_DISCORD]

    # Take doc hits with a per-document cap so one long PDF doesn't
    # dominate the context window.
    docs: list = []
    per_doc: dict[int, int] = {}
    for h in mixed:
        if h.source_type != "doc_page":
            continue
        did = h.doc_page.document_id
        if per_doc.get(did, 0) >= PER_DOC_CAP:
            continue
        per_doc[did] = per_doc.get(did, 0) + 1
        docs.append(h)
        if len(docs) >= K_DOCS:
            break

    out: list[RetrievedSource] = []

    # Fetch message bodies for discord hits. semantic.semantic_search returns
    # SemanticHit objects whose message_id is the primary identifier.
    if discord:
        ids = [h.message_id for h in discord if getattr(h, "message_id", None)]
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
            for h in discord:
                mid = getattr(h, "message_id", None)
                if not mid:
                    continue
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
                    score=h.score,
                    message_id=r["id"],
                ))

    # Fetch doc page text for doc hits
    for h in docs:
        d = h.doc_page
        if d is None:
            continue
        row = conn.execute(
            "SELECT COALESCE(NULLIF(ocr_text,''), extracted_text) AS t "
            "FROM document_pages WHERE id = ?",
            (d.page_id,),
        ).fetchone()
        if not row:
            continue
        text = (row["t"] or "").strip()
        if not text:
            continue
        if len(text) > CONTEXT_CHAR_CAP:
            text = text[: CONTEXT_CHAR_CAP - 1].rstrip() + "…"
        out.append(RetrievedSource(
            kind="doc_page",
            citation_id=f"doc:{d.page_id}",
            author=d.title or d.filename,
            when=f"page {d.page_num}",
            text=text,
            score=h.score,
            doc_page_id=d.page_id,
            doc_title=d.title,
            doc_page_num=d.page_num,
        ))
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
def ask(
    conn: sqlite3.Connection,
    question: str,
    history: list[ChatTurn] | None = None,
) -> AnswerResult:
    history = history or []

    # 1) retrieve
    sources = retrieve(conn, question)

    # 2) build message list
    messages: list[dict] = []
    for t in history:
        messages.append({"role": t.role, "content": t.content})
    messages.append({"role": "user", "content": build_user_prompt(question, sources)})

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
    )
