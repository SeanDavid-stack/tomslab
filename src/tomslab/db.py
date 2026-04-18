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

-- ---- Tom-authored & third-party reference documents (PDF corpus) ----------
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    filename TEXT UNIQUE,
    author TEXT,            -- 'tom_b' | 'third_party' | 'unknown'
    doc_type TEXT,          -- 'authoritative' | 'reference'
    source_path TEXT,
    page_count INTEGER,
    added_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_author ON documents(author);

CREATE TABLE IF NOT EXISTS document_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    page_num INTEGER NOT NULL,
    rendered_path TEXT,           -- path to rendered PNG
    extracted_text TEXT,          -- what pdfplumber found (may be empty)
    ocr_text TEXT,                -- what Gemini Vision OCR produced
    caption TEXT,                 -- reserved for future LLaVA captioning
    text_source TEXT,             -- 'extracted' | 'ocr' | 'combined'
    added_at TEXT,
    UNIQUE (document_id, page_num),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_document_pages_doc ON document_pages(document_id);

CREATE TABLE IF NOT EXISTS document_page_embeddings (
    page_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    generated_at TEXT,
    FOREIGN KEY (page_id) REFERENCES document_pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_page_embed_model ON document_page_embeddings(model);

-- CLIP embeddings of the rendered PDF page images (for visual search).
CREATE TABLE IF NOT EXISTS doc_page_image_embeddings (
    page_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    generated_at TEXT,
    FOREIGN KEY (page_id) REFERENCES document_pages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_page_img_embed_model ON doc_page_image_embeddings(model);

-- ---- YouTube videos (Phase 7.5) --------------------------------------------
-- One row per ingested video. source_channel is the @handle we scraped
-- from.  audio_path is the on-disk MP3 we transcribed from and (by
-- policy) keep so future Whisper upgrades can reprocess without re-
-- downloading. transcript_status tracks resume state across restarts.
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,              -- YouTube video id (11 chars)
    title TEXT,
    url TEXT,
    source_channel TEXT,
    published_at TEXT,                -- ISO, may be approximate from yt-dlp
    duration_sec INTEGER,
    audio_path TEXT,
    transcript_status TEXT,           -- 'pending' | 'downloaded' | 'transcribed' | 'failed'
    transcript_error TEXT,
    summary TEXT,
    added_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(transcript_status);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(source_channel);

-- Transcript chunks — one row per ~90-second semantic window; carries
-- start/end offsets so citations can open YouTube at the exact second.
CREATE TABLE IF NOT EXISTS video_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    chunk_index INTEGER,
    start_sec REAL,
    end_sec REAL,
    text TEXT,
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_video_chunks_video ON video_chunks(video_id);

CREATE TABLE IF NOT EXISTS video_chunk_embeddings (
    chunk_id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    generated_at TEXT,
    FOREIGN KEY (chunk_id) REFERENCES video_chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_video_chunk_embed_model ON video_chunk_embeddings(model);

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

-- ---- Image embeddings (Phase 4) --------------------------------------------
-- CLIP joint-space embeddings (image + text share the same vector space),
-- one row per attachment. 512-dim for ViT-B-32, larger for bigger models.
CREATE TABLE IF NOT EXISTS image_embeddings (
    attachment_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    generated_at TEXT,
    FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_embeddings_model ON image_embeddings(model);

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
    "ai_provider_chat_fallback": "ollama",   # used when chat primary fails
    "ai_provider_vision": "ollama",
    "embed_model_ollama": "nomic-embed-text",
    "embed_model_gemini": "gemini-embedding-001",
    "chat_model_ollama": "llama3.1:8b",
    "chat_model_gemini": "gemini-2.5-flash",
    "vision_model_ollama": "llava:13b",
    # Phase 4 — CLIP for visual search.
    "clip_model": "ViT-B-32",
    "clip_pretrained": "openai",
    # Phase 4.5 — OCR engine for the PDF doc ingest.
    # easyocr: fast, local, classical CV. ollama: LLaVA (tends to describe,
    # not transcribe — avoid for OCR). gemini: rate-limited on free tier.
    "ocr_provider": "easyocr",
    # Feed noise filter — hides reactions, one-word replies ("lol", "ok"),
    # emoji-only messages, etc. Tom's own messages and messages with
    # attachments are always kept. Toggle via the header button.
    "hide_feed_noise": "1",
    # YouTube (TomTube) ingest defaults. Channel is Bookmap's — Tom posts
    # there alongside other educators; the title filter narrows to his work.
    "youtube_channel_url": "https://www.youtube.com/@Bookmap_pro/videos",
    "youtube_title_filter": "tom b",
    "youtube_audio_bitrate": "96",
    # Browser to read YouTube session cookies from. YouTube blocks
    # anonymous downloads on many videos (bot gate). yt-dlp uses the
    # logged-in session from one of: chrome, edge, firefox, brave,
    # opera, vivaldi. Empty string = don't send cookies.
    "youtube_browser_cookies": "chrome",
    "whisper_model": "large-v3",
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
