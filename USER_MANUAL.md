# Tom's Lab — User Manual

Tom's Lab is a free desktop **library / searchable encyclopedia** of
Tom B's publicly-shared teaching from the Bookmap Discord
`traders-lab-tom-b` channel, his published reference PDFs, and his
public YouTube uploads. It is **not** a trading tool, signals service,
or strategy generator — it is a study reference. Published by
**SDE-Software (SDES.DEV)**.

This manual is the single source of truth for operating the program.
Every feature, limitation, and known issue is documented here. No
separate one-on-one assistance is provided.

---

## 1. Policy — read first

Before installing or using Tom's Lab, understand the terms under which
it is shared. Continued use indicates agreement.

### 1.1 Free tool, no warranty

Tom's Lab is a free, as-is utility. There is no purchase, no licence
fee, and no associated service contract. The software is provided
without warranty of any kind.

### 1.2 Self-service only

No one-on-one support, walkthroughs, troubleshooting calls, email
support, chat support, or individual installation assistance is
provided by the publisher. All operational guidance is in this manual.
If a question is not answered here, it is out of scope.

Do not contact Tom B, Bookmap Ltd., the Bookmap Discord moderators, or
Discord Inc. about Tom's Lab. None of them are associated with the
program and none of them will be able to help.

### 1.3 User responsibility

The user is responsible for:

- Installing Python dependencies, the application itself, and any
  optional components (Ollama, a Gemini API key, GPU drivers) as
  documented in this manual.
- Obtaining and importing their own corpus material: Discord Chat
  Exporter JSON files, YouTube videos or audio, and reference PDFs.
- Respecting all third-party Terms of Service when obtaining that
  material. Bulk-exporting Discord messages and bulk-downloading
  YouTube content may violate those platforms' terms. The decision to
  do so — and any consequences — is the user's alone.
- Verifying every AI-generated answer against the original source
  before relying on it. Models can misread, misquote, or invent
  plausible-sounding detail. This is an experimental research tool,
  not financial advice.
- All trading decisions and any resulting gains or losses.

### 1.4 Updates

Bug fixes and feature updates may be released on an occasional,
discretionary basis. No schedule is guaranteed. No commitment to fix,
respond to, or acknowledge individual reports is made. Users are
responsible for checking for new releases and installing them.

### 1.5 Independent third party

Tom's Lab is an independent software project. It is not affiliated
with, endorsed by, sponsored by, or connected to Tom B, Bookmap Ltd.,
the Bookmap Discord, Discord Inc., Google, Ollama, Hugging Face, or
any other third party referenced elsewhere in the program.

### 1.6 Same structure as BMBridge Lite

Tom's Lab is the free side of SDE-Software's product line. Its support,
warranty, and liability model is the same as BMBridge Lite: published
for public benefit, used entirely at the user's own risk, no paid
support tier.

Full legal terms are presented in the app on first launch and are
available at any time under **Help → Disclaimer & Legal**.

---

## 2. System requirements

