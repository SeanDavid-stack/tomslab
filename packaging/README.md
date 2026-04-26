# Tom's Lab packaging

This folder holds the build inputs (icon) and notes for turning the source
tree into a shippable Windows app. Phase 1 produces a frozen one-folder
bundle via PyInstaller. Phase 2 wraps it in a single `.exe` installer,
bundles a pre-built DB, and checks for Ollama on first launch.

## Current Phase 1 status

- Entry: `D:\Toms Lab\tomslab.spec` (root of repo)
- Build driver: `build.ps1` (root of repo)
- Output: `dist\tomslab\tomslab.exe`
- On-disk size: **~5.2 GB** (torch + CUDA DLLs dominate — see below)
- Build time: ~8 minutes on this machine (cold), ~3 min with warm cache

## Build

From the repo root in PowerShell:

```powershell
.\build.ps1             # build only
.\build.ps1 -Run        # build, then launch the packaged exe
.\build.ps1 -Clean      # wipe dist/ and build/ first
```

`build.ps1` shells out to `.\.venv\Scripts\python.exe -m PyInstaller --clean tomslab.spec`.
It installs PyInstaller into the venv if missing.

## Regenerating the spec when deps change

The spec relies on PyInstaller's `collect_all` helper for every heavy
package. That means most new dependencies (anything without C extensions
or non-Python data files) will be picked up automatically by import-graph
analysis — no spec edit needed.

Edit `tomslab.spec` when you add a package that:

- Ships **C extensions / native DLLs** — add to `COLLECT_ALL_PACKAGES`.
- Ships **data files** that are loaded via `__file__`-relative paths
  (JSONs, model configs, vocab blobs, shaders, schemas) — add to
  `COLLECT_ALL_PACKAGES` so `collect_all` picks them up.
- Is imported **only by string** (e.g. `importlib.import_module(name)`),
  dynamically — add to `hiddenimports`.
- Drags in large unused transitive deps — add them to `excludes` so they
  don't inflate the bundle.

After editing, run `.\build.ps1 -Clean` so stale PYZ/PKG caches are wiped.

## Known PyInstaller issues on Windows

### 1. `cudnn_ops64_9.dll` / `nvrtc64_120_0.dll` — CUDA DLL loading

PyInstaller's static analysis doesn't see `torch.*` CUDA DLLs; we rely on
`collect_all("torch")` to grab them. On Windows you may still hit
`OSError: [WinError 126]` when `torch.cuda.is_available()` or
`torch.cuda.init()` runs, because Windows' DLL loader can't find cuDNN's
dependencies even when they're sitting in the same folder.

**Workarounds tried / candidates:**

- Ensure `dist\tomslab\_internal\torch\lib\*.dll` exists after build (it
  does). PyInstaller 6.x places all `collect_all` binaries under
  `_internal\` in one-folder mode.
- If loading fails at runtime, add the torch lib dir to the DLL search
  path explicitly at startup (before `import torch`):
  ```python
  os.add_dll_directory(str(Path(sys.prefix) / "torch" / "lib"))
  ```
  — but we haven't needed this yet because torch is lazy-imported from
  a worker thread that inherits the correct search path.
- `torch.distributed.elastic` pulls in `/usr/lib64/libgomp.so.1` via
  ctypes — harmless warning, PyInstaller ignores it.

### 2. UPX breaks torch DLLs

`upx=False` in the spec is deliberate. UPX-compressed CUDA DLLs crash on
load with no useful error. Never turn this on.

### 3. `anthropic` hidden import warning

The spec lists `anthropic` as a hidden import so a future `pip install
anthropic` gets included without a spec edit. Today it's not installed,
so PyInstaller logs `ERROR: Hidden import 'anthropic' not found` — that's
expected and non-fatal.

### 4. `console=False` hides stderr

The spec sets `console=False` because this is a GUI app and a black
console window would flash on every launch. The tradeoff is that
`print()` / unhandled exceptions disappear. To debug a packaged build,
temporarily flip `console=True` in `tomslab.spec` and rebuild; you'll
get a console attached to the main window that captures all stderr.

### 5. `imageio-ffmpeg` binary placement

`imageio_ffmpeg.get_ffmpeg_exe()` walks up from `__file__` looking for
`binaries/ffmpeg-win-*.exe`. We ship the binary explicitly at
`imageio_ffmpeg/binaries/` so the package's own resolver works unchanged
at runtime.

### 6. `platformdirs.user_data_dir` under Windows AppContainer

Tom's Lab defaults to `%LOCALAPPDATA%\TomsLab` for data. Because Claude
Code runs inside a Windows AppContainer, writes from *my* processes get
redirected to an app-private overlay (and so the user never sees them).
The packaged app run by the user is NOT in an AppContainer, so it writes
to the real `%LOCALAPPDATA%\TomsLab` — no fix needed, just noting for
anyone debugging "where did the DB go".

### 7. One-folder vs one-file

Phase 1 is **one-folder**. `--onefile` extracts to a temp dir on every
launch; with a 5 GB payload that's ~10 seconds of cold-start lag and a
second copy on disk while running. The eventual Inno Setup installer
(Phase 2) produces a single `.exe` file for distribution anyway, so
one-folder loses nothing.

## Shipping a data pack

Tom's Lab's binary (the PyInstaller `.exe`) ships the **app**, not the
**data**. A user who installs `TomsLab-Setup-1.2.0.exe` gets an empty
corpus — no messages, no charts, no embeddings — and would otherwise
need to run the full ingest pipeline (import DCE JSON, classify, embed,
ingest YouTube, etc.) which takes hours and requires local Ollama +
Gemini keys. That's not a reasonable first-run.

To avoid it, the PM builds a **data pack** on their own machine and
ships it as a separate GitHub release asset. Users download it and
install it with `File → Install data pack…` in the app. One `.tar.zst`
snapshot → a fully-loaded Tom's Lab in a few minutes.

### 1. Build the pack on the PM machine

Prerequisites: the PM has already run ingest, classified images, built
embeddings, optionally purged discard-flagged charts, and (importantly)
sanity-checked Ask Tom answer quality before shipping.

```powershell
.\.venv\Scripts\python.exe packaging\build_data_pack.py `
    --data-dir "D:\Toms Lab\data" `
    --out-dir  "D:\Toms Lab\dist" `
    --app-version 1.2.0
```

