# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Tom's Lab (Phase 1: one-folder desktop build).

Regenerate notes (if deps change):
  - Bump or add package tuples in COLLECT_ALL_PACKAGES
  - Run `.\build.ps1` from repo root (activates .venv, invokes pyinstaller --clean)
  - Check `packaging/README.md` for known issues, esp. around torch CUDA DLLs

Phase 1 deliberately one-folder (not --onefile):
  * Launch is much faster (no extract-to-temp on every run)
  * CUDA DLL loading bugs are easier to diagnose when you can cd into dist/
  * Phase 2 (Inno Setup wrapper) will produce a single installer regardless

The app lazy-imports torch / faster_whisper / open_clip inside worker
functions, so the main window opens without touching CUDA. PyInstaller
walks the import graph statically though, so every lazy dep still gets
collected — that's why the bundle is ~5 GB.
"""
from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(SPECPATH).resolve()
SRC_DIR   = REPO_ROOT / "src"
ENTRY     = SRC_DIR / "tomslab" / "main.py"
ICON      = REPO_ROOT / "packaging" / "icon.ico"


# ---------------------------------------------------------------------------
# Heavy deps: collect everything (submodules + data files + DLLs)
# ---------------------------------------------------------------------------
# collect_all() returns (datas, binaries, hiddenimports). For these packages
# we want all three — they have C extensions, JSON config files, vocab blobs,
# and plugin registries that PyInstaller's static analysis misses.
COLLECT_ALL_PACKAGES = [
    "torch",              # ~4.5 GB of CUDA + cuDNN + cuBLAS DLLs
    "torchvision",        # transforms, models
    "faster_whisper",     # transcription pipeline
    "ctranslate2",        # faster_whisper backend (C++ DLLs)
    "open_clip",          # CLIP models + model_configs/*.json + bpe_simple_vocab_16e6.txt.gz
    "tokenizers",         # Rust ext used by faster_whisper
    "easyocr",            # OCR (loads torch, craft model configs)
    "pypdfium2",          # PDF renderer (bundled pdfium DLL)
    "pdfplumber",         # PDF text extraction
    "google.genai",       # Gemini SDK
    "platformdirs",       # small, but has stub resource files
    "ollama",             # HTTP client (tiny, just for safety)
]

datas, binaries, hiddenimports = [], [], []
for pkg in COLLECT_ALL_PACKAGES:
    try:
        d, b, h = collect_all(pkg)
        datas       += d
        binaries    += b
        hiddenimports += h
    except Exception as exc:
        print(f"[tomslab.spec] WARNING collect_all({pkg!r}) failed: {exc}")


# ---------------------------------------------------------------------------
# Hidden imports PyInstaller won't find on its own
# ---------------------------------------------------------------------------
# PyQt6 plugins + QtNetwork/QtSvg are pulled in indirectly by stylesheets
# and pixmap loaders. Hook ships most of this but be explicit.
hiddenimports += [
    "PyQt6.sip",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtNetwork",
    "PyQt6.QtSvg",
    # google-genai historically splits across google.genai.* submodules
    *collect_submodules("google.genai"),
    # anthropic SDK is optional; include if the user installs it later
    "anthropic",
    # sqlite extensions — faster_whisper / easyocr indirectly touch them
    "sqlite3",
]


# ---------------------------------------------------------------------------
# imageio-ffmpeg: ship the bundled ffmpeg.exe next to the app binary so
# imageio_ffmpeg.get_ffmpeg_exe() finds it without a system ffmpeg install.
# ---------------------------------------------------------------------------
try:
    import imageio_ffmpeg  # type: ignore
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    if ffmpeg_exe and os.path.exists(ffmpeg_exe):
        # Place inside the imageio_ffmpeg/binaries/ subdir so the package's
        # own resolver keeps working (it walks up from __file__).
        datas.append((
            ffmpeg_exe,
            "imageio_ffmpeg/binaries",
        ))
except Exception as exc:
    print(f"[tomslab.spec] WARNING imageio-ffmpeg bundle failed: {exc}")


# ---------------------------------------------------------------------------
# Excludes: stop PyInstaller from pulling in megabytes of unused deps
# ---------------------------------------------------------------------------
# These never run in Tom's Lab but can get dragged in by transitive imports
# (e.g. numpy's optional backends, torch.distributed test harnesses).
excludes = [
    "tensorflow",
    "tensorflow_cpu",
    "tensorflow_gpu",
    "keras",
    "jax",
    "jaxlib",
    "matplotlib",       # pandas tries to import lazily
    "scipy.weave",
    "notebook",
    "jupyter",
    "jupyterlab",
    "IPython",
    "ipykernel",
    "pytest",
    "_pytest",
    "sphinx",
    "docutils",
    "torch.distributed.elastic",
    "torch.testing",
    "torchvision.datasets",
    "torchvision.io",   # we don't decode video via torchvision; ffmpeg does it
]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)


# ---------------------------------------------------------------------------
# EXE + COLLECT (one-folder mode)
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tomslab",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX breaks torch DLL loading on Windows
    console=False,             # GUI app — no black console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tomslab",
)
