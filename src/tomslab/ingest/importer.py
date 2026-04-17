"""Orchestrates an import of a DCE JSON export into the SQLite database.

Phases executed (PRD §7.2):
  1. Parse + insert messages (deduped on Discord message ID)
  2. Resolve + insert attachments (paths point at the DCE _Files folder)
  3. Build conversation windows around featured-speaker messages

Embeddings and vision descriptions are Phase 3/6 — not run here.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tomslab import db as dbmod
from tomslab.ingest.dce import (
    MessageRecord,
    count_messages,
    read_header,
    stream_messages,
)
from tomslab.ingest.windows import build_windows

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]  # (phase, current, total)


@dataclass
class ImportResult:
    messages_added: int = 0
    messages_skipped: int = 0
    attachments_added: int = 0
    windows_built: int = 0
    total_seen: int = 0


def _noop(_phase: str, _current: int, _total: int) -> None:
    pass


def import_export_file(
    json_path: Path,
    conn: sqlite3.Connection | None = None,
    progress: ProgressFn = _noop,
) -> ImportResult:
    """Import a DCE JSON file. Idempotent — existing message IDs are skipped.

    If ``conn`` is None a default connection is opened and closed here.
    """
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(json_path)

    close_when_done = False
    if conn is None:
        conn = dbmod.connect()
        dbmod.initialise(conn)
        close_when_done = True

    try:
        return _run_import(json_path, conn, progress)
    finally:
        if close_when_done:
            conn.close()


def _run_import(
    json_path: Path, conn: sqlite3.Connection, progress: ProgressFn
) -> ImportResult:
    now = datetime.now(timezone.utc).isoformat()
    result = ImportResult()

    # --- Phase 0: header + count (for progress) ---
    progress("Reading export header", 0, 0)
    header = read_header(json_path)
    progress("Counting messages", 0, 0)
    total = count_messages(json_path)
    result.total_seen = total
    log.info(
        "Importing %d messages from %s / %s", total, header.guild_name, header.channel_name
    )

    # --- Record this import run ---
    cur = conn.execute(
        "INSERT INTO imports(source_path, guild_id, channel_id, started_at) "
        "VALUES (?, ?, ?, ?)",
        (str(json_path), header.guild_id, header.channel_id, now),
    )
    import_id = cur.lastrowid
    conn.commit()

    featured = dbmod.get_setting(conn, "featured_speaker_username", "tom_b_trades") or ""

    # --- Phase 1: messages + attachments ---
    messages_added = 0
    messages_skipped = 0
    attachments_added = 0

    existing_ids: set[str] = {
        row["id"] for row in conn.execute("SELECT id FROM messages")
    }

    batch: list[MessageRecord] = []
    BATCH_SIZE = 500

    for i, msg in enumerate(stream_messages(json_path), start=1):
        if msg.id in existing_ids:
            messages_skipped += 1
        else:
            batch.append(msg)
            existing_ids.add(msg.id)

        if len(batch) >= BATCH_SIZE:
            added, attached = _flush_batch(conn, batch, featured, now)
            messages_added += added
            attachments_added += attached
            batch.clear()

        if i % 500 == 0 or i == total:
            progress("Importing messages", i, total)

    if batch:
        added, attached = _flush_batch(conn, batch, featured, now)
        messages_added += added
        attachments_added += attached

    result.messages_added = messages_added
    result.messages_skipped = messages_skipped
    result.attachments_added = attachments_added
    conn.commit()

    # --- Phase 2: conversation windows (only for the messages we just added) ---
    progress("Building conversation windows", 0, 0)
    result.windows_built = build_windows(
        conn,
        featured_speaker=featured,
        channel_id=header.channel_id,
        progress=lambda i, n: progress("Building conversation windows", i, n),
    )

    # --- finalise ---
    conn.execute(
        "UPDATE imports SET finished_at = ?, messages_added = ?, "
        "messages_skipped = ?, attachments_added = ? WHERE id = ?",
        (
            datetime.now(timezone.utc).isoformat(),
            messages_added,
            messages_skipped,
            attachments_added,
            import_id,
        ),
    )
    conn.commit()
    progress("Done", total, total)
    return result


def _flush_batch(
    conn: sqlite3.Connection,
    batch: list[MessageRecord],
    featured_speaker: str,
    imported_at: str,
) -> tuple[int, int]:
    """Insert a batch of new messages and their attachments. Returns (msgs, attachments)."""
    message_rows = [
        (
            m.id,
            m.channel_id,
            m.channel_name,
            m.guild_id,
            m.guild_name,
            m.author_id,
            m.author_name,
            m.author_nickname,
            m.timestamp,
            m.timestamp_edited,
            m.content,
            m.reply_to_message_id,
            1 if m.is_pinned else 0,
            1 if m.author_name == featured_speaker else 0,
            m.raw_json,
            imported_at,
        )
        for m in batch
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO messages ("
        "id, channel_id, channel_name, guild_id, guild_name, "
        "author_id, author_name, author_nickname, "
        "timestamp, timestamp_edited, content, reply_to_message_id, "
        "is_pinned, is_featured_speaker, raw_json, imported_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        message_rows,
    )

    # Mirror into FTS5. Skip any IDs already there (re-import safety).
    existing_fts = {
        r[0]
        for r in conn.execute(
            "SELECT id FROM messages_fts WHERE id IN ("
            + ",".join("?" * len(batch))
            + ")",
            [m.id for m in batch],
        )
    } if batch else set()
    fts_rows = [
        (m.id, m.content or "", m.author_name or "", m.author_nickname or "")
        for m in batch
        if m.id not in existing_fts
    ]
    if fts_rows:
        conn.executemany(
            "INSERT INTO messages_fts(id, content, author_name, author_nickname) "
            "VALUES (?,?,?,?)",
            fts_rows,
        )

    attachment_rows = []
    for m in batch:
        for a in m.attachments:
            if not a.id:
                continue
            attachment_rows.append(
                (
                    a.id,
                    a.message_id,
                    a.filename,
                    a.local_path,
                    a.url_original,
                    a.content_type,
                    a.file_size,
                    a.width,
                    a.height,
                )
            )
    if attachment_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO attachments("
            "id, message_id, filename, local_path, url_original, "
            "content_type, file_size, width, height"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            attachment_rows,
        )

    return len(message_rows), len(attachment_rows)
