# Tom's Lab v1.0.1 — patch release

A small fix-and-polish release. **No re-install of the data pack is needed** if you already have the `tomslab-data-2026-04-21.tar.zst` from v1.0.0 — the corpus is unchanged.

Published by **SDE-Software (SDES.DEV)**.

---

## ⚠️ Read before installing (same disclaimers as v1.0.0)

- **Tom B has not reviewed or endorsed this app.** Independent third-party project from SDE-Software. Tom is not affiliated with it and will not support it.
- **Not affiliated** with Tom B, Bookmap Ltd., the Bookmap Discord moderators, or Discord Inc. Do not contact any of them about this app.
- **The installer is NOT digitally signed and never will be.** Windows SmartScreen will warn on first launch — you click "More info → Run anyway" at your own risk.
- **Free, no support.** SDE-Software does not provide one-on-one help.
- **Bulk-exporting Discord messages and bulk-downloading YouTube content may violate those platforms' Terms of Service.** Importing your own export and your own video downloads is your decision and your responsibility.
- **AI answers can be wrong. Verify everything against the original source before relying on it.**

---

## What's new in v1.0.1

### Groq added as a third chat provider

Groq is now an alternative to Gemini for the **Chat (Ask Tom)** role. Chat-only — embedding and vision still need Ollama or Gemini.

| Trade-off | Gemini 2.5 Flash | Groq (Llama 3.3 70B) |
|---|---|---|
| Free-tier daily limit | ~1,500 requests | ~14,400 requests |
| Token streaming speed | Fast | Much faster (LPU hardware) |
| Citation discipline (Ask Tom links) | Better | Less reliable — verify links |
| Context window | 1M tokens | 128K tokens |

Use Gemini by default; switch to Groq if you regularly hit Gemini's daily cap or want faster streaming. **Settings → AI Providers** has a new Groq section — paste a free key from [console.groq.com/keys](https://console.groq.com/keys) and change the **Chat** dropdown from `gemini` to `groq`.

### Update notifications now actually work

v1.0.0 shipped with the auto-update checker pointing at a JSON manifest URL (`raw.githubusercontent.com/SDES-Software/tomslab/main/latest.json`) that was never published. As a result, **no v1.0.0 install would ever have detected this release** — you would have had to find out about it manually.

v1.0.1 switches the checker to the GitHub Releases API directly (`api.github.com/repos/SeanDavid-stack/tomslab/releases/latest`), which is self-maintaining: cutting a new tag is the only release action needed to notify users. **Existing v1.0.0 installs are silently migrated to the new URL on first launch.**

If you previously had auto-update on, you'll get the v1.0.2 banner whenever it ships. No action required.

### Documentation cleanup

- README and the user manual have a prominent **System Requirements** section (minimum CPU/RAM/disk + an honest GPU vs CPU breakdown by use case).
- README has a **Download** section with the Google Drive folder link at the top of the page.
- v1.0.0 release notes pointed at a GitHub Assets section that didn't exist (the installer is too large to host there). Both files now live in the Drive folder.

---

## Install

> **Both the installer and the data pack are hosted on Google Drive — not on GitHub Releases.**
>
> 👉 **https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW**

### If you're upgrading from v1.0.0

1. Download `TomsLab-Setup-1.0.1.exe` from the Drive folder above.
2. Run the installer. It upgrades in place; your data and settings are preserved. SmartScreen will warn — click **More info → Run anyway** if you accept the risk.
3. **You do not need to re-download the data pack.** The `tomslab-data-2026-04-21.tar.zst` from v1.0.0 is still current.

### Fresh install

Same five-step flow as v1.0.0 — see [`USER_MANUAL.md`](https://github.com/SeanDavid-stack/tomslab/blob/main/USER_MANUAL.md) §3 for full step-by-step instructions, including Ollama setup and the data-pack install.

---

## Known limitations (carried over from v1.0.0)

- Three Tom PDFs (Glossary, AMT-101, Stats by Target) are single-page-indexed and need re-ingesting at full per-page granularity. Search and citations work; per-page granularity does not.
- 56 of 469 ingested YouTube videos are still pending transcription.
- No code signing. SmartScreen will warn on first launch. This is by design and won't change.

---

*Tom's Lab is published by **SDE-Software · SDES.DEV** · © 2026.*
