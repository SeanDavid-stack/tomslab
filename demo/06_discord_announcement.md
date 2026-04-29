# Discord announcement — Tom's Lab v1.0.0

*Drafted by SDE-Software, April 2026. Edit before posting if Tom prefers different wording.*

---

## Short version (paste this)

> **Tom's Lab — a free, local research tool for going deeper into Tom B's publicly-shared material.**
>
> It's a desktop application from **SDE-Software (SDES.DEV)** that turns the `traders-lab-tom-b` Discord channel, Tom's reference PDFs, and his public YouTube uploads into one searchable local library. Keyword search, semantic / meaning-based search, visual chart search, and an "Ask Tom" conversational mode that pulls Tom's own posts, PDF pages, and video transcript moments side by side with click-through to the exact source. **587,469 Discord messages indexed (Dec 2021 → Apr 2026), 413 of Tom's YouTube videos transcribed locally, 8 Tom-authored reference PDFs, 36,770 chart images.**
>
> **It is not a course.** It does not teach the methodology. It assumes you already follow Tom's content and want a faster way to dive deeper into a specific concept, find a specific post, or see how a topic has evolved. **It is not a trading tool** — no signals, no alerts, no strategy generation.
>
> **🛠 Requires Ollama** (free, local) to run the AI features — install from ollama.com and pull `nomic-embed-text` and `llama3.1:8b`. A free Gemini API key is optional but strongly recommended for higher-quality answers.
>
> **⚠️ Important disclaimers:**
> - **Tom B has not reviewed or endorsed this app.** It is an independent third-party project.
> - **Not affiliated with Tom B, Bookmap, the Bookmap Discord moderators, or Discord Inc.** Do not contact any of them about this app.
> - **The installer is NOT digitally signed and never will be.** Windows SmartScreen will warn on first launch — you click "More info → Run anyway" at your own risk. By installing, you accept that you are running unsigned software from an unknown publisher and that all consequences are yours.
> - **Free, no support.** SDE-Software does not provide one-on-one help, troubleshooting, or installation assistance. The user manual is the entire support layer.
> - **Bulk-exporting Discord messages and bulk-downloading YouTube content may violate those platforms' Terms of Service. Importing your own export and your own video downloads is your decision and your responsibility.**
> - **AI answers can be wrong. Verify everything against the original source before relying on it.** This is an experimental research tool, not financial advice. All trading decisions are yours.
>
> 📦 **Downloads (installer + data pack):** https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW
> 📖 **Install steps + release notes:** https://github.com/SeanDavid-stack/tomslab/releases/tag/v1.0.0
>
> *(Both the installer and the 10.5 GB data pack are hosted on Google Drive — GitHub Releases caps individual files at 2 GB.)*

---

## Longer version (use if pinned post / readme channel)

### What Tom's Lab is

Tom's Lab is a free desktop application — a **local searchable library** built on top of Tom B's publicly-shared trading material. It indexes:

- **587,469 Discord messages** from `traders-lab-tom-b` (Dec 3, 2021 → Apr 18, 2026)
   *(89,748 from Tom B, 497,721 community)*
- **413 transcribed YouTube videos** out of 469 ingested (29,862 ~90-second transcript chunks; the other 56 retry on the next transcribe pass)
- **8 Tom-authored reference PDFs** plus 1 Linnsoft community thread and 2 third-party reference books for background context
- **36,770 chart attachments** with CLIP-powered visual search
- **26 Tom-glossary concepts** seeded from his Glossary PDF

It runs entirely on your machine. Nothing phones home, no account required, no telemetry, no ads, no subscription.

### What it does

- **Keyword search** — fast literal full-text over every message, post, and PDF page.
- **Semantic search** — meaning-based; finds posts that talk about a concept even when the words don't match.
- **Visual search** — type *"chart with absorption at the high"* and get the most visually-similar charts.
- **Ask Tom** — natural-language Q&A over the whole corpus. Every claim is cited inline; click a citation to jump to the exact Discord post, PDF page, or YouTube timestamp.
- **Deep Dive** — Ask Tom mode that triples retrieval and writes a 600–1,200 word structured briefing (definition · setups · entry/exit · examples · common mistakes · key takeaways).
- **Click-through citations** — `[msg:…]` opens the post, `[doc:…]` opens the PDF page, `[vid:…]` opens YouTube at the exact timestamp.

### What it is NOT

- **Not a course.** Doesn't teach the methodology from scratch. Assumes you already know who Tom is and have read at least some of his pinned material.
- **Not a trading tool.** No signals, no alerts, no strategy generation, no broker connection.
- **Not affiliated with Tom B.** Tom has not reviewed or endorsed it. It is an independent third-party project from SDE-Software.
- **Not signed.** The installer is not Authenticode-signed and never will be. Windows SmartScreen will warn on first launch.
- **Not supported.** No one-on-one help. Self-service via the bundled user manual.

### How to install

**Downloads (Drive folder — installer and data pack):**
https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW

1. Download the installer (`TomsLab-Setup-1.0.2.exe` is the small Standard build, ~225 MB; `TomsLab-Setup-1.0.2-GPU.exe` is the full GPU build for NVIDIA users) and a data pack (`.tar.zst` — pick Full or Tom-only) from the Drive folder above.
2. Run the installer. SmartScreen will warn — click **More info → Run anyway** if you accept the risk.
3. Launch Tom's Lab.
4. **File → Install data pack…** and pick the downloaded `.tar.zst`.
5. **File → Settings…** to install Ollama (required, free, local) and optionally paste a Gemini API key (optional, free tier, dramatically improves Ask Tom answer quality).

Full step-by-step instructions are in the bundled `USER_MANUAL.md`.

### What you accept by installing

- That this is unsigned software from an unknown publisher.
- That AI-generated answers can be wrong, and you must verify against the source.
- That importing Discord exports and YouTube downloads may violate those platforms' Terms of Service, and that decision is yours.
- That this is not financial advice, and any trading decisions you make based on what you find here are yours alone.
- That nobody at SDE-Software, Tom, Bookmap, or Discord owes you support, fixes, updates, or even a reply.

If any of that's not acceptable to you, please **don't install it**. There is no obligation either way.

---

*Tom's Lab is published by **SDE-Software · SDES.DEV** · © 2026.*
