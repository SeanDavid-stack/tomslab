"""SQLite schema and connection helpers.

Mirrors PRD §6 data model. Vector-search virtual tables (sqlite-vss) are
intentionally NOT created in Phase 1 — they'll be added in Phase 3 once
the extension is wired up. All other tables are created up front so later
phases don't need migrations.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tomslab.paths import database_path

SCHEMA = """
-- ---- messages --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT,
    channel_name TEXT,
    guild_id TEXT,
    guild_name TEXT,
    author_id TEXT,
    author_name TEXT,
    author_nickname TEXT,
    timestamp TEXT,
    timestamp_edited TEXT,
    content TEXT,
    reply_to_message_id TEXT,
    is_pinned INTEGER DEFAULT 0,
    is_featured_speaker INTEGER DEFAULT 0,
    raw_json TEXT,
    imported_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_author    ON messages(author_name);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_channel   ON messages(channel_id);
CREATE INDEX IF NOT EXISTS idx_messages_featured  ON messages(is_featured_speaker);

-- ---- attachments -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    filename TEXT,
    local_path TEXT,
    url_original TEXT,
    content_type TEXT,
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    content_hash TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attachments_hash    ON attachments(content_hash);

-- ---- conversation windows (the "chunks" we'll later embed) -----------------
CREATE TABLE IF NOT EXISTS conversation_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anchor_message_id TEXT,
    start_message_id TEXT,
    end_message_id TEXT,
    text_combined TEXT,
    image_count INTEGER DEFAULT 0,
    FOREIGN KEY (anchor_message_id) REFERENCES messages(id)
);
CREATE INDEX IF NOT EXISTS idx_windows_anchor ON conversation_windows(anchor_message_id);

-- ---- concepts (populated Phase 6) ------------------------------------------
CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    extracted_at TEXT
);
CREATE TABLE IF NOT EXISTS message_concepts (
    message_id TEXT,
    concept_id INTEGER,
    confidence REAL,
    PRIMARY KEY (message_id, concept_id)
);

-- ---- image descriptions (populated Phase 6) --------------------------------
CREATE TABLE IF NOT EXISTS image_descriptions (
    attachment_id TEXT PRIMARY KEY,
    description TEXT,
    ocr_text TEXT,
    extracted_ticker TEXT,
    extracted_timeframe TEXT,
    model_used TEXT,
    generated_at TEXT,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id)
);

-- ---- bookmarks (populated Phase 7) -----------------------------------------
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT,
    note TEXT,
    tags TEXT,
    created_at TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

-- ---- dedup / relations (populated Phase 6) ---------------------------------
CREATE TABLE IF NOT EXISTS message_relations (
    source_message_id TEXT,
    related_message_id TEXT,
    relation_type TEXT,
    similarity_score REAL,
    detected_at TEXT,
    user_confirmed INTEGER DEFAULT NULL,
    PRIMARY KEY (source_message_id, related_message_id, relation_type)
);

CREATE TABLE IF NOT EXISTS semantic_clusters (
    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    representative_message_id TEXT,
    concept_summary TEXT,
    member_count INTEGER,
    earliest_message_date TEXT,
    latest_message_date TEXT
);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id INTEGER,
    message_id TEXT,
    PRIMARY KEY (cluster_id, message_id)
);

-- ---- app configuration -----------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- ---- imports log (so we can resume / show history) -------------------------
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT,
    guild_id TEXT,
    channel_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    messages_added INTEGER DEFAULT 0,
    messages_skipped INTEGER DEFAULT 0,
    attachments_added INTEGER DEFAULT 0
);

-- ---- Window embeddings (Phase 3) -------------------------------------------
-- One row per conversation_window. The embedding is stored as raw float32
-- bytes — for the corpus size we care about (~18K rows) a plain numpy
-- cosine sweep is <100ms, so sqlite-vss / sqlite-vec is unnecessary weight.
CREATE TABLE IF NOT EXISTS window_embeddings (
    window_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    generated_at TEXT,
    FOREIGN KEY (window_id) REFERENCES conversation_windows(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_window_embeddings_model ON window_embeddings(model);

-- ---- FTS5 keyword index (Phase 2) ------------------------------------------
-- Contentless-external: we manage inserts explicitly so we can index both
-- the display name and the message text in a single row.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    id UNINDEXED,
    content,
    author_name,
    author_nickname,
    tokenize = "porter unicode61 remove_diacritics 2"
);
"""

# Phase-1 defaults — set once on fresh DBs, never overwritten.
DEFAULT_SETTINGS: dict[str, str] = {
    "featured_speaker_username": "tom_b_trades",
    "conversation_window_before": "3",
    "conversation_window_after": "5",
    # Phase 3 defaults: Ollama for bulk embedding (no rate limits, local),
    # leaving Gemini available in the same provider layer for chat later.
    "ai_provider_embed": "ollama",
    "ai_provider_chat": "gemini",
    "ai_provider_vision": "ollama",
    "embed_model_ollama": "nomic-embed-text",
    "embed_model_gemini": "gemini-embedding-001",
    "chat_model_ollama": "llama3.1:8b",
    "chat_model_gemini": "gemini-2.5-flash",
    "vision_model_ollama": "llava:13b",
    "schema_version": "1",
}


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the tomslab SQLite database."""
    db_path = path or database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def initialise(conn: sqlite3.Connection) -> None:
    """Create tables + seed default settings. Idempotent."""
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()
    _backfill_fts(conn)


def _backfill_fts(conn: sqlite3.Connection) -> None:
    """Populate the FTS5 index for any messages that aren't in it yet.

    Runs once on first launch after Phase 2 is deployed against an existing
    database; subsequent imports add rows incrementally via the importer.
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
    msg_count = int(row["n"] or 0)
    row = conn.execute("SELECT COUNT(*) AS n FROM messages_fts").fetchone()
    fts_count = int(row["n"] or 0)
    if fts_count >= msg_count or msg_count == 0:
        return

    conn.execute("DELETE FROM messages_fts")
    conn.execute(
        """
        INSERT INTO messages_fts(id, content, author_name, author_nickname)
        SELECT id,
               COALESCE(content, ''),
               COALESCE(author_name, ''),
               COALESCE(author_nickname, '')
          FROM messages
        """
    )
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


@contextmanager
def open_db(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        initialise(conn)
        yield conn
    finally:
        conn.close()
