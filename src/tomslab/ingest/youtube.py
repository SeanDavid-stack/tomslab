"""YouTube (TomTube) ingest — download audio, transcribe locally, chunk, store.

Designed to be re-runnable and long-running. Each stage persists to SQLite
so a crash / Ctrl-C / overnight pause leaves the pipeline in a known state:

  transcript_status column on `videos`:
    - 'pending'      → row exists, nothing downloaded yet
    - 'downloaded'   → audio MP3 on disk, no transcript yet
    - 'transcribed'  → chunks + embeddings stored
    - 'failed'       → see transcript_error for detail

Designed around faster-whisper on CUDA — on a 3080 Ti, large-v3 transcribes
audio at ~10× realtime, so one 80-minute video takes ~8 minutes of compute.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from tomslab import db as dbmod
from tomslab.paths import data_dir

log = logging.getLogger(__name__)

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

try:
    import imageio_ffmpeg
except ImportError:  # pragma: no cover
    imageio_ffmpeg = None

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover
    WhisperModel = None


ProgressFn = Callable[[str, int, int], None]   # (stage, current, total)


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
def videos_dir() -> Path:
    d = data_dir() / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audio_path_for(video_id: str) -> Path:
    return videos_dir() / f"{video_id}.mp3"


def _ffmpeg_path() -> str:
    if imageio_ffmpeg is None:
        return "ffmpeg"
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------------------
# channel enumeration + filter
# ---------------------------------------------------------------------------
@dataclass
class VideoEntry:
    id: str
    title: str
    url: str
    duration_sec: int
    published_at: str
    channel: str


def enumerate_channel(
    channel_url: str,
    title_filter: str = "tom b",
    limit: int | None = None,
) -> list[VideoEntry]:
    """Walk a YouTube channel's /videos page with yt-dlp's flat extractor
    (fast — just titles + IDs, no per-video metadata round-trips). Return
    only the entries whose title matches ``title_filter`` (case-insensitive,
    word-bounded so "tom" alone doesn't match random tom words)."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp not installed")
    opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
        "ignoreerrors": True,
    }
    if limit:
        opts["playlistend"] = int(limit)

    log.info("Enumerating %s", channel_url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    entries = (info.get("entries") or []) if info else []
    pattern = re.compile(rf"\b{re.escape(title_filter.lower())}\b", re.IGNORECASE)
    out: list[VideoEntry] = []
    for e in entries:
        if not e:
            continue
        title = e.get("title") or ""
        if not pattern.search(title):
            continue
        vid = e.get("id") or ""
        if not vid:
            continue
        out.append(VideoEntry(
            id=vid,
            title=title,
            url=e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            duration_sec=int(e.get("duration") or 0),
            published_at="",   # flat extractor doesn't give us the date
            channel=info.get("channel") or info.get("title") or "",
        ))
    log.info("%d videos matched %r (of %d total)", len(out), title_filter, len(entries))
    return out


# ---------------------------------------------------------------------------
# upsert into the videos table
# ---------------------------------------------------------------------------
def upsert_video_rows(conn: sqlite3.Connection, entries: list[VideoEntry]) -> int:
    added = 0
    now = datetime.now(timezone.utc).isoformat()
    for e in entries:
        existing = conn.execute(
            "SELECT id FROM videos WHERE id = ?", (e.id,)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO videos(id, title, url, source_channel, duration_sec, "
            "published_at, transcript_status, added_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (e.id, e.title, e.url, e.channel, e.duration_sec,
             e.published_at, "pending", now),
        )
        added += 1
    conn.commit()
    return added


# ---------------------------------------------------------------------------
# audio download
# ---------------------------------------------------------------------------
def _yt_common_opts(browser: str | None) -> dict:
    """Options common to both enumerate + download calls."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "ffmpeg_location": _ffmpeg_path(),
    }
    # YouTube blocks anonymous downloads on many videos. Passing cookies
    # from a logged-in browser session bypasses that without storing a
    # cookie file manually.
    if browser:
        opts["cookiesfrombrowser"] = (browser.lower(),)
    return opts


def download_audio(
    video_id: str,
    bitrate_kbps: int = 96,
    browser: str | None = "chrome",
) -> Path:
    """Download a single video's audio as a low-bitrate MP3 into videos_dir.
    Resumable: if the target MP3 already exists, return it unchanged.

    ``browser`` is passed through to yt-dlp's ``cookiesfrombrowser`` option
    so YouTube's bot-gate sees a logged-in session. Pass None to attempt
    an anonymous download (usually fails on current YouTube).
    """
    if yt_dlp is None:
        raise RuntimeError("yt-dlp not installed")
    out = audio_path_for(video_id)
    if out.exists() and out.stat().st_size > 1024:
        return out
    tmp_template = str(videos_dir() / (video_id + ".%(ext)s"))
    opts = _yt_common_opts(browser)
    opts.update({
        "format": "bestaudio/best",
        "outtmpl": tmp_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": str(bitrate_kbps),
        }],
    })
    url = f"https://www.youtube.com/watch?v={video_id}"
    log.info("Downloading audio for %s (cookies=%s)", video_id, browser or "none")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    if not out.exists():
        for cand in videos_dir().glob(f"{video_id}.*"):
            if cand.suffix.lower() == ".mp3":
                return cand
        raise RuntimeError(f"audio download for {video_id} produced no .mp3")
    return out


# ---------------------------------------------------------------------------
# transcription
# ---------------------------------------------------------------------------
_whisper: "WhisperModel | None" = None


def _load_whisper(model_name: str = "large-v3") -> "WhisperModel":
    global _whisper
    if _whisper is not None:
        return _whisper
    if WhisperModel is None:
        raise RuntimeError("faster-whisper not installed")
    try:
        # GPU float16 if CUDA is available; fall back to CPU int8.
        import torch  # lazy — only needed to check CUDA
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"
    compute = "float16" if device == "cuda" else "int8"
    log.info("Loading faster-whisper %s on %s (%s)", model_name, device, compute)
    _whisper = WhisperModel(model_name, device=device, compute_type=compute)
    return _whisper


@dataclass
class Segment:
    start: float
    end: float
    text: str


def transcribe(audio_path: Path, model_name: str = "large-v3") -> list[Segment]:
    model = _load_whisper(model_name)
    segments, _info = model.transcribe(
        str(audio_path),
        vad_filter=True,           # skip silence — huge speed win
        vad_parameters={"min_silence_duration_ms": 700},
        language="en",
        condition_on_previous_text=True,
    )
    out: list[Segment] = []
    for s in segments:
        out.append(Segment(
            start=float(s.start or 0.0),
            end=float(s.end or 0.0),
            text=(s.text or "").strip(),
        ))
    return out


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------
CHUNK_TARGET_SEC = 90         # ~1.5 minute windows keep enough context + per-chunk retrieval stays sharp
CHUNK_OVERLAP_SEC = 10        # small overlap so a citation at a boundary still retrieves both sides


@dataclass
class Chunk:
    start: float
    end: float
    text: str


def chunk_segments(segs: list[Segment]) -> list[Chunk]:
    """Collapse Whisper's fine-grained segments into ~90s windows."""
    if not segs:
        return []
    chunks: list[Chunk] = []
    cur_start = segs[0].start
    cur_end = segs[0].end
    cur_text: list[str] = [segs[0].text]
    for s in segs[1:]:
        window = s.end - cur_start
        if window >= CHUNK_TARGET_SEC:
            chunks.append(Chunk(cur_start, cur_end, " ".join(cur_text).strip()))
            # Start the next window with a small overlap
            overlap_back = max(0.0, s.start - CHUNK_OVERLAP_SEC)
            cur_start = overlap_back
            cur_text = [s.text]
            cur_end = s.end
        else:
            cur_text.append(s.text)
            cur_end = s.end
    if cur_text:
        chunks.append(Chunk(cur_start, cur_end, " ".join(cur_text).strip()))
    return chunks


def store_chunks(
    conn: sqlite3.Connection, video_id: str, chunks: list[Chunk]
) -> int:
    conn.execute("DELETE FROM video_chunks WHERE video_id = ?", (video_id,))
    rows = [
        (video_id, i, c.start, c.end, c.text)
        for i, c in enumerate(chunks)
    ]
    conn.executemany(
        "INSERT INTO video_chunks(video_id, chunk_index, start_sec, end_sec, text) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# single-video ingest (the smoke-test / manual path)
# ---------------------------------------------------------------------------
def ingest_single_video(
    conn: sqlite3.Connection,
    video_id: str,
    model_name: str = "large-v3",
    bitrate_kbps: int = 96,
    browser: str | None = None,
) -> dict:
    """Download + transcribe + chunk one video. Resumable — skip phases that
    are already done (checks file existence + DB state). Returns a small
    report dict for logging."""
    row = conn.execute(
        "SELECT title, transcript_status FROM videos WHERE id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"video {video_id} not enumerated — run the "
                           "channel scraper first, or insert it manually")

    # 1) audio
    audio = audio_path_for(video_id)
    if not audio.exists():
        if browser is None:
            browser = (
                dbmod.get_setting(conn, "youtube_browser_cookies", "chrome")
                or "chrome"
            )
        audio = download_audio(video_id, bitrate_kbps=bitrate_kbps, browser=browser)
    conn.execute(
        "UPDATE videos SET audio_path = ?, transcript_status = 'downloaded' "
        "WHERE id = ?",
        (str(audio), video_id),
    )
    conn.commit()

    # 2) transcribe
    try:
        segs = transcribe(audio, model_name=model_name)
    except Exception as exc:
        conn.execute(
            "UPDATE videos SET transcript_status = 'failed', "
            "transcript_error = ? WHERE id = ?",
            (str(exc)[:500], video_id),
        )
        conn.commit()
        raise

    # 3) chunk + store
    chunks = chunk_segments(segs)
    n = store_chunks(conn, video_id, chunks)
    conn.execute(
        "UPDATE videos SET transcript_status = 'transcribed', "
        "transcript_error = NULL WHERE id = ?",
        (video_id,),
    )
    conn.commit()

    return {
        "video_id": video_id,
        "title": row["title"],
        "audio_path": str(audio),
        "segments": len(segs),
        "chunks": n,
    }


# ---------------------------------------------------------------------------
# batch ingest
# ---------------------------------------------------------------------------
def ingest_channel(
    conn: sqlite3.Connection,
    channel_url: str | None = None,
    title_filter: str = "tom b",
    limit: int | None = None,
    model_name: str = "large-v3",
    bitrate_kbps: int = 96,
    progress: ProgressFn | None = None,
) -> dict:
    """End-to-end channel ingest. Enumerates → upserts → iterates pending
    videos, downloading + transcribing + chunking each. Already-done videos
    are skipped. Interrupt with Ctrl-C and re-run to resume."""
    channel_url = channel_url or (
        dbmod.get_setting(conn, "youtube_channel_url",
                          "https://www.youtube.com/@Bookmap_pro/videos")
    )

    if progress:
        progress("Enumerating channel", 0, 0)
    entries = enumerate_channel(channel_url, title_filter=title_filter, limit=limit)
    added = upsert_video_rows(conn, entries)

    # find the work set — 'pending' + 'downloaded' (partial from prior runs)
    pending = conn.execute(
        "SELECT id FROM videos WHERE transcript_status IN ('pending','downloaded','failed') "
        "ORDER BY added_at"
    ).fetchall()
    total = len(pending)

    done = 0
    errors: list[tuple[str, str]] = []
    for i, r in enumerate(pending, start=1):
        vid = r["id"]
        if progress:
            progress(f"Processing {vid}", i - 1, total)
        try:
            ingest_single_video(conn, vid, model_name=model_name, bitrate_kbps=bitrate_kbps)
            done += 1
        except Exception as exc:
            log.warning("video %s failed: %s", vid, exc)
            errors.append((vid, str(exc)[:300]))

    if progress:
        progress("Done", total, total)
    return {
        "enumerated": len(entries),
        "newly_added_rows": added,
        "processed": done,
        "failed": len(errors),
        "errors": errors,
    }