| Component | Status | Notes |
|---|---|---|
| OS | Required | Windows 10 or 11, 64-bit. Other platforms unsupported. |
| CPU | Required | 4 cores, x86-64. Embedding and classification are CPU-heavy. |
| RAM | Required | 16 GB minimum. 32 GB recommended if transcribing YouTube audio. |
| Disk | Required | 15 GB free. Data pack ≈ 10 GB, app ≈ 5 GB. |
| [**Ollama**](https://ollama.com/download/windows) | **Required** | Local AI for query embedding. Ask Tom cannot embed questions without it. |
| [Gemini API key](https://aistudio.google.com/apikey) | Recommended | Higher-quality Ask Tom answers than the local Ollama fallback. |
| [NVIDIA GPU driver](https://www.nvidia.com/download/index.aspx) | Optional | Enables GPU acceleration for CLIP image search and Whisper transcription. Runs on CPU without. |

---

## 3. Installation

### 3.1 Install the application

v1.0.0 ships as an **Inno Setup installer** — a single
`TomsLab-Setup-1.0.0.exe`.

1. Download `TomsLab-Setup-1.0.0.exe` from the project's Google Drive
   folder (the same folder also contains the data pack):
   **https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW**
   The installer is hosted on Drive — not on GitHub — because GitHub
   Releases caps individual assets at 2 GB.
2. Double-click the installer. Approve the UAC prompt.
3. Follow the prompts — pick an install location (default
   `C:\Program Files\TomsLab`), choose whether to create a desktop
   shortcut, then click **Install**.
4. Launch **Tom's Lab** from the Start Menu (or the desktop shortcut
   if you created one). The installer also offers to launch at the
   end of setup.

To uninstall, use **Settings → Apps → Installed apps**. User data
under `%LOCALAPPDATA%\TomsLab\` is **not** removed by the
uninstaller — delete it manually if you want a complete wipe.

> **Windows SmartScreen warning.** Because the installer is not yet
> Authenticode-signed, Windows may show a *"Microsoft Defender
> SmartScreen prevented an unrecognised app from starting"* dialog on
> first launch. Click **More info → Run anyway** to proceed. Code
> signing is planned for a future release.

### 3.2 Install a data pack

A data pack is a pre-built snapshot of the Discord corpus, reference
PDFs, and YouTube transcripts. Without one the application is empty.

The currently shipped pack
(`tomslab-data-2026-04-21.tar.zst`) is **10.5 GB compressed** and
unpacks to roughly **25-30 GB on disk**, so plan for both download
time and a chunky extraction. See the timing table below before you
start.

#### Steps

1. Download the current data pack (`.tar.zst`) from the same Google
   Drive folder as the installer:
   **https://drive.google.com/drive/folders/1Y6Yo1R46dfjSXp5AOYdbAKgHUKEdL0pW**
2. When prompted by Google Drive that it cannot scan the file for
   viruses, choose **Download anyway**. This warning appears for all
   files over approximately 100 MB.
3. Optionally verify the downloaded file's SHA-256 hash against the
   hash published in the release notes.
4. Launch Tom's Lab and go to **File → Install data pack…**.
5. Select the downloaded `.tar.zst` file.
6. Confirm the replacement. The current data directory is backed up
   automatically to `data.backup-<timestamp>` before extraction.
7. Wait for the install to complete. The status bar shows progress;
   see the timing expectations below.

Once installed, all tabs populate automatically.

#### What to expect during install

The install runs in three phases:

| Phase | What it does | Approx duration |
|---|---|---|
| 1. SHA-256 verify | Reads the whole 10.5 GB once to check integrity. | ~1-2 min |
| 2. Backup | Renames the current data directory to `data.backup-<timestamp>`. | Seconds |
| 3. Extract + finalise | Decompresses zstd, writes ~25-30 GB to disk, rewrites paths in the database. | Dominates total time. |

**End-to-end install time depends mostly on your disk speed:**

| Disk | Total expected install time |
|---|---|
| NVMe SSD | ~5-8 min |
| SATA SSD | ~8-15 min |
| Mechanical HDD | 25-45 min, sometimes longer |

#### "Not Responding" — please don't kill the app

During the extract phase, the Tom's Lab window may show **"Not
Responding"** in its title bar for a minute or two at a time. This
is **not a crash** — the install is working. The progress callback
fires only every 500 files, and Windows marks any window that hasn't
painted in 5 seconds as "not responding" by default. **Wait it out;
do not click the close X, do not End Task in the Task Manager, do
not run the installer again.**

If you genuinely think the install is stuck (no disk activity for
several minutes — check the Task Manager **Performance → Disk** tab
for activity on your install drive), the safe recovery path is:

1. Close Tom's Lab.
2. Restart Tom's Lab.
3. Re-run **File → Install data pack…**. The auto-backup created in
   step 2 means the previous data is still on disk under
   `data.backup-<timestamp>`; nothing is lost.

A future release will move the extract to a background thread so the
window stays responsive throughout.

### 3.3 Install Ollama (required)

Ollama is required. The shipped data pack's embeddings were built with
Ollama's `nomic-embed-text` model, and Ask Tom must embed each question
in the same vector space to search against those embeddings. Without
Ollama, Ask Tom cannot answer anything.

1. Download the Windows installer from
   [ollama.com/download/windows](https://ollama.com/download/windows)
   and run it. Ollama installs as a background service that starts
   automatically on login.
2. Open **Command Prompt** or **PowerShell** and pull the required
   models (first pull downloads ~5 GB total):
   ```
   ollama pull nomic-embed-text
   ollama pull llama3.1:8b
   ```
   - `nomic-embed-text` (~270 MB) — required for Ask Tom query embedding.
   - `llama3.1:8b` (~4.7 GB) — required as the chat fallback when
     Gemini is unconfigured or rate-limited.
3. Confirm Ollama is running by visiting
   [http://127.0.0.1:11434](http://127.0.0.1:11434) in a browser —
   you should see "Ollama is running".

### 3.4 Configure a Gemini API key (recommended)

A Google Gemini API key upgrades Ask Tom chat quality substantially
compared to the local Ollama fallback. Embedding always stays local via
Ollama; only the final answer generation goes to Gemini.

1. Create a free key at
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
   (Google account required). The free tier quota is comfortable for
   personal study use.
2. In Tom's Lab: **File → Settings…** and paste the key into
   **Gemini API key**.
3. Click **Test connection** to verify.

Keys are stored encrypted in the local database and never transmitted
anywhere except to Google's own API endpoint.

### 3.5 NVIDIA GPU driver (optional)

CLIP image search and Whisper video transcription both run dramatically
faster on an NVIDIA GPU with CUDA. If you have one, keep its driver
current from
[nvidia.com/download](https://www.nvidia.com/download/index.aspx).
Without a GPU the app still works, just slower.

---

## 4. First launch

On first launch Tom's Lab presents, in order:

1. **Disclaimer & Legal** — a scrollable legal terms dialog. The
   acceptance button is disabled until the text has been scrolled to
   the end. Declining closes the application.
2. **Getting Started & Policy** — an expectations-setting summary of
   the content in section 1 of this manual.
3. **First-run wizard** — offers to import an initial Discord export
   or a data pack.

All three can be re-opened from the **Help** menu at any time.

---

## 5. The main window

The main window has six tabs, from left to right. The **top search
bar** (with the **Keyword / Semantic / Visual** mode dropdown) is
shown only when the **Feed** or **Gallery** tab is active — those are
the two tabs it drives. Ask Tom, Docs, TomTube, and Bookmarks each
have their own input where relevant, so the top bar is hidden on
those tabs to avoid the impression of a no-op input.

### 5.1 Ask Tom

A chat interface grounded in the indexed corpus. Each question is
answered by retrieving the most relevant Discord conversation windows,
PDF pages, and YouTube transcript segments, then passing them to an
AI model (Gemini by default, Ollama as fallback) with instructions
to cite every claim. When the retrieval finds material in all three
source types, the answer must include at least one citation from each.

Features:

- `[msg:...]`, `[doc:...]`, and `[vid:...]` citations are clickable
  and jump to the source. `[vid:...]` opens YouTube at the exact
  timestamp. `[msg:...]` opens a popover; from there **Show in
  timeline** scrolls the Feed to the message — even when it pre-dates
  the Feed's recent-history window (Tom's Lab loads a windowed view
  around the target so older posts are reachable).
- **★ Tom** toggle restricts retrieval to Tom-authored content. The
  star indicates the active state.
- **Videos only** and **Discord only** toggles redirect the entire
  retrieval budget at one source type when needed.
- **Sources: oldest first / newest first** flips both the source list
  AND the order in which the answer body presents the evidence — so
  a "show me how Tom's thinking evolved" question reads chronologically
  when oldest-first is selected.
- Each answer can be saved as a bookmark via the star icon.
- **Try one:** the suggested prompts on the empty-state landing are
  curated to questions whose answers are grounded in Tom's authored
  PDFs.

### 5.2 Gallery

A grid of every indexed chart, ranked by either chronology or visual
similarity to a query.

- **Keyword** mode ranks by text hits on filename and caption.
- **Visual** mode uses CLIP image-text embeddings — type a text query
  and the most visually-similar charts surface first.
- Clicking a chart jumps to its message in the Feed. Right-click a
  chart to open it in the system image viewer.
- Scroll performance is cached; first load of a new query may be
  slow on large corpora.

### 5.3 Feed

The raw Discord view, styled after the original channel. Tom B's
messages carry a gold accent. Inline chart thumbnails expand on click.

- The top search bar drives the Feed when this tab is active —
  Keyword / Semantic / Visual mode dropdown applies here.
- The **Hide noise** toggle suppresses one-word replies, reaction
  emojis, and other low-signal messages.
- Right-clicking an author's avatar gives the option to filter to
  that author or add them to **Favorites**.
- **Show in timeline** (from any clickable Discord citation in Ask
  Tom or in the Bookmarks tab) lands you on the exact post even if
  it's far older than the most recent thousand messages.

### 5.4 Docs

Reference PDFs rendered page by page. Text is extracted via pdfplumber
and — for image-only pages — EasyOCR. Each page is text-embedded and
image-embedded, so it surfaces in Ask Tom citations and Gallery
searches.

### 5.5 TomTube

YouTube videos ingested from Tom's public uploads. Each video is
transcribed with Whisper and chunked into ~90-second semantic windows.
Clicking a transcript segment opens YouTube at the exact timestamp.

### 5.6 Bookmarks

Messages and chat answers the user has explicitly starred. Personal
only — bookmarks are cleared in shipped data packs.

---

## 6. Data management

### 6.1 Importing a Discord export

1. Produce a JSON export with Discord Chat Exporter (DCE) on the
   `traders-lab-tom-b` channel. DCE is a third-party tool; its use is
   governed by Discord's Terms of Service and is the user's
   responsibility.
2. In Tom's Lab: **File → Import → Discord export (DCE JSON)…**
3. Select the JSON file. The attached `_Files` folder must be in the
   same directory.
4. Import is resumable — re-selecting the same JSON only adds new
   messages.

### 6.2 Importing YouTube videos from a folder (recommended)

Reliable path that does not depend on YouTube's anti-bot defences.

1. Download the videos with any working tool of your choice.
2. Rename each file so the 11-character YouTube video id is in
   brackets, for example `Daily Recap [abcdef12345].mp3`.
3. In Tom's Lab: **File → Import → YouTube videos from a folder…**
4. Select the folder. Every recognised file is queued for
   transcription.

### 6.3 Importing YouTube directly (experimental)

An in-app yt-dlp pipeline is available for users willing to maintain a
Node.js + bgutil proof-of-token setup. It breaks frequently as
YouTube's anti-bot system tightens. The folder-import path above is
the recommended default.

### 6.4 Checking for new Tom videos

**File → Import → Check for new Tom videos** asks YouTube whether any
new uploads match the configured title filter. Enumeration runs in a
background thread so the window remains responsive.

### 6.5 Importing reference PDFs

Drop PDFs into the `tom_docs/` folder next to the application or use
the **Import PDF** dialog. PDFs are rendered, OCR'd, and embedded
automatically. Copyright remains with the original authors; the user
is responsible for ensuring their use respects applicable law.

### 6.6 Building embeddings

After any import:

1. **File → Process → Build text embeddings…**
2. **File → Process → Build image (CLIP) embeddings…**

Embeddings require Ollama (text) and CLIP (image) to be available.
Both run in background workers and can be stopped by closing the
application.

### 6.7 Classifying charts

**File → Process → Classify Discord images…** separates real charts
from memes, thumbnails, and low-signal screenshots using a two-stage
filter (fast rules, then CLIP zero-shot scoring). Decisions are
reversible — nothing is deleted.

### 6.8 Reviewing and purging

- **File → Review → Review chart classifications…** shows all flagged
  images for manual override.
- **File → Review → Purge discarded images from disk…** moves flagged
  images to a `_discarded/` sibling folder. Files are not hard-
  deleted; the user can empty the folder themselves to reclaim disk.

### 6.9 Installing a new data pack

See section 3.2. Installing a pack always backs up the current data
directory first.

**Personal data is preserved across pack updates.** When a new pack is
installed over an existing one, Tom's Lab automatically copies the
user's **bookmarks** (saved messages and Ask Tom answers) and
**favorite authors** from the previous database into the new one.
Only bookmarks whose referenced message still exists in the new pack
are carried over; any dangling references are dropped.

**Not preserved:** Ask Tom chat history. Chat history is a rolling
transient log and is cleared on every pack update.

The installed-pack confirmation dialog reports how many bookmarks and
favorites were carried over.

---

## 7. Favorites

Right-clicking an author's avatar in the Feed offers **Save as
favorite**. Favorites are personal; they are cleared in shipped data
packs. The set of favorites is visible via the Feed filter bar.

---

## 8. Daily Study

**Study → Today's concept** picks one concept from Tom's glossary and
presents a short reading drill based on the relevant corpus material.
Intended as a 5-minute daily refresher.

---

## 9. Settings

**File → Settings…** exposes:

- AI provider assignment (embed, chat, vision) and model selection.
- Gemini API key.
- Whisper model tier for transcription (`distil-large-v3` default).
- YouTube channel and title filter for the new-videos check.
- Feed noise filter.
- Chat retrieval budgets (how many Tom messages, how many community
  messages, how many PDF pages, how many video chunks per turn).

---

## 10. Troubleshooting

### 10.1 The window shows "Not Responding"

A background task is holding the UI thread. Wait for it to finish; the
Jobs panel (bottom-right) shows what is running. If nothing is
running and the window stays frozen for more than a minute, close and
relaunch the application.

### 10.2 The app will not start

- Check `%LOCALAPPDATA%\TomsLab\logs\tomslab.log` for the last error.
- On a first install, the most common cause is a corrupted
  `tomslab.db`. Rename the `data/` folder to `data-broken/` and
  install a fresh data pack.

### 10.3 Ask Tom returns an error

- If the error mentions a Gemini quota, wait and retry, or configure
  Ollama as the chat provider in Settings.
- If the error mentions no embeddings, run **File → Process → Build
  text embeddings…** first.
- If the error mentions no API key, paste a Gemini key in Settings.

### 10.4 Gallery visual search returns nothing

Run **File → Process → Build image (CLIP) embeddings…** once. CLIP
embeddings are built on demand and persist across sessions.

### 10.5 YouTube direct download fails

Expected. Use **File → Import → YouTube videos from a folder…**
instead. The experimental direct-YouTube path depends on external
tools that YouTube routinely breaks.

### 10.6 Data pack install fails

The application automatically rolls back to the previous data
directory. Re-download the pack — a corrupted download is the most
common cause. Verify the SHA-256 against the release notes before
retrying.

### 10.7 Discord export file is too large to open

Exports are streamed — there is no file-size limit in Tom's Lab
itself. If the import hangs at 0% for more than a minute, the export
is probably malformed. Re-run DCE to produce a fresh JSON.

### 10.8 "Database is locked" errors

Another instance of Tom's Lab is already running. Close it.

---

## 11. File locations

| Path | Contents |
|---|---|
| `%LOCALAPPDATA%\TomsLab\data\tomslab.db` | Main database. |
| `%LOCALAPPDATA%\TomsLab\data\charts\` | Chart images, installed from a data pack. |
| `%LOCALAPPDATA%\TomsLab\data\doc_images\` | Rendered PDF pages. |
| `%LOCALAPPDATA%\TomsLab\data\videos\` | YouTube audio (kept so future Whisper upgrades can re-transcribe without re-downloading). |
| `%LOCALAPPDATA%\TomsLab\data.backup-*\` | Automatic backups created before each data-pack install. Safe to delete when the new pack is confirmed working. |
| `%LOCALAPPDATA%\TomsLab\logs\tomslab.log` | Rolling log file. |

Setting the `TOMSLAB_DATA_DIR` environment variable redirects the
whole `data/` tree to a custom location — useful for keeping the
corpus on a separate drive.

---

## 12. Uninstall

1. Use **Settings → Apps → Installed apps** in Windows to remove the
   Tom's Lab application itself.
2. Delete `%LOCALAPPDATA%\TomsLab\` to remove all user data,
   downloaded audio, logs, and backups.

---

## 13. Credits and trademarks

Tom's Lab is developed and published by **SDE-Software (SDES.DEV)**.

Bookmap™ is a trademark of Bookmap Ltd. and is referenced solely to
describe subject-matter context. Discord™ is a trademark of Discord
Inc. YouTube™ is a trademark of Google LLC. All other trademarks are
the property of their respective owners.
