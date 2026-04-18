"""Runtime filesystem layout for Tom's Lab.

Default: per-user app data dir via platformdirs (%LOCALAPPDATA%/TomsLab
on Windows). Override with the ``TOMSLAB_DATA_DIR`` environment
variable to pin data to a specific folder — useful when the default
resolves to a sandboxed / virtualised path, or when you want data next
to the source tree.
"""
from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

from tomslab import __app_name__

APP_DIR_NAME = "TomsLab"


def _override_data_dir() -> Path | None:
    """Return ``TOMSLAB_DATA_DIR`` as a path if set, else None."""
    v = os.environ.get("TOMSLAB_DATA_DIR")
    return Path(v).expanduser().resolve() if v else None


def app_root() -> Path:
    """Per-user app root (parent of data/, logs/, exports/)."""
    override = _override_data_dir()
    if override:
        # When TOMSLAB_DATA_DIR points at .../data, app_root is its parent.
        root = override.parent if override.name == "data" else override
    else:
        root = Path(user_data_dir(APP_DIR_NAME, appauthor=False))
    root.mkdir(parents=True, exist_ok=True)
    return root


def data_dir() -> Path:
    override = _override_data_dir()
    d = override if override is not None else (app_root() / "data")
    d.mkdir(parents=True, exist_ok=True)
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
