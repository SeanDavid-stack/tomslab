"""Seed the ``concepts`` table from Tom's Glossary PDF.

Pulls the glossary document's OCR text, asks Gemini to parse it into a
JSON list of ``{term, abbreviation, definition}`` objects, and inserts
one row per term. INSERT OR IGNORE by name — re-running never duplicates.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from tomslab import secret_store

log = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None
    genai_types = None


GLOSSARY_FILENAMES = ("Trader_Lab_Glossary.pdf",)


PARSE_PROMPT = """\
Below is the OCR'd text of a trader's glossary page. Parse it into a JSON array
of objects, one per defined term. Each object must have exactly these keys:

  "term"         : the full-name term (string, e.g. "Initial Balance High")
  "abbreviation" : the ticker / short form if one is shown (string, may be "")
  "definition"   : the definition text (string). Strip bullet prefixes. Keep
                   succinct; 1-3 sentences max.

Output ONLY the JSON array, nothing else — no preamble, no code fences, no
explanation. If you can't parse the text, output [].

OCR TEXT:
```
{text}
```
"""


def load_glossary_text(conn: sqlite3.Connection) -> str:
    """Concatenate OCR + extracted text from every page of the Glossary doc(s)."""
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(p.ocr_text,''), p.extracted_text) AS text
          FROM document_pages p
          JOIN documents d ON d.id = p.document_id
         WHERE d.filename IN ({','.join('?' * len(GLOSSARY_FILENAMES))})
         ORDER BY d.id, p.page_num
        """,
        GLOSSARY_FILENAMES,
    ).fetchall()
    return "\n".join((r["text"] or "").strip() for r in rows if r["text"])


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    # some models wrap in ```json...``` despite instructions
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, flags=re.DOTALL)
    if m:
        return m.group(1)
    return text


def parse_glossary_with_gemini(
    conn: sqlite3.Connection, model: str = "gemini-2.5-flash"
) -> list[dict]:
    if genai is None:
        raise RuntimeError("google-genai not installed")
    api_key = secret_store.load_api_key(conn, "gemini")
    if not api_key:
        raise RuntimeError("no Gemini API key configured — see Settings → AI Providers")

    text = load_glossary_text(conn)
    if not text.strip():
        return []

    client = genai.Client(api_key=api_key)
    r = client.models.generate_content(
        model=model,
        contents=PARSE_PROMPT.format(text=text),
    )
    raw = _strip_json_fence(r.text or "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Last-ditch: try to find the first JSON array in the response
        m = re.search(r"\[.*\]", raw, flags=re.DOTALL)
        if not m:
            log.warning("could not parse Gemini glossary output; raw=%r", raw[:200])
            return []
        data = json.loads(m.group(0))

    out = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            if not term:
                continue
            abbr = str(item.get("abbreviation", "") or "").strip()
            defn = str(item.get("definition", "") or "").strip()
            out.append({"term": term, "abbreviation": abbr, "definition": defn})
    return out


def seed_concepts(conn: sqlite3.Connection) -> int:
    """Parse the Glossary PDF and insert terms into concepts. Idempotent.

    Returns the number of *new* rows inserted (existing terms untouched).
    """
    parsed = parse_glossary_with_gemini(conn)
    if not parsed:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for item in parsed:
        name = item["term"]
        desc_parts: list[str] = []
        if item.get("abbreviation"):
            desc_parts.append(f"({item['abbreviation']})")
        if item.get("definition"):
            desc_parts.append(item["definition"])
        description = " ".join(desc_parts)
        rows.append((name, description, now))

    # pre-count to know how many are new
    existing = {r["name"] for r in conn.execute("SELECT name FROM concepts")}
    new_rows = [r for r in rows if r[0] not in existing]

    conn.executemany(
        "INSERT OR IGNORE INTO concepts(name, description, extracted_at) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(new_rows)
