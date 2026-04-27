# Tom's Lab v1.0.0 — first public release

A free desktop **library / searchable encyclopedia** of Tom B's publicly-shared trading material. **Not a course, not a trading tool, not endorsed by Tom.** A research aid for going deeper into Tom's existing Discord posts, reference PDFs, and public YouTube videos.

Published by **SDE-Software (SDES.DEV)**.

---

## ⚠️ Read before installing

- **Tom B has not reviewed or endorsed this app.** This is an independent third-party project from SDE-Software. Tom is not affiliated with it and will not support it.
- **Not affiliated** with Tom B, Bookmap Ltd., the Bookmap Discord moderators, or Discord Inc. Do not contact any of them about this app.
- **The installer is NOT digitally signed and never will be.** Windows SmartScreen will warn on first launch — you click "More info → Run anyway" at your own risk. By installing, you accept that you are running unsigned software from an unknown publisher and that all consequences are yours.
- **Free, no support.** SDE-Software does not provide one-on-one help, troubleshooting, or installation assistance. The bundled `USER_MANUAL.md` is the entire support layer.
- **Bulk-exporting Discord messages and bulk-downloading YouTube content may violate those platforms' Terms of Service.** Importing your own export and your own video downloads is your decision and your responsibility.
- **AI answers can be wrong. Verify everything against the original source before relying on it.** This is an experimental research tool, not financial advice. All trading decisions are yours.

If any of the above isn't acceptable to you, please **don't install it.**

---

## What's indexed in the shipped data pack

Counts verified against the production database:

- **587,469 Discord messages** (December 3, 2021 → April 18, 2026) — 89,748 from Tom B, 497,721 community
- **413 of Tom's YouTube videos** transcribed locally (29,862 ~90-second transcript chunks)
- **8 of Tom's reference PDFs** (Auction Market Theory 101, Market Structure, Mean Reversion Structured Trade, Opening Context Alignment, Stats by Target, 60 Structured Trades, Bookmap Settings, Trader Lab Glossary), plus 1 Linnsoft community thread and 2 third-party trading books
- **36,770 chart attachments** with CLIP-powered visual search
- **26 Tom-glossary concepts** seeded from his Glossary PDF

---

## Install

> **Both files are hosted on Google Drive — not in GitHub Releases.** GitHub caps release assets at 2 GB; the installer and the data pack both live in the same Drive folder:
>
> 👉 **https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW**

1. **Download the installer** — `TomsLab-Setup-1.0.0.exe` from the Drive folder above.
2. **Download the data pack** — `tomslab-data-2026-04-21.tar.zst` (~10.5 GB) from the same Drive folder. When Drive warns it can't scan the file for viruses, choose **Download anyway** (this happens for any file over ~100 MB).
3. **Run the installer**, accept the SmartScreen warning ("More info → Run anyway"), pick an install location, click Install.
4. **Launch Tom's Lab** from the Start Menu, accept the disclaimer (one-time).
5. **Install Ollama** from https://ollama.com/download/windows (required for query embedding):
   - `ollama pull nomic-embed-text` (~270 MB)
   - `ollama pull llama3.1:8b` (~4.7 GB, used as fallback when Gemini is unavailable)
6. **(Recommended)** Add a free Gemini API key in **File → Settings…** for higher-quality Ask Tom answers.
7. In Tom's Lab, **File → Install data pack…** and pick the `.tar.zst` you downloaded in step 2.

⏱ **The data-pack install takes ~5-15 minutes** depending on your disk (NVMe SSD ~5-8 min; SATA SSD ~8-15 min; HDD up to 45 min). It unpacks ~25-30 GB to disk and the Tom's Lab window may show **"Not Responding"** for stretches during extraction — that is **not a crash**, just the UI thread blocked by the decompress. Please wait it out. A future release will move the extract to a background thread.

Full install reference: see [`USER_MANUAL.md`](https://github.com/SeanDavid-stack/tomslab/blob/main/USER_MANUAL.md) §3.

---

## What's in v1.0.0

### Library / search functionality
- **Keyword search** — full-text FTS5 over every Discord message and PDF page. `@author` syntax to scope by poster.
- **Semantic search** — meaning-based via Ollama `nomic-embed-text`. Finds posts by concept, even when the words don't match.
- **Visual search** — CLIP joint image-text embeddings. Type `chart with three peaks` and the most-similar charts surface first.
- **Ask Tom (conversational)** — multi-turn natural-language Q&A. Every claim is cited inline with click-through to the exact Discord post (`[msg:…]`), PDF page (`[doc:…]`), or YouTube timestamp (`[vid:…]`). Symmetric citation rule: when material in all three source types is retrieved, the answer must cite from each.
- **Deep Dive** — Ask Tom mode that triples retrieval and writes a 600–1,200 word structured briefing.
- **Sources sort** — flips both the source strip AND the order in which the answer body presents the evidence (oldest-first walks Tom's evolution chronologically).

### UX polish in this release
- **Show in timeline** for Discord citations works for any message, even posts older than the recent-history window.
- **Top search bar** (Keyword / Semantic / Visual) hides on tabs that don't use it (Ask Tom, Docs, TomTube, Bookmarks).
- **Splash + disclaimer** open in correct z-order on first launch.
- **Sample prompts** trimmed to ones grounded in Tom's authored PDFs.
- **Tom only** pill renamed to **★ Tom** so the descender no longer clips its rounded edge.

---

## Known limitations

- **Three of Tom's PDFs** (Glossary, AMT-101, Stats by Target) are currently single-page-indexed and need re-ingesting at full page granularity. Search and citations work for those documents — granularity will improve in v1.0.1.
- **56 YouTube videos** still pending transcription (out of 469 ingested). They'll be picked up by the next transcribe pass.
- **No code signing.** SmartScreen will warn on first launch. This is by design and won't change.

---

## Thanks

Tom B for publishing the material this app indexes. The Bookmap Discord community for keeping the channel active. Beta readers of the demo pack who pushed back on inaccurate copy.

---

*Tom's Lab is published by **SDE-Software · SDES.DEV** · © 2026.*
