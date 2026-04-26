# Tom's Lab

**A free desktop research tool for going deeper into Tom B's publicly-shared trading material — a searchable library of what already exists, not a course that teaches it.**

*Published by SDE-Software (SDES.DEV) — version 1.0.0, April 2026.*

---

## What it is

Tom's Lab is a desktop application that turns three large bodies of Tom B's publicly-shared material into one **local, searchable, click-through reference**:

- **Bookmap Discord — `traders-lab-tom-b` channel:** the user imports their own Discord export.
- **Tom's reference PDFs:** Tom's authored documents (Auction Market Theory 101, Market Structure, Mean Reversion Structured Trade, Opening Context Alignment, Stats by Target, 60 Structured Trades, Bookmap Settings, Trader Lab Glossary).
- **Tom's public YouTube uploads:** transcribed locally with Whisper, with click-through that opens YouTube at the exact timestamp.

It is a **research aid**, not a course. Tom's Lab does not teach the methodology from A to Z, it does not walk you through a structured curriculum, and it is not a substitute for reading Tom's own posts and PDFs or watching his videos. It is for people who **already follow Tom's content** and want a faster way to dive deeper into a specific concept, find a specific post they remember, see how a topic evolved over years of Discord activity, or pull together everything Tom has said about an idea across Discord, PDFs, and video.

It is also not a trading tool — no signals, no live alerts, no strategy generation. The app is for **research and study**, full stop.

### Who this is for

You'll get the most out of Tom's Lab if you:

- Already know who Tom is and have read at least some of his pinned material.
- Want to study a specific Tom concept in depth instead of scrolling Discord by hand.
- Want to revisit something Tom said and aren't sure whether it was a Discord post, a PDF page, or a video moment.
- Want to build on top of what Tom teaches with your own notes (favourites, bookmarks).

You'll get less out of it if you're brand new to order flow or auction market theory and want a structured "start here, then read this, then practice that" curriculum. Tom's Lab assumes the curriculum exists — in his own words, in his published material — and helps you navigate it.

---

## What's in the corpus

The currently-shipped data pack indexes (counts verified against the live database):

