# Tom's Lab v1.0.2 — slim release

A footprint-focused release. Same features, dramatically smaller download. **The two big wins:**

1. **Two installer flavors** — a small CPU-only installer that's the new default, and a separate GPU installer for users with NVIDIA cards. Most users save ~75% on installer size.
2. **Smaller data pack** — chart images are now downscaled to 1600 px wide before re-encoding. Cuts the pack significantly without affecting search quality (CLIP downscales to 224×224 internally anyway).

Published by **SDE-Software (SDES.DEV)**.

---

## ⚠️ Read before installing (same disclaimers as v1.0.0/1.0.1)

- **Tom B has not reviewed or endorsed this app.** Independent third-party project.
- **Not affiliated** with Tom B, Bookmap Ltd., Bookmap Discord moderators, or Discord Inc.
- **Installer is NOT digitally signed.** Windows SmartScreen will warn — accept the risk if you're comfortable.
- **Free, no support.** Self-service via the bundled `USER_MANUAL.md`.
- **Bulk-exporting Discord/YouTube content may violate those platforms' Terms of Service.** Your decision and responsibility.
- **AI answers can be wrong. Verify against the original source.**

---

## What's new in v1.0.2

### Two installer flavors

The v1.0.0 and v1.0.1 installers bundled GPU PyTorch with full CUDA driver libraries — about **3 GB** of NVIDIA libraries that 90% of users never touched (they never had an NVIDIA card to use them). v1.0.2 splits that:

| | Standard installer | GPU installer |
|---|---|---|
| File on Drive | `TomsLab-Setup-1.0.2.exe` | `TomsLab-Setup-1.0.2-GPU.exe` |
| Download size | **225 MB** | ~2.1 GB |
| Installed footprint | **892 MB** | ~5.2 GB |
| Best for | Anyone without an NVIDIA card | Users with an NVIDIA card who want full speed on visual search and YouTube transcription |
| Visual search (CLIP) | Works on CPU — slower but functional | GPU-accelerated — much faster |
| YouTube transcription (Whisper) | CPU — much slower (1-hr video ≈ 1–3 hr) | GPU — much faster (1-hr video ≈ 10–30 min) |
| Ask Tom (Gemini/Groq cloud) | Identical | Identical |

**Pick the Standard installer unless you specifically have an NVIDIA card and plan to transcribe YouTube videos or do heavy visual searches.** Day-to-day searching the shipped data pack is the same speed either way once embeddings are loaded.

### Smaller data pack — and a brand-new Tom-only flavor

Chart images bulk up the data pack — they come from Discord at full resolution, often 1900–2100 px wide. The data pack builder now downscales them to 1600 px max before WebP re-encoding. CLIP itself downscales to 224×224 at search time, so the loss of detail is invisible to the search engine.

There are now **two data pack flavors** to pick from:

| | Full pack | Tom-only pack |
|---|---|---|
| File | `tomslab-data-2026-04-27.tar.zst` | `tomslab-data-tom-only-2026-04-27.tar.zst` |
| Size | **7.66 GB** (was 10.5 GB on v1.0.1 — saved 2.84 GB) | **725 MB** (93% smaller than v1.0.1) |
| Messages | 587,469 (Tom's 89,748 + community 497,721) | **89,748 — Tom only** |
| Charts | 36,770 (1600 px max) | 1,047 — Tom's charts only |
| Tom's PDFs + YouTube transcripts | All included | All included |
| Search context for community questions | Yes | No — replies show "(message not in pack)" |
| Best for | Users who want the full archive and conversational context | Users who only want Tom's own teaching material |

**If you already have the v1.0.1 data pack installed, you do NOT need to re-download.** The old pack still works with v1.0.2 — the schema is unchanged. Re-download only if you want the smaller footprint or the Tom-only flavor.

### Update notifications stay armed

The fix shipped in v1.0.1 keeps working — your installed v1.0.1 client will notify you about v1.0.2 as soon as it lands.

---

## Install

> **Both installers + the new data pack are on Google Drive — not GitHub Releases.**
>
> 👉 **https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW**

### If you're upgrading from v1.0.0 or v1.0.1

1. Download the **Standard** installer (`TomsLab-Setup-1.0.2.exe`) from the Drive folder, unless you have an NVIDIA card and want GPU acceleration — in which case grab `TomsLab-Setup-1.0.2-GPU.exe`.
2. Run the installer. It upgrades in place; your data and settings are preserved. SmartScreen will warn — click **More info → Run anyway** if you accept the risk.
3. **You do not need to re-download the data pack.** The v1.0.1 pack continues to work.

### Fresh install

Same five-step flow — see [`USER_MANUAL.md`](https://github.com/SeanDavid-stack/tomslab/blob/main/USER_MANUAL.md) §3.

---

## Known limitations (carried over)

- Three Tom PDFs (Glossary, AMT-101, Stats by Target) are still single-page-indexed.
- 56 of 469 ingested YouTube videos still pending transcription.
- No code signing.

---

*Tom's Lab is published by **SDE-Software · SDES.DEV** · © 2026.*
