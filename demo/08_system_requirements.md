# Tom's Lab — system requirements

*A free desktop study tool published by **SDE-Software (SDES.DEV)**.*
*Version: 1.0.0 · Date: April 2026*

---

## Minimum to run effectively

| Component | Status | Notes |
|---|---|---|
| OS | Required | Windows 10 or 11, 64-bit. Mac and Linux are unsupported. |
| CPU | Required | 4-core x86-64. Embedding and classification are CPU-heavy. |
| RAM | Required | 16 GB minimum. 32 GB recommended if you transcribe YouTube audio. |
| Disk | Required | **~40 GB free.** App ≈ 5 GB; data pack download ≈ 10.5 GB; unpacks to ~25–30 GB on disk. |
| Ollama | Required | Free local AI used for query embedding. The app cannot embed questions without it. Install from **ollama.com/download/windows**. |
| Gemini API key | Recommended | Free tier is sufficient. Higher-quality Ask Tom answers than the local Ollama fallback. Get one at **aistudio.google.com/apikey**. |
| NVIDIA GPU + driver | Optional | The app runs on CPU. How much you miss the GPU depends on what you do — see below. |

---

## Does the GPU matter?

The app runs on a CPU-only machine. Whether you feel the absence of a GPU depends entirely on what you ask it to do:

- **Searching the shipped data pack and using Ask Tom via Gemini.** Barely matters. Searches return in well under a second on CPU once embeddings have loaded.
- **Re-embedding your own Discord export, or re-running PDF OCR on imported documents.** Moderately slower — minutes become tens of minutes.
- **Transcribing YouTube videos with Whisper.** **Much, much slower.** A 1-hour video that takes ~10–30 minutes on a modern NVIDIA GPU can take **~1–3 hours on CPU**.
- **Local Ask Tom fallback (no Gemini key, using Ollama Llama 3.1 8B).** Big difference. On CPU answers come back as a slow trickle; on GPU they're near-instant.

If you only plan to use the shipped data pack with a free Gemini key, a GPU is genuinely optional. If you plan to transcribe your own YouTube content, a GPU is strongly recommended.

---

## Disk speed during the data-pack install

The one-time data-pack install is dominated by writing 25–30 GB to disk:

| Disk | Approximate install time |
|---|---|
| NVMe SSD | ~5–8 minutes |
| SATA SSD | ~8–15 minutes |
| Mechanical HDD | ~25–45 minutes (sometimes longer) |

The Tom's Lab window may show **"Not Responding"** in its title bar for a minute or two during the extract phase. That is **not a crash** — the install is working. Wait it out; do not End Task.

---

## Where to read more

Full operational detail — installation steps, the SmartScreen warning, Ollama model setup, optional Gemini configuration, and troubleshooting — is in `USER_MANUAL.md` §2 and §3.

---

*Published by SDE-Software · SDES.DEV · © 2026. All rights reserved.*
