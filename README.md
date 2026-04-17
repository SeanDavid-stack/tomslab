# Tom's Lab

> Desktop app that turns the Bookmap Discord `traders-lab-tom-b` channel into a
> smart, searchable study tool. AI-powered keyword / semantic / visual / chat
> search over Tom B's teachings on market mechanics, order flow, and volume profile.

See [`toms_lab_prd.md`](toms_lab_prd.md) for the full product spec.

---

## Status

**Phase 1 — Ingestion pipeline** ✅
- Streaming DCE JSON parser (handles 400 MB+ exports in O(1) memory)
- SQLite schema from PRD §6, stored at `%APPDATA%/TomsLab/data/tomslab.db`
- Messages + attachments import, idempotent (re-importing skips existing message IDs)
- Conversation windows built around every featured-speaker message
- Main window with File → Import (Ctrl+I), drag-and-drop for `.json` files,
  paginated message list (newest first, 500 per page, up to 10,000 shown)

Benchmark: full 195,300-message / 429 MB Bookmap export imports in ~27 s on
a Ryzen 9 5900X.

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
│   ├── ingest/
│   │   ├── dce.py        # Streaming DiscordChatExporter JSON parser
│   │   ├── importer.py   # Orchestrates import run
│   │   └── windows.py    # Builds conversation windows
│   └── ui/
│       ├── main_window.py
│       ├── message_model.py
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
| 1 | DCE JSON → SQLite ingestion + message list UI ← **current** |
| 2 | Keyword search + Discord-style feed |
| 3 | AI provider abstraction + semantic search |
| 4 | Visual / CLIP search + chart gallery |
| 5 | "Ask Tom" conversational RAG |
| 6 | Vision chart descriptions + concepts + dedup |
| 7 | Bookmarks + first-run wizard |
| 8 | PyInstaller + Inno Setup installer for handoff |

## License

MIT. See [LICENSE](LICENSE).