| Source | Count |
|---|---|
| Discord messages | **587,469** (December 3, 2021 → April 18, 2026) |
| ↳ posted by Tom B | 89,748 |
| ↳ posted by the community | 497,721 |
| Chart attachments with local files | **36,770** (CLIP visual-searchable) |
| YouTube videos ingested | **469 total** |
| ↳ transcribed | 413 |
| ↳ pending re-transcription | 56 (the next transcribe pass picks them up) |
| Video transcript chunks | 29,862 (~90 sec each, click-to-timestamp) |
| Tom's authored reference PDFs | **8** (Auction Market Theory 101, Market Structure, Mean Reversion Structured Trade, Opening Context Alignment, Stats by Target, 60 Structured Trades, Bookmap Settings, Trader Lab Glossary) |
| Other ingested references | 1 Linnsoft community thread (Tom B-curated); 2 third-party trading books retained as background context |
| Glossary concepts | 26 (Tom's vocabulary, seeded from his Glossary PDF) |

*Caveat on PDF coverage:* three of Tom's authored PDFs (Glossary, AMT-101, Stats by Target) are currently indexed at one-page-per-document and need re-ingesting at full per-page granularity. **Search, retrieval, and citation work for those documents** — but the granularity will improve in the v0.1.1 data pack. The other five Tom PDFs are fully indexed page-by-page.

Every message, attachment, video chunk, and PDF page is **embedded once** into both a text-meaning vector and a CLIP image-meaning vector, so the app can find content by literal words, by meaning, or by what a chart looks like.

---

## How you search

The app offers four search modes that complement each other.

### 1. Keyword search

Classic full-text search powered by SQLite FTS5. Type `VPOC absorption` and you get every message that contains those tokens, ranked by BM25. Use `@author` to scope by poster (`@alice volume profile`).

Best for: finding a specific phrase you remember.

### 2. Semantic search

Meaning-based. The app embeds your query through Ollama's `nomic-embed-text` model and returns posts whose meaning is closest, even when the words don't match. Asking *"how does Tom handle overnight inventory imbalance?"* surfaces posts that talk about gaps, balance area drift, and overnight rotation — even if those exact words aren't in the question.

Best for: concept-level questions where you don't know Tom's exact vocabulary.

### 3. Visual search

CLIP joint image-text embeddings on every chart in the export. Type *"chart with three clear peaks"* or *"Bookmap heatmap with absorption at the top"* and the gallery reorders to put the most visually-similar charts first.

Best for: "I remember a chart but not which post it came from."

### 4. Ask Tom — conversational

Multi-turn natural-language Q&A over the entire corpus. You ask in plain English; the app retrieves the most relevant Discord posts, PDF pages, and video chunks, then asks Gemini (cloud) or Ollama (local fallback) to synthesise an answer **using only that retrieved material**, with every claim cited inline.

If the corpus doesn't cover something, the answer says it doesn't know rather than guessing.

#### Deep Dive

A toggle on Ask Tom that triples the retrieval budget and asks the model to write a 600–1,200 word structured briefing with sections (Definition · Setup conditions · Entry & exit · Examples from Tom's teaching · Common mistakes · Related concepts), Tom's own quoted words, and a "key takeaways" close.

Best for: spending 5 minutes really understanding a concept rather than skimming a one-liner.

---

## Click-through citations — every claim leads to a source

Every answer Tom's Lab produces shows the evidence behind it. Three citation types render as clickable pills directly inside the answer text:

- **`[msg:…]` — a Discord post.** Click it to open a popover with the full message; click **"Show in timeline"** to scroll the Feed to that exact post (works even for messages from years ago).
- **`[doc:…]` — a page from one of Tom's reference PDFs.** Click it to jump to that page in the Docs tab with the rendered scan visible.
- **`[vid:…]` — a YouTube transcript chunk.** Click it to open YouTube at the exact ~90-second window where Tom said it.

If the retrieval found relevant material across all three source types, the answer is required to cite at least one of each — so you see Tom's authored framework AND his real-time Discord reasoning AND him saying it out loud, side by side.

A **Sources** strip beneath every answer also lists the same citations explicitly, sortable oldest-first or newest-first. Switching the sort flips both the source list AND the order in which the answer body presents the evidence — useful for *"how did Tom's thinking evolve?"* questions.

---

## Other useful surfaces

- **Feed tab** — the raw Discord view, styled like the original channel. Tom's posts get a gold accent. The same Keyword / Semantic / Visual modes apply here, so you can browse the channel and search it with whichever mode fits.
- **Gallery tab** — every chart attachment as a thumbnail grid, browsable or visual-search-driven.
- **Docs tab** — Tom's reference PDFs page-by-page, with full text available to search.
- **TomTube tab** — the indexed YouTube videos. Click any transcribed segment to open YouTube at that timestamp.
- **Bookmarks tab** — anything you've starred (messages, charts, Ask Tom answers) for later.
- **Glossary chips** above the search bar — Tom's defined terms (VPOC, NVPOC, IB, IBH, IBL, MR, VWAP, …) with mention counts. One click filters the Feed or seeds an Ask Tom prompt.
- **Daily Study** — a 5-minute one-concept-per-day reading drill picked from Tom's glossary.

---

## What it is not

- Not a signals service. No live alerts. No "buy here" output.
- Not a trading platform. Doesn't connect to a broker.
- Not a strategy generator. Doesn't backtest.
- Not affiliated with Tom B, Bookmap Ltd., the Bookmap Discord moderators, or Discord Inc.
- Not endorsed by Tom B. He has not reviewed or approved its outputs.
- Not financial advice. AI-generated answers can be wrong; verify everything against the original source before relying on it.

---

## Distribution & support model

- **Free.** No fee, no subscription, no ads, no telemetry, no account.
- **Self-service.** No one-on-one support. The user manual covers everything that's covered.
- **Not digitally signed.** Windows SmartScreen will warn on first run; you accept the risk of running unsigned software when you install it.
- **You own your data.** Discord export is the user's responsibility (subject to Discord's Terms of Service); YouTube downloads are the user's responsibility (subject to YouTube's Terms of Service). Tom's Lab does not fetch anything on your behalf.

---

*Published by **SDE-Software · SDES.DEV** · © 2026.*
