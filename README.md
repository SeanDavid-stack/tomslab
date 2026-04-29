# Tom's Lab

Free desktop **library / searchable encyclopedia** of Tom B's
publicly-shared teaching from the Bookmap Discord
`traders-lab-tom-b` channel, his published reference PDFs, and his
public YouTube uploads. Keyword, semantic, visual (chart-image),
and conversational retrieval over a single local index. **Not** a
trading tool, signals service, or strategy generator — a study
reference.

Published by **SDE-Software (SDES.DEV)**.

- **Users:** see [`USER_MANUAL.md`](USER_MANUAL.md) for installation
  and operation.
- **Developers:** product spec at [`toms_lab_prd.md`](toms_lab_prd.md);
  build notes in this README.

> **Tom B has not reviewed or endorsed this tool.** Tom's Lab is an
> independent third-party project. The disclaimers shown in-app on
> first launch are the binding terms; this README is summary.

---

## System requirements

| Component | Status | Notes |
|---|---|---|
| OS | Required | Windows 10 or 11, 64-bit. Mac and Linux are unsupported. |
| CPU | Required | 4-core x86-64. Embedding and classification are CPU-heavy. |
| RAM | Required | 16 GB minimum. 32 GB recommended if you transcribe YouTube audio. |
| Disk | Required | **~40 GB free.** App ≈ 5 GB; data pack download ≈ 10.5 GB; unpacks to ~25-30 GB on disk. |
| [Ollama](https://ollama.com/download/windows) | Required | Free local AI used for query embedding. Ask Tom cannot embed questions without it. |
| [Gemini API key](https://aistudio.google.com/apikey) | Recommended | Higher-quality Ask Tom answers than the Ollama fallback. Free tier is sufficient. |
| [NVIDIA GPU + driver](https://www.nvidia.com/download/index.aspx) | Optional | The app runs on CPU. How much you miss the GPU depends on what you do — see below. |

**How much does the GPU matter?**

- *Searching the shipped data pack + Ask Tom via Gemini:* barely. Sub-second on CPU once embeddings load.
- *Re-embedding your own Discord export, or re-running PDF OCR:* moderately slower — minutes become tens of minutes.
- *Transcribing YouTube videos with Whisper:* **much, much slower.** A 1-hour video is ~10–30 min on a modern NVIDIA GPU vs **~1–3 hours on CPU**.
- *Local Ask Tom fallback (no Gemini key, using Ollama Llama 3.1 8B):* big difference — slow trickle on CPU, near-instant on GPU.

Faster disks help most during data-pack install: NVMe SSD ≈ 5–8 min, SATA SSD ≈ 8–15 min, mechanical HDD ≈ 25–45 min. Full operational detail is in [`USER_MANUAL.md`](USER_MANUAL.md) §2–3.

---

## Download

**Both installers and the data pack are hosted on Google Drive — not on GitHub.** GitHub Releases caps individual assets at 2 GB.

👉 **[Tom's Lab — Google Drive folder](https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW)**

The folder contains:

**Installer (pick one):**

- **`TomsLab-Setup-1.0.2.exe`** — Standard installer (**225 MB**). **Pick this one unless you specifically need GPU acceleration.**
- `TomsLab-Setup-1.0.2-GPU.exe` — GPU installer (~2.1 GB) with NVIDIA CUDA libraries bundled. Pick this if you have an NVIDIA card and plan to transcribe YouTube videos or do heavy visual searches.

**Data pack (pick one):**

- `tomslab-data-2026-04-27.tar.zst` — **Full pack** (**7.66 GB**). Tom + community Discord history (587K messages), Tom's PDFs, Tom's YouTube transcripts.
- `tomslab-data-tom-only-2026-04-27.tar.zst` — **Tom-only pack** (**725 MB**). Just Tom's own messages and his attached charts (89,748 messages). Tom's PDFs and YouTube transcripts still included. Drops community context — replies to non-Tom users will show "(message not in pack)".

When Drive warns it can't scan a file for viruses, choose **Download anyway** — that warning appears on every file over ~100 MB. After downloading the installer + data pack, run the installer first, then use **File → Install data pack…** inside the app to load the `.tar.zst`. Step-by-step in [`USER_MANUAL.md`](USER_MANUAL.md) §3.

---

## Policy

Tom's Lab is a free, as-is utility. By installing or using the
program, the user agrees to the following terms:

- **Free tool, no warranty.** The software is provided without
  warranty of any kind. There is no purchase, no licence fee, and no
  service contract.
- **Self-service only.** No one-on-one support, walkthroughs,
  troubleshooting calls, or individual installation assistance is
  provided. Everything required to operate the program is documented
  in [`USER_MANUAL.md`](USER_MANUAL.md). Questions not answered there
  are out of scope.
- **User responsibility.** Installing the application, optional
  components (Ollama, Gemini API key, GPU drivers), obtaining and
  importing corpus material (Discord exports, YouTube content,
  reference PDFs), and respecting the Terms of Service of the source
  platforms are the user's responsibility.
- **Discretionary updates.** Bug fixes and feature updates may be
  released on an occasional, discretionary basis. No schedule is
  guaranteed and no commitment to fix, respond to, or acknowledge
  individual reports is made.
- **Independent third party.** Tom's Lab is not affiliated with,
  endorsed by, sponsored by, or connected to Tom B, Bookmap Ltd., the
  Bookmap Discord, Discord Inc., Google, or any other third party
  referenced elsewhere in the program.
- **Not financial advice.** Tom's Lab is an experimental research
  tool. All trading decisions, and any resulting gains or losses, are
  the user's alone.

This is the same support, warranty, and liability model used by
SDE-Software's **BMBridge Lite**.

---

## Status

**v1.0.2 — slim release**. Ships as two parallel Inno Setup installers
on the project's [Google Drive folder](https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW):
a Standard installer (`TomsLab-Setup-1.0.2.exe`, ~700 MB, CPU PyTorch)
and a GPU installer (`TomsLab-Setup-1.0.2-GPU.exe`, ~2.1 GB, CUDA
libraries bundled). Not Authenticode-signed; SmartScreen will warn on
first launch — see [`USER_MANUAL.md`](USER_MANUAL.md) §3.1.

### What's new in v1.0.2

- **Two installer flavors.** v1.0.0 and v1.0.1 bundled the full NVIDIA
  CUDA driver stack into every download — about 3 GB of libraries that
  most users never used because they had no NVIDIA card. v1.0.2 splits
  that: a small CPU-only installer becomes the default, and a separate
  GPU installer carries the CUDA bits for users who want them. **Most
  users save roughly 75% on installer size.**
- **Smaller data pack.** Chart images are now downscaled to 1600 px
  wide before WebP re-encoding. CLIP downscales to 224×224 internally
  at search time, so search quality is unchanged but the data pack
  is meaningfully smaller. Existing v1.0.1 data packs continue to
  work — re-download is optional.

### What was new in v1.0.1

- **Groq added as a third chat provider** alongside Gemini and Ollama.
  Chat-only. Free-tier daily request limit is roughly 10× Gemini's
  1,500/day, and LPU streaming is much faster — at the cost of
  weaker citation discipline. Settings → AI Providers picks it up.
- **Update notifications now actually fire.** v1.0.0 shipped pointing
  at a JSON manifest URL that was never published, so no installed
  v1.0.0 client could ever detect a newer release. The check now
  queries the GitHub Releases API directly. Existing v1.0.0 installs
  are silently migrated on first launch.
- Documentation: README and the user manual have a System
  Requirements section + a Download section with the Drive folder
  link prominently placed; release notes no longer point at empty
  GitHub Assets.

### v1.0.0 — first public release

- **Show in timeline** for any Discord citation now reaches messages
  outside the recent-history window — older posts load via a
  windowed view around the target.
- **Sources sort toggle** in Ask Tom now reorders the answer body
  (oldest-first walks Tom's evolution chronologically), not just
  the source strip beneath it.
- Top search bar (Keyword / Semantic / Visual) hides on tabs that
  don't use it (Ask Tom, Docs, TomTube, Bookmarks).
- Trimmed Ask-Tom sample prompts to questions grounded in Tom's
  authored PDFs.
- Cosmetic Feed/Ask-Tom polish: hover tooltip removed, "★ Tom"
  pill no longer clips its descender, gallery search is faster on
  broad queries.

### Build history

*Phase notes below are kept as a development changelog — the benchmark numbers cited in each phase reflect the corpus size at that point in time. Current corpus statistics are documented in [`demo/05_what_is_toms_lab.md`](demo/05_what_is_toms_lab.md).*

**Phase 8 — PyInstaller + Inno Setup installer** ✅ (this release)

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

A window titled "Tom's Lab v1.0.1" should appear.

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

## Licence

Proprietary. Published by SDE-Software (SDES.DEV) as a free
personal-use utility. Not open source. Redistribution, public
hosting, commercial use, modification, and reverse engineering are
prohibited without prior written consent. See [LICENSE](LICENSE)
for the full terms.
