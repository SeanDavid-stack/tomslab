"""Streaming parser for DiscordChatExporter JSON exports.

DCE files can be huge (the Bookmap export is 429 MB) so we stream the
`messages[]` array with ijson and yield one dict at a time. Top-level
`guild`, `channel`, `exportedAt` metadata is parsed eagerly before the
message stream starts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import ijson


@dataclass
class ExportHeader:
    guild_id: str = ""
    guild_name: str = ""
    channel_id: str = ""
    channel_name: str = ""
    exported_at: str = ""


@dataclass
class AttachmentRecord:
    id: str
    message_id: str
    filename: str
    url_original: str          # path as stored in JSON (relative to export dir)
    local_path: str             # absolute path on this machine, or "" if missing
    content_type: str = ""
    file_size: int = 0
    width: int = 0
    height: int = 0


@dataclass
class MessageRecord:
    id: str
    channel_id: str
    channel_name: str
    guild_id: str
    guild_name: str
    author_id: str
    author_name: str
    author_nickname: str
    timestamp: str
    timestamp_edited: str
    content: str
    reply_to_message_id: str | None
    is_pinned: bool
    raw_json: str
    attachments: list[AttachmentRecord] = field(default_factory=list)


def read_header(path: Path) -> ExportHeader:
    """Pull the guild/channel/exportedAt fields without loading all messages."""
    h = ExportHeader()
    with path.open("rb") as f:
        parser = ijson.parse(f)
        for prefix, event, value in parser:
            if prefix == "messages" and event == "start_array":
                break
            if prefix == "guild.id":       h.guild_id = value or ""
            elif prefix == "guild.name":   h.guild_name = value or ""
            elif prefix == "channel.id":   h.channel_id = value or ""
            elif prefix == "channel.name": h.channel_name = value or ""
            elif prefix == "exportedAt":   h.exported_at = value or ""
    return h


def count_messages(path: Path) -> int:
    """Stream through just to count — useful for progress bars."""
    n = 0
    with path.open("rb") as f:
        for _ in ijson.items(f, "messages.item"):
            n += 1
    return n


def _resolve_attachment_path(export_dir: Path, url: str) -> str:
    """DCE writes attachment URLs as paths relative to the JSON's parent dir
    (Windows backslashes). Turn that into an absolute path if the file exists."""
    if not url:
        return ""
    # URL format: "<export>_Files\\file-hash.png" (Windows) — normalise.
    relative = url.replace("\\", "/")
    candidate = (export_dir / relative).resolve()
    return str(candidate) if candidate.exists() else ""


def stream_messages(path: Path) -> Iterator[MessageRecord]:
    """Yield one MessageRecord per message in the export. Streaming — O(1) memory."""
    export_dir = path.parent.resolve()
    header = read_header(path)

    with path.open("rb") as f:
        for raw in ijson.items(f, "messages.item"):
            yield _build_record(raw, header, export_dir)


def _build_record(
    raw: dict[str, Any], header: ExportHeader, export_dir: Path
) -> MessageRecord:
    author = raw.get("author") or {}
    ref = raw.get("reference") or {}
    reply_to = ref.get("messageId") if isinstance(ref, dict) else None

    msg_id = str(raw.get("id", ""))
    attachments: list[AttachmentRecord] = []
    for a in raw.get("attachments") or []:
        aid = str(a.get("id", ""))
        url = a.get("url", "") or ""
        attachments.append(
            AttachmentRecord(
                id=aid,
                message_id=msg_id,
                filename=a.get("fileName", "") or "",
                url_original=url,
                local_path=_resolve_attachment_path(export_dir, url),
                content_type="",  # DCE doesn't include this in basic attachment entries
                file_size=int(a.get("fileSizeBytes") or 0),
                width=int(a.get("width") or 0),
                height=int(a.get("height") or 0),
            )
        )

    return MessageRecord(
        id=msg_id,
        channel_id=header.channel_id,
        channel_name=header.channel_name,
        guild_id=header.guild_id,
        guild_name=header.guild_name,
        author_id=str(author.get("id", "")),
        author_name=str(author.get("name", "")),
        author_nickname=str(author.get("nickname") or ""),
        timestamp=str(raw.get("timestamp") or ""),
        timestamp_edited=str(raw.get("timestampEdited") or ""),
        content=str(raw.get("content") or ""),
        reply_to_message_id=str(reply_to) if reply_to else None,
        is_pinned=bool(raw.get("isPinned")),
        raw_json=json.dumps(raw, ensure_ascii=False),
        attachments=attachments,
    )
