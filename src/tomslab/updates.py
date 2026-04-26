"""In-app update checker.

Fetches a tiny JSON manifest from GitHub and compares it against the
bundled ``__version__``. No auto-download, no auto-install — the goal is
just to let the user know when Tom has published a newer build, so they
can grab the installer manually.

Design constraints:
  * Stdlib only. The app already carries enough wheels; one more network
    helper does not justify ``requests`` or ``httpx``.
  * Silent on failure. If GitHub is unreachable or returns garbage we
    return ``None`` — no toast, no log spam, no modal. Update checks
    must never get in the user's way.
  * Policy-compliant: free-utility, no-support. We surface "an update
    exists" but never claim it's required, safe, or supported.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from tomslab import __version__
from tomslab import db as dbmod

DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/SDES-Software/tomslab/main/latest.json"
)
_TIMEOUT_SECONDS = 5
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # once per day
_USER_AGENT = f"TomsLab/{__version__} (+https://github.com/SDES-Software/tomslab)"


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    url: str
    notes: str
    released: str
    is_newer: bool


# ----------------------------------------------------------------------
# version compare
# ----------------------------------------------------------------------
def _parse_version(v: str) -> tuple:
    """Return a comparable tuple for *v*.

    Uses ``packaging.version`` when available (it is a transitive dep of
    pip and therefore almost certainly installed). Falls back to a plain
    numeric-split so this module still works on a stripped venv.
    """
    try:
        from packaging.version import parse as _pkg_parse  # type: ignore

        return (_pkg_parse(v),)
    except Exception:
        parts = []
        for chunk in (v or "0").strip().lstrip("vV").split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    try:
        return _parse_version(latest) > _parse_version(current)
    except Exception:
        return False


# ----------------------------------------------------------------------
# network
# ----------------------------------------------------------------------
def _fetch_manifest(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
        data = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _coerce(data: dict) -> Optional[UpdateInfo]:
    latest = str(data.get("version", "")).strip()
    if not latest:
        return None
    return UpdateInfo(
        current_version=__version__,
        latest_version=latest,
        url=str(data.get("url", "")).strip(),
        notes=str(data.get("notes", "")).strip(),
        released=str(data.get("released", "")).strip(),
        is_newer=_is_newer(latest, __version__),
    )


# ----------------------------------------------------------------------
# public entry points
# ----------------------------------------------------------------------
def check_for_update(conn: sqlite3.Connection) -> Optional[UpdateInfo]:
    """Synchronous fetch + parse. Never raises on network failure."""
    url = dbmod.get_setting(conn, "update_check_url", DEFAULT_MANIFEST_URL) \
        or DEFAULT_MANIFEST_URL
    data = _fetch_manifest(url)
    if data is None:
        return None
    info = _coerce(data)
    if info is None:
        return None
    # Record the timestamp so the throttle works even if we're up-to-date.
    dbmod.set_setting(
        conn, "update_last_checked_at",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return info


def should_auto_check(conn: sqlite3.Connection) -> bool:
    """Respect the enabled-flag and the 24h throttle."""
    if (dbmod.get_setting(conn, "update_check_enabled", "1") or "1") == "0":
        return False
    last = dbmod.get_setting(conn, "update_last_checked_at", "")
    if not last:
        return True
    try:
        # fromisoformat accepts the "+00:00" suffix on 3.11+; on older
        # Pythons we just treat a parse error as "time to check".
        when = datetime.fromisoformat(last)
    except ValueError:
        return True
    now = datetime.now(when.tzinfo or timezone.utc)
    return (now - when).total_seconds() >= _CHECK_INTERVAL_SECONDS


def mark_version_notified(conn: sqlite3.Connection, version: str) -> None:
    """Record that we've already told the user about *version* so we
    don't toast them again on every subsequent launch."""
    dbmod.set_setting(conn, "update_available_version", version or "")


def already_notified_for(conn: sqlite3.Connection, version: str) -> bool:
    seen = dbmod.get_setting(conn, "update_available_version", "") or ""
    return seen.strip() == (version or "").strip()


def set_auto_check_enabled(conn: sqlite3.Connection, enabled: bool) -> None:
    dbmod.set_setting(conn, "update_check_enabled", "1" if enabled else "0")


def get_auto_check_enabled(conn: sqlite3.Connection) -> bool:
    return (dbmod.get_setting(conn, "update_check_enabled", "1") or "1") != "0"


def get_manifest_url(conn: sqlite3.Connection) -> str:
    return dbmod.get_setting(conn, "update_check_url", DEFAULT_MANIFEST_URL) \
        or DEFAULT_MANIFEST_URL


def set_manifest_url(conn: sqlite3.Connection, url: str) -> None:
    dbmod.set_setting(
        conn, "update_check_url",
        (url or DEFAULT_MANIFEST_URL).strip() or DEFAULT_MANIFEST_URL,
    )