Arguments:

- `--data-dir` — the populated data folder. Defaults to
  `$TOMSLAB_DATA_DIR` then `./data`. On the PM's machine this is
  `D:\Toms Lab\data` per the data-on-D migration.
- `--out-dir` — where `tomslab-data-<date>.tar.zst` and
  `tomslab-data-<date>.manifest.json` are written. Default `./dist`.
- `--app-version` — the minimum app version this pack is compatible
  with. Write it into the manifest so old clients refuse bad packs.
- `--release-date` (optional) — pin the filename's date. Default is
  today's UTC date.

What the script does:

1. Copies `tomslab.db` to a temp location, runs `VACUUM`, then
   clears `chat_history`, `bookmarks`, and `imports` (PM-private),
   then `VACUUM`s again to reclaim the pages.
2. Walks every keeper attachment (`chart_decision IN ('keep',
   'auto_keep')` or NULL) and re-encodes PNG/JPG/BMP to **WebP q85**.
   `.webp` and `.gif` files are passed through. Discard-flagged
   attachments are dropped entirely from the pack (and their
   `local_path` is cleared in the shipped DB).
3. Does the same for `document_pages.rendered_path`.
4. Rewrites path columns to a sentinel form (`{DATA}/charts/<shard>/
   <id>.webp`) so the user's install step can retarget them to their
   local `data_dir()`.
5. Streams everything into `tomslab-data-<date>.tar.zst` at zstd
   level 19 (pure-Python `zstandard` — no external libs).
6. Writes a sibling `.manifest.json` with version, release date,
   sizes, SHA-256, and table counts. Paste the manifest block into
   the GitHub release notes.

Re-running the script on the same data folder is safe — already-WebP
images are passed through, and a DB copy is always made so the
original is never mutated.

### 2. What the PM should verify before uploading

- **Answer quality.** Ask Tom a handful of questions the corpus should
  answer crisply ("what is VPOC?", "how does Tom use IBL?", a
  daily-recap question). If citations look wrong, the pack is not
  ready to ship.
- **Data integrity.** Open the pack's manifest and confirm `counts`
  look sane: roughly as many messages as you imported, embeddings
  in the tens of thousands, videos transcribed.
- **Disk sanity.** Raw / compressed sizes printed at the end should
  be in the expected band (8 GB → ~2–3 GB compressed). A wildly
  smaller pack usually means the PNG→WebP step silently failed; a
  wildly larger one means discards weren't purged.
- **Sentinel pass.** Quickly grep a dump of the packed DB for
  `D:\Toms Lab` (or any PM-machine-specific path) — there should
  be none. Every ``local_path``/``rendered_path`` should start with
  ``{DATA}/``.

### 3. Publish the release (split hosting)

GitHub release-asset uploads are capped at **2 GB per file**. A real
Tom's Lab data pack is ~2–3 GB, so we split hosting:

- **GitHub Releases** — the app installer and the manifest.
- **Google Drive** — the `.tar.zst` data pack itself.

Why this split: the installer is small (tens of MB) and belongs next
to the source tree where version-tagging, release notes, and code-
signing live. The data pack is big, changes independently of the
source code, and benefits from a host that doesn't charge egress.

**Steps per release:**

