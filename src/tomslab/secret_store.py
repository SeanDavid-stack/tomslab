"""API-key storage with a machine-bound XOR mask.

Per PRD §14, secrets in the database should not be plaintext.  This is
obfuscation, not strong cryptography — the threat model is "someone
glancing at the sqlite file can't grep for AIzaSy...", not "the DB file
leaks to an attacker who also has the same machine".

If we ever need real key protection we should move to the Windows
Credential Manager via `keyring`.  XOR keeps the single-file app story
and avoids yet another PyInstaller dep.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import socket
import sqlite3

_PREFIX = "xor1:"  # version tag so we can roll the scheme later


def _mask() -> bytes:
    salt = f"{socket.gethostname()}|{getpass.getuser()}|tomslab".encode("utf-8")
    return hashlib.sha256(salt).digest()   # 32 bytes


def _xor(data: bytes, mask: bytes) -> bytes:
    if not mask:
        return data
    return bytes(b ^ mask[i % len(mask)] for i, b in enumerate(data))


def encode(plaintext: str) -> str:
    if plaintext is None:
        return ""
    raw = plaintext.encode("utf-8")
    return _PREFIX + base64.b64encode(_xor(raw, _mask())).decode("ascii")


def decode(stored: str | None) -> str:
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        # legacy / plaintext — return as-is so migrations don't break
        return stored
    payload = stored[len(_PREFIX) :]
    try:
        raw = base64.b64decode(payload.encode("ascii"))
    except Exception:
        return ""
    return _xor(raw, _mask()).decode("utf-8", errors="replace")


# ---- sqlite helpers --------------------------------------------------------
def store_api_key(conn: sqlite3.Connection, provider: str, api_key: str) -> None:
    key = f"api_key_{provider}"
    value = encode(api_key) if api_key else ""
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def load_api_key(conn: sqlite3.Connection, provider: str) -> str:
    key = f"api_key_{provider}"
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return decode(row["value"]) if row and row["value"] else ""
