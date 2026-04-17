"""Runtime filesystem layout for Tom's Lab.

Per PRD §6 the app lives under %APPDATA%/TomsLab/ on Windows. On other
platforms platformdirs picks the equivalent user-data directory.
"""
from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir

from tomslab import __app_name__

APP_DIR_NAME = "TomsLab"


def app_root() -> Path:
    """Return the per-user app data root (created on first access)."""
    root = Path(user_data_dir(APP_DIR_NAME, appauthor=False))
    root.mkdir(parents=True, exist_ok=True)
    return root


def data_dir() -> Path:
    d = app_root() / "data"
    d.mkdir(exist_ok=True)
    return d


def assets_dir() -> Path:
    d = data_dir() / "_assets"
    d.mkdir(exist_ok=True)
    return d


def logs_dir() -> Path:
    d = app_root() / "logs"
    d.mkdir(exist_ok=True)
    return d


def exports_dir() -> Path:
    d = app_root() / "exports"
    d.mkdir(exist_ok=True)
    return d


def database_path() -> Path:
    return data_dir() / "tomslab.db"


def log_path() -> Path:
    return logs_dir() / "tomslab.log"