1. On GitHub, tag the release (e.g. `v1.2.0`) and upload as release
   assets:
   - `TomsLab-Setup-1.2.0.exe` (PyInstaller + Inno Setup output)
   - `tomslab-data-<date>.manifest.json` (tiny — size + SHA-256 +
     counts, so users can verify the Drive download).
2. On Google Drive, upload `tomslab-data-<date>.tar.zst` and share
   it "Anyone with the link → Viewer". When you ship a new pack,
   use Drive's *Replace* (right-click the existing file → Manage
   versions → Upload new version) so the URL stays stable.
3. In the GitHub release notes, include:
   - A link to the Google Drive download page (the plain
     `https://drive.google.com/file/d/…/view` URL — don't try to
     construct a direct-download `uc?export=download&id=` link,
     they stop working on large files).
   - The manifest JSON block, pasted inline, so users see size +
     SHA-256 + built-at without having to download the 2.5 GB pack.
   - A note: *"Google Drive will show a 'can't scan for viruses'
     page for files this large. Click **Download anyway** — verify
     the SHA-256 below matches after download."*

The app's Check-for-updates flow doesn't auto-install data packs —
users re-download and re-install via `File → Install data pack…`
when a new corpus drops. This keeps the bandwidth cost opt-in.

**When to consider moving off Drive:** if you end up shipping data
packs more than about once a month, Hugging Face's datasets hosting
is a better long-term fit — free, CDN-backed, no "can't scan"
friction, no 2 GB limit, and purpose-built for distributing large
data artifacts. Migration is a one-time move: upload the `.tar.zst`
to a public HF dataset repo and swap the Drive link in the release
notes for the HF download URL. The in-app install flow doesn't care
where the file came from — it reads a local `.tar.zst` the user
picked in the file dialog.

### 4. How users install a pack

1. Download the `.tar.zst` from the GitHub release.
2. Launch Tom's Lab.
3. `File → Install data pack…`.
4. Pick the downloaded file. The app shows the pack's release date
   and size, confirms, then:
   - stops background workers,
   - closes the DB,
   - renames the current `data/` to `data.backup-<timestamp>/`,
   - extracts the archive into a fresh `data/`,
   - rewrites `{DATA}` sentinels to the user's real data path,
   - re-opens the DB and reloads every tab.
5. If anything goes wrong, the app rolls back — the backup folder is
   renamed back to `data/` and the user sees their old corpus.

The install is backup-first, never destructive. Users who want to
reclaim disk after verifying the new pack works can delete the
`data.backup-*` folders themselves from File Explorer.

## What Phase 2 will add

- **Inno Setup wrapper** (`packaging\tomslab.iss`): produces a signed
  `TomsLab-Setup-x.y.z.exe` that installs to `%PROGRAMFILES%\TomsLab`,
  creates a Start Menu entry, registers an uninstaller, and copies
  a pre-built read-only DB into `%LOCALAPPDATA%\TomsLab\data\` on
  first launch (or merges if one already exists).
- **Bundled DB**: ship `data\tomslab.db` already ingested so the app is
  useful on first launch without a 30-min ingest wait.
- **First-launch Ollama check**: a `FirstRunWizard` step that detects
  Ollama via `http://127.0.0.1:11434/api/tags`, offers to open the
  Ollama installer page if absent, and pre-pulls the default model.
- **Code signing**: Authenticode signature on both the PyInstaller
  `tomslab.exe` and the outer Inno Setup installer. Required to avoid
  SmartScreen warnings. Uses SDE-Software's code-signing cert (once we
  have one).
- **Auto-updater**: `packaging\latest.json.example` is the seed for a
  simple "phone home → download new installer" flow. See that file for
  the expected schema.
- **Clean-machine test**: run the installer on a fresh Windows VM with
  no Python / no CUDA driver pre-installed, confirm the app starts
  (AI features off — they require local Ollama + optional CUDA driver).

## Icon

`packaging\icon.ico` is a placeholder — a dark rounded square with a
gold "TL" wordmark. Replace with a real Tom's Lab icon when the brand is
nailed down. Multi-size (16/32/48/64/128/256) PNG-in-ICO so Windows
Explorer renders it cleanly at every tile size.

## Troubleshooting a broken build

If the packaged exe crashes silently at launch:

1. Temporarily set `console=True` in `tomslab.spec`, rebuild, re-run.
2. Check `%LOCALAPPDATA%\TomsLab\logs\tomslab.log` — our own log is
   written before any heavy imports so even a hang will usually show
   the "Tom's Lab starting" line.
3. Read `dist\tomslab\last-stderr.log` if the exe was launched via
   `build.ps1 -Run`.
4. Inspect `build\tomslab\warn-tomslab.txt` — PyInstaller lists every
   hidden-import and data-file warning it hit during analysis.
