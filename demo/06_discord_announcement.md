# Discord announcement — Tom's Lab v1.0.2

*Drafted by SDE-Software, April 2026. Edit before posting if Tom prefers different wording.*

---

## Short version (paste this — fits in Discord's 2000-char limit)

> **Tom's Lab v1.0.2 is out — and it's mostly about making the download dramatically smaller.**
>
> 🎯 The typical user's total download went from **~12.6 GB → ~950 MB**. About 93% smaller.
>
> **Three things changed:**
>
> **1. Two installer flavors.** The new **Standard installer is 225 MB** (was 2.13 GB on v1.0.1). The GPU installer (~2.1 GB) is for users with NVIDIA cards who want hardware acceleration for visual search and YouTube transcription. Most users want the Standard.
>
> **2. Tom-only data pack.** A new **725 MB** flavor containing only Tom's own messages, his attached charts, his PDFs, and his YouTube transcripts. The Full pack (7.66 GB, all community context) is still there for users who want the conversational replies.
>
> **3. Auto-update banners now actually work.** v1.0.0 shipped pointing at an unpublished update URL, so no v1.0.0 install ever detected v1.0.1. That was fixed in v1.0.1 → existing v1.0.1 users will see a banner for v1.0.2 within 24 hours of next launch.
>
> **If you're already on v1.0.0 or v1.0.1:** you'll get a notification soon. Pick the **Standard** installer unless you actually have an NVIDIA card. **You don't need to re-download the data pack** — your existing one still works.
>
> **If you're new:** grab the Standard installer + a data pack from Drive.
>
> 📦 **Downloads:** https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW
> 📖 **Release notes:** https://github.com/SeanDavid-stack/tomslab/releases/tag/v1.0.2
>
> ⚠️ Same disclaimers as always: Tom hasn't reviewed or endorsed this app — it's an independent third-party project from SDE-Software. Not affiliated with Tom B, Bookmap, or Discord. Installer isn't signed (SmartScreen will warn). Free, no support. AI answers can be wrong — verify everything against the original source.

---

## Longer version (use if pinned post / readme channel)

### What's new in v1.0.2

This is a footprint-focused release. No new features, no behavior changes. Same Tom's Lab — just much smaller to download and install.

### The headline

| | v1.0.1 (before) | v1.0.2 (now) |
|---|---|---|
| Standard installer | 2.13 GB | **225 MB** |
| Data pack (typical) | 10.5 GB | **725 MB** *(Tom-only flavor)* |
| **Typical total download** | **~12.6 GB** | **~950 MB** |

### Why was it so big before?

The v1.0.0 and v1.0.1 installers bundled the full NVIDIA CUDA driver stack — about 3 GB of GPU libraries — into every download. The vast majority of users don't have an NVIDIA graphics card, so those libraries just sat on their disk doing nothing.

v1.0.2 splits that into two installers:

- **Standard installer** (225 MB) — works on any modern Windows machine. Visual search and YouTube transcription run on CPU. Slower for those specific operations, but everything works.
- **GPU installer** (2.1 GB) — same as before. Bundles the CUDA libraries. Pick this if you have an NVIDIA card and plan to do heavy visual searches or transcribe YouTube videos.

For day-to-day searching the shipped data pack, both flavors feel identical.

### Tom-only data pack

The v1.0.0 / v1.0.1 data pack contained 587K Discord messages — Tom's 89,748 plus all the community replies and side conversations. Lots of people install Tom's Lab specifically *because of Tom*, and the community context isn't what they're after.

v1.0.2 adds a second data pack flavor:

- **Full pack** (7.66 GB) — Tom + community Discord history (587K messages), Tom's PDFs, Tom's YouTube transcripts. Picks up conversational context — when Tom replies to someone, you can read what the question was.
- **Tom-only pack** (725 MB) — Just Tom's own messages and his attached charts (89,748 messages). Tom's PDFs and YouTube transcripts still included. **93% smaller download.** Drops community context — replies to non-Tom users will show "(message not in pack)".

### What about my v1.0.0 / v1.0.1 install?

You'll get an auto-update notification within 24 hours of next launch. Click through, download the new Standard installer, run it. **Your data and settings carry over** — and **you do NOT need to re-download the data pack**. The v1.0.1 pack continues to work with v1.0.2.

The old installers and the v1.0.1 data pack are still archived in the Drive folder under `archive/` if you ever need to roll back.

### How to install

**Downloads (Drive folder — pick one installer + one data pack):**
https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW

1. Download `TomsLab-Setup-1.0.2.exe` (Standard) unless you have an NVIDIA card and want GPU acceleration.
2. Download a data pack — `tomslab-data-tom-only-2026-04-27.tar.zst` for Tom-only (small), or `tomslab-data-2026-04-27.tar.zst` for the Full pack (larger, includes community context).
3. Run the installer. SmartScreen will warn — click **More info → Run anyway** if you accept the risk.
4. Launch Tom's Lab.
5. **File → Install data pack…** and pick the downloaded `.tar.zst`.
6. **File → Settings…** to install Ollama (required, free, local) and optionally paste a Gemini or Groq API key for higher-quality Ask Tom answers.

Full step-by-step instructions are in the bundled `USER_MANUAL.md`.

### What you accept by installing

- This is unsigned software from an unknown publisher.
- AI-generated answers can be wrong, and you must verify against the source.
- Importing Discord exports and YouTube downloads may violate those platforms' Terms of Service, and that decision is yours.
- This is not financial advice; any trading decisions you make based on what you find here are yours alone.
- Nobody at SDE-Software, Tom, Bookmap, or Discord owes you support, fixes, updates, or even a reply.

If any of that's not acceptable to you, please **don't install it**. There is no obligation either way.

---

*Tom's Lab is published by **SDE-Software · SDES.DEV** · © 2026.*
