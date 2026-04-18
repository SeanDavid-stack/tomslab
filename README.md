# Tom's Lab

> Desktop app that turns the Bookmap Discord `traders-lab-tom-b` channel into a
> smart, searchable study tool. AI-powered keyword / semantic / visual / chat
> search over Tom B's teachings on market mechanics, order flow, and volume profile.

See [`toms_lab_prd.md`](toms_lab_prd.md) for the full product spec.

---

## Status

**Phase 5 — Ask Tom conversational RAG** ✅
- New "Ask Tom" tab with a chat interface. Each turn pulls the top
  K Discord windows **and** the top K PDF pages independently (per-doc
  cap so one long PDF can't monopolise), assembles them into a
  grounded prompt, and calls Gemini 2.5 Flash.
- Tom-authored PDFs get a strong score boost so definitional queries
  surface the authored source before Discord chatter or third-party
  books.
- Answers render as styled cards with clickable `[msg:...]` and
  `[doc:...]` citations. Clicking a message citation jumps to it in
  the Feed; clicking a doc citation pops up the PDF page content.
- Multi-turn — history is kept per session and prepended to each call.

**Phase 4.6 — Concept extraction + doc-page CLIP** ✅
- Parses Tom's Glossary PDF with Gemini and seeds the `concepts` table
  with the 26 definitions Tom uses (RTH, VPOC, VA, HVN/LVN, VWAP,
  Naked VPOC, IB, Mean Reversion, ...). Idempotent INSERT OR IGNORE.
- CLIP-embeds every rendered PDF page alongside chart attachments so
  Visual mode / Gallery also find Tom's diagrams, not just Discord
  screenshots.

**Phase 4.5 — Reference PDF ingest** ✅
- Drops Tom's authored PDFs (glossary, 60 Structured Trades, Market Structure,
  Opening Context Alignment, Bookmap Settings, ...) and third-party references
  (Best Loser Wins, Trade Your Way) into a first-class corpus
- PDFs → per-page PNG render (pypdfium2, 150 dpi)
- Extracted text via pdfplumber; EasyOCR on GPU for image-only pages
  (2–3 s/page). Tried LLaVA first — it hallucinated meta-commentary rather
  than transcribing. Tried Gemini Vision — fine on single pages but free-tier
  RPM makes bulk runs impractical. EasyOCR is fast, local, and actually
  transcribes.
- Every doc page gets text-embedded via Ollama alongside Discord windows
- Semantic search now returns a merged `message + doc_page` feed; Tom's PDFs
  get a small score boost so definitional queries surface the authored source
  before Discord chatter
- Doc hits render as blue-accented 📄 cards with the rendered page inline;
  Feed shares the same scroll as Discord messages

Benchmark: 10 PDFs / 591 pages ingested in 6 min (128 OCR calls +
pdfplumber extracts elsewhere); all 591 pages text-embedded via Ollama
in 10.7 s.

**Phase 4 — Visual search + chart gallery** ✅
- CLIP (open_clip, ViT-B-32/openai) joint image-text embeddings, stored as
  raw float32 bytes in `image_embeddings`
- Extension-filtered pipeline (images only — skips audio/video/pdf/docx
  attachments that DCE also captures)
- Visual mode in the search combo and a dedicated Gallery tab with a grid
  of thumbnails; clicking a chart jumps to its message in the Feed
- Runs on the 3080 Ti (CUDA) with graceful CPU fallback
- Numpy cosine over a cached in-memory matrix — under 100 ms per query
  after the first cache warmup

Benchmark: 10,098 chart attachments embedded in ~6.4 min total (27 imgs/s,
512 dim, `ViT-B-32:openai`) on the 3080 Ti.

**Phase 3 — AI provider layer + semantic search** ✅
- Clean `AIProvider` abstraction with `Ollama` (local, default for embed/vision)
  and `Gemini` (cloud, default for chat) implementations
- Provider registry maps role → provider from user settings
- Settings dialog lets the user change assignments, paste a Gemini API key
  (stored XOR-masked in SQLite), and "Test connection" per provider
- Batch-embedding pipeline (resumable, runs off the UI thread)
- Semantic search over `conversation_windows` using numpy cosine over an
  in-memory normalised matrix — lazily loaded and cached
- Mode combo (Keyword / Semantic / Visual-disabled) + Build-embeddings menu

