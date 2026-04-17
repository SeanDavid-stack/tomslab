# Tom's Lab

> Desktop app that turns the Bookmap Discord `traders-lab-tom-b` channel into a
> smart, searchable study tool. AI-powered keyword / semantic / visual / chat
> search over Tom B's teachings on market mechanics, order flow, and volume profile.

See [`toms_lab_prd.md`](toms_lab_prd.md) for the full product spec.

---

## Status

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
│   ├── search.py         # FTS5 query builder + result retrieval
│   ├── ingest/
│   │   ├── dce.py        # Streaming DiscordChatExporter JSON parser
│   │   ├── importer.py   # Orchestrates import run
│   │   └── windows.py    # Builds conversation windows
│   └── ui/
│       ├── main_window.py
│       ├── message_model.py
│       ├── message_delegate.py   # Discord-style message cards
│       └── import_worker.py
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
| 2 | Keyword search + Discord-style feed ← **current** |
| 3 | AI provider abstraction + semantic search |
| 4 | Visual / CLIP search + chart gallery |
| 5 | "Ask Tom" conversational RAG |
| 6 | Vision chart descriptions + concepts + dedup |
| 7 | Bookmarks + first-run wizard |
| 8 | PyInstaller + Inno Setup installer for handoff |

## License

MIT. See [LICENSE](LICENSE).