Benchmark: all 17,909 windows embedded in ~3 min via Ollama `nomic-embed-text`
on a 3080 Ti (768 dim, 101 texts/s avg). Post-cache warmup, semantic
queries return in <200 ms.

**Phase 2 — Keyword search + Discord UI** ✅
- FTS5 full-text index over message content, author name, nickname
- Search bar with debounced query, prefix matching, quoted phrases
- Custom-painted message cards: gold accent for Tom, inline chart thumbnails,
  reply previews (↪ replying to @user), in-body match highlighting
- Dark Discord-inspired palette
- 7,350 VPOC hits / 150 absorption hits / etc. indexed in ~2 s backfill

**Phase 1 — Ingestion pipeline** ✅
- Streaming DCE JSON parser (400 MB+ exports in O(1) memory)
- SQLite schema from PRD §6, at `%APPDATA%/TomsLab/data/tomslab.db`
- Idempotent import (re-running skips existing message IDs)
- Conversation windows built around every featured-speaker message
- Benchmark: 195,300-message / 429 MB import in ~27 s on a Ryzen 9 5900X

**Phase 0 — Repo scaffolding** ✅

See the 9-phase build plan in PRD §12.

## Quick start (developer)

Requires Python 3.11+ on Windows 10+.

```bash
# From the repo root
python -m venv .venv
.venv\Scripts\activate
pip install -e .
tomslab
```

Or, without installing:

```bash
pip install -r requirements.txt
python -m tomslab
```

A window titled "Tom's Lab v0.1.0" should appear.

## Project layout

```
Toms Lab/
├── src/tomslab/
│   ├── __init__.py
│   ├── __main__.py       # python -m tomslab
│   ├── main.py           # Qt entry point
│   ├── paths.py          # %APPDATA%/TomsLab layout
│   ├── db.py             # SQLite schema + connection
│   ├── search.py         # FTS5 query builder
│   ├── semantic.py       # Text cosine search (numpy) — messages + doc pages
│   ├── visual.py         # CLIP joint image/text embeddings + search
│   ├── chat.py           # Ask Tom retrieval + prompt + Gemini call
│   ├── concepts.py       # Glossary-PDF → concepts table seeder
│   ├── embed_service.py  # Text embedding pipeline (windows + doc pages)
│   ├── image_embed_service.py  # CLIP pipeline (charts + doc pages)
│   ├── secret_store.py   # XOR-masked API key storage
│   ├── docs/
│   │   ├── pdf_render.py # pypdfium2 → PNG
│   │   ├── ocr.py        # EasyOCR (default) / LLaVA / Gemini OCR
│   │   └── importer.py   # Document ingest orchestrator
│   ├── ai/
│   │   ├── base.py       # AIProvider abstract interface
│   │   ├── ollama_provider.py
│   │   ├── gemini.py
│   │   └── registry.py   # Role → provider mapping
│   ├── ingest/
│   │   ├── dce.py        # Streaming DCE JSON parser
│   │   ├── importer.py
│   │   └── windows.py    # Conversation-window builder
│   └── ui/
│       ├── main_window.py
│       ├── message_model.py
│       ├── message_delegate.py   # Discord-style message cards
│       ├── gallery_view.py       # CLIP-backed chart grid
│       ├── chat_view.py          # Ask Tom chat UI
│       ├── chat_worker.py
│       ├── settings_dialog.py    # AI Providers settings
│       ├── import_worker.py
│       ├── embed_worker.py
│       └── image_embed_worker.py
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── LICENSE
└── toms_lab_prd.md       # Product spec (v1.0)
```

### Runtime data location

All user data lives under `%APPDATA%/TomsLab/` (never in the repo):

```
%APPDATA%/TomsLab/
├── data/
│   ├── tomslab.db           # SQLite (messages, attachments, windows, ...)
│   └── _assets/             # (Phase 6) local chart copies
└── logs/tomslab.log
```

## Roadmap (per PRD §12)

| Phase | Deliverable |
|------:|-------------|
| 0 | Repo + Hello window |
| 1 | DCE JSON → SQLite ingestion + message list UI |
| 2 | Keyword search + Discord-style feed |
| 3 | AI provider abstraction + semantic search |
| 4 | Visual / CLIP search + chart gallery ← **current** |
| 5 | "Ask Tom" conversational RAG |
| 6 | Vision chart descriptions + concepts + dedup |
| 7 | Bookmarks + first-run wizard |
| 8 | PyInstaller + Inno Setup installer for handoff |

## License

MIT. See [LICENSE](LICENSE).
