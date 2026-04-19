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

try:
    from pytubefix import YouTube as _PTFYouTube
except ImportError:  # pragma: no cover
    _PTFYouTube = None


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


# File extensions the folder-ingest scanner treats as audio sources.
# Defined early so download_audio() below can also consult it when
# resolving an existing download.
_AUDIO_VIDEO_EXTS = (".mp3", ".m4a", ".opus", ".aac", ".wav", ".flac",
                     ".mp4", ".webm", ".mkv", ".mov")


class YouTubeNotSignedInError(RuntimeError):
    """Raised when ingest is attempted but the auth pre-flight fails.

    Kept under the old name for wire-compatibility with the UI; the
    actual requirement is now "Firefox signed into YouTube + Node.js on
    PATH + bgutil PO-token script installed" rather than an OAuth token
    file.
    """


def is_signed_in() -> bool:
    """True if the full yt-dlp pipeline is ready: Firefox profile is
    accessible, Node.js is on PATH, and the bgutil script is present.

    Checks all three because yt-dlp will silently fall back to an
    unauthenticated client path (android_vr) if any of them is missing,
    and YouTube then rejects every video. Failing fast is friendlier.
    """
    import shutil
    if shutil.which("node") is None:
        return False
    # Cheap existence check for the bgutil script in its usual locations.
    for loc in (
        Path.home() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
        Path(r"C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js"),
        data_dir() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
    ):
        if loc.is_file():
            break
    else:
        return False
    # Firefox cookies get read by yt-dlp at download time; we can't cheaply
    # validate them without a network round-trip. Treat the other two
    # checks as sufficient — yt-dlp will surface a clear error if cookies
    # are missing/stale at runtime.
    return True


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


def _channel_root(channel_url: str) -> str:
    """Given any channel URL (.../videos, .../streams, .../), return the
    base @handle URL we can then suffix with /videos, /streams, etc."""
    u = channel_url.rstrip("/")
    for suffix in ("/videos", "/streams", "/shorts", "/featured", "/playlists"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    return u


def _enumerate_one(url: str, limit: int | None) -> tuple[list[dict], str]:
    """Single yt-dlp walk. Returns (entries, channel_display_name)."""
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
    log.info("Enumerating %s", url)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        log.warning("enumerate %s failed: %s", url, exc)
        return [], ""
    entries = (info.get("entries") or []) if info else []
    return entries, (info.get("channel") or info.get("title") or "") if info else ""


def enumerate_channel(
    channel_url: str,
    title_filter: str = "tom b",
    limit: int | None = None,
) -> list[VideoEntry]:
    """Enumerate a YouTube channel's content filtered to ``title_filter``.

    Primary path: YouTube's in-channel search URL
    ``/@handle/search?query=<filter>``. The server-side filter is far
    better than title scraping — it indexes across /videos + /streams +
    shorts and uses YouTube's fuzzy matching so variations like
    "with Tom B.", "Tom B,", "by TomB" all resolve.

    Fallback: if search returns nothing (rare, e.g. a channel with
    search disabled), walk /videos and /streams and filter client-side
    with the same word-bounded pattern.
    """
    root = _channel_root(channel_url)
    # Use just the first token of the filter as the search query — shorter
    # is more lenient on YouTube's side. We still apply a client-side
    # sanity filter below to drop anything that slipped through.
    query_term = title_filter.strip().split()[0] if title_filter.strip() else "tom"
    search_url = f"{root}/search?query={query_term}"

    all_entries, channel_name = _enumerate_one(search_url, limit)
    source = "channel search"

    # Fallback: if search returned nothing, use /videos + /streams.
    if not all_entries:
        log.warning("channel search returned 0 — falling back to /videos+/streams")
        seen_ids: set[str] = set()
        for u in (f"{root}/videos", f"{root}/streams"):
            entries, nm = _enumerate_one(u, limit)
            channel_name = channel_name or nm
            for e in entries:
                vid = (e or {}).get("id") or ""
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    all_entries.append(e)
        source = "/videos + /streams"

    pattern = re.compile(rf"\b{re.escape(title_filter.lower())}\b", re.IGNORECASE)
    out: list[VideoEntry] = []
    seen: set[str] = set()
    for e in all_entries:
        if not e:
            continue
        title = e.get("title") or ""
        # Keep the word-bounded sanity check — YouTube's search can
        # sometimes return barely-related suggestions at the tail.
        if not pattern.search(title):
            continue
        vid = e.get("id") or ""
        if not vid or vid in seen:
            continue
        dur = int(e.get("duration") or 0)
        # Skip livestream placeholders / upcoming streams with no audio.
        # A sub-60-second "Tom B" hit isn't a teaching stream.
        if dur < 60:
            continue
        seen.add(vid)
        out.append(VideoEntry(
            id=vid,
            title=title,
            url=e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            duration_sec=dur,
            published_at="",
            channel=channel_name,
        ))
    log.info("%d videos matched %r via %s (from %d entries)",
             len(out), title_filter, source, len(all_entries))
    return out


# ---------------------------------------------------------------------------
# upsert into the videos table
# ---------------------------------------------------------------------------
def find_new_videos(
    conn: sqlite3.Connection,
    channel_url: str | None = None,
    title_filter: str = "tom b",
    limit: int | None = None,
) -> tuple[list[VideoEntry], list[VideoEntry]]:
    """Quick enumerate + split into (new, already-known). Doesn't touch disk.

    Returns ``(new_entries, existing_entries)`` so the caller can ask the
    user "3 new videos — ingest now?" without committing anything yet.
    """
    channel_url = channel_url or (
        dbmod.get_setting(conn, "youtube_channel_url",
                          "https://www.youtube.com/@Bookmap_pro/videos")
    )
    entries = enumerate_channel(channel_url, title_filter=title_filter, limit=limit)
    existing_ids = {
        r["id"] for r in conn.execute("SELECT id FROM videos")
    }
    new = [e for e in entries if e.id not in existing_ids]
    old = [e for e in entries if e.id in existing_ids]
    return new, old


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
# audio download (yt-dlp + Firefox cookies + Node bgutil script)
# ---------------------------------------------------------------------------
# Pipeline we confirmed works against YouTube's 2026 bot-gate:
#
#   1. `--cookies-from-browser firefox` — signed-in session cookies
#   2. `--js-runtimes node`             — Node.js solves the n-challenge
#   3. `--extractor-args youtubepot-bgutilscript:script_path=<js>`
#                                        — bgutil Node.js script mints PO tokens
#
# All three are required. Removing any single one causes yt-dlp to fall
# back to the android_vr client, which YouTube then rejects with
# "Sign in to confirm you're not a bot". The sleep-interval flags keep us
# under YouTube's session rate-limit (~20-50s per request is safe).


def _bgutil_script_path() -> Path:
    """Locate the bgutil-ytdlp-pot-provider Node script. Searches the
    standard install locations; raises if not found."""
    candidates = [
        Path.home() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
        Path(r"C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js"),
        data_dir() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise YouTubeNotSignedInError(
        "bgutil PO-token script not found. Clone and build "
        "https://github.com/Brainicism/bgutil-ytdlp-pot-provider into "
        "your home directory before using direct YouTube import."
    )


def _node_on_path() -> bool:
    """True if `node` is resolvable on PATH (required for the JS runtime
    and the bgutil script)."""
    import shutil
    return shutil.which("node") is not None


def _convert_to_mp3(src: Path, bitrate_kbps: int) -> Path:
    """Run ffmpeg to strip video + transcode to low-bitrate MP3.
    Deletes the source file after a successful convert."""
    import subprocess
    tgt = src.with_suffix(".mp3")
    subprocess.run(
        [_ffmpeg_path(), "-y", "-i", str(src),
         "-vn", "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k",
         str(tgt)],
        check=True, capture_output=True,
    )
    src.unlink(missing_ok=True)
    return tgt


def download_archive_path() -> Path:
    """Shared download-archive file recording every video id Tom's Lab has
    finished downloading. yt-dlp consults this BEFORE issuing any network
    request, so resume passes on already-downloaded videos cost zero
    YouTube API calls — which is how we stay well under the rate-limiter
    when users click Import for the second time."""
    return data_dir() / "youtube_download_archive.txt"


def _already_downloaded_via_archive(video_id: str) -> bool:
    """True if the archive file records this id as complete.

    Matches yt-dlp's archive format: "youtube VIDEO_ID\\n" per line.
    Cheap to check — a tiny text file read each call."""
    ap = download_archive_path()
    if not ap.exists():
        return False
    needle = f"youtube {video_id}"
    try:
        with ap.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == needle:
                    return True
    except OSError:
        return False
    return False


def download_audio(video_id: str, bitrate_kbps: int = 96) -> Path:
    """Download a single video's audio via yt-dlp subprocess.

    Saves the native audio stream (.webm/Opus, ~70MB per 80-min video) so
    we don't need a working ffprobe. Tom's Lab's faster-whisper pipeline
    reads .webm directly, and the folder-import path does the same.

    Two layers of resume:
      1. If a non-empty file for this video_id already exists in any
         supported audio extension, return it immediately — no subprocess.
      2. If yt-dlp's download-archive records this id as complete, skip
         too (covers cases where the file was moved/renamed but we
         remember having fetched it).
    """
    for ext in (".webm", ".m4a", ".opus", ".mp3"):
        existing = videos_dir() / f"{video_id}{ext}"
        if existing.exists() and existing.stat().st_size > 1024:
            return existing
    if _already_downloaded_via_archive(video_id):
        # Nothing on disk but the archive says we've done this one —
        # caller will get a clearer error than trying to re-fetch it.
        raise RuntimeError(
            f"video {video_id} is recorded in the download archive but "
            f"its file is missing from {videos_dir()}. Remove the id from "
            f"{download_archive_path()} if you want to re-download it."
        )

    if not _node_on_path():
        raise YouTubeNotSignedInError(
            "Node.js not found on PATH. Install Node.js (≥20) before "
            "using direct YouTube import."
        )
    bgutil = _bgutil_script_path()    # raises if missing

    import subprocess
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(videos_dir() / f"{video_id}.%(ext)s")
    log.info("Downloading audio for %s (yt-dlp/firefox+bgutil)", video_id)

    # Rate-limit-safe pacing matches the standalone batch file
    # (download_tom_videos.bat):
    #   • 30-180s random sleep between videos (6x spread, not a fixed
    #     cadence that anti-bot heuristics could fingerprint)
    #   • 2s sleep between individual HTTP requests
    #   • --download-archive records every completed video id so
    #     subsequent passes skip them without hitting YouTube at all
    cmd = [
        "python", "-m", "yt_dlp",
        "--cookies-from-browser", "firefox",
        "--js-runtimes", "node",
        "--extractor-args", f"youtubepot-bgutilscript:script_path={bgutil}",
        "--format", "bestaudio[ext=webm]/bestaudio/best",
        "--no-overwrites", "--continue",
        "--no-warnings",
        "--sleep-interval", "30", "--max-sleep-interval", "180",
        "--sleep-requests", "2",
        "--download-archive", str(download_archive_path()),
        "-o", out_template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = "\n".join(msg[-8:]) if msg else "(no output)"
        if "Sign in to confirm" in tail or "not a bot" in tail:
            raise YouTubeNotSignedInError(
                "YouTube rejected the request as unauthenticated. Open "
                "Firefox, confirm you're signed into youtube.com, then "
                "retry. If this persists, YouTube rate-limited the IP — "
                "wait ~60 minutes."
            )
        raise RuntimeError(f"yt-dlp failed for {video_id}: {tail}")

    # Find whatever it produced. yt-dlp names it video_id.ext.
    for p in videos_dir().glob(f"{video_id}.*"):
        if p.is_file() and p.suffix.lower() in _AUDIO_VIDEO_EXTS:
            if p.stat().st_size > 1024:
                return p
    raise RuntimeError(f"yt-dlp ran but produced no output for {video_id}")


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

    # 1) audio via pytubefix + OAuth (cached token)
    audio = audio_path_for(video_id)
    if not audio.exists():
        audio = download_audio(video_id, bitrate_kbps=bitrate_kbps)
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


# ---------------------------------------------------------------------------
# folder ingest — offline alternative to direct YouTube download
# ---------------------------------------------------------------------------
# YouTube's bot-gate makes direct downloads fragile (see commit history of
# this file). The reliable path: let the user bulk-download Tom's videos
# with any consumer tool (4K Video Downloader, yt-dlp CLI, etc.) to a
# folder, then ingest from there. Filenames from most tools embed the
# 11-char YouTube ID in brackets or after a dash; we parse it out so
# citations still link to the canonical URL + timestamp.
# (_AUDIO_VIDEO_EXTS is defined near the top of this module so the
# download path can also consult it.)

# 4K Video Downloader:    "Title [VIDEO_ID].mp3"
# yt-dlp default:          "Title-VIDEO_ID.ext" or "Title [VIDEO_ID].ext"
# Trailing-ID form is common because the extension is stripped before we
# hit the regex (we match against the stem), so the id often lands at EOL.
_YT_ID_IN_FILENAME = re.compile(
    r"(?:\[|[\s\-_])([A-Za-z0-9_-]{11})(?:\]|[\s\.]|$)"
)


def _parse_video_id(stem: str) -> str | None:
    """Find a likely YouTube id embedded in a filename stem, or None."""
    m = _YT_ID_IN_FILENAME.search(stem)
    return m.group(1) if m else None


def _derive_id_from_path(path: Path) -> str:
    """Generate a stable synthetic id for files that don't carry a YouTube
    id — uses a short hash of the filename so re-runs are idempotent."""
    import hashlib
    h = hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:11]
    return f"local_{h}"


def _probe_duration_sec(path: Path) -> int:
    """Best-effort audio duration via ffprobe. Returns 0 if not available."""
    import subprocess
    ffmpeg = _ffmpeg_path()
    # imageio-ffmpeg ships ffmpeg.exe; ffprobe is usually alongside it.
    probe = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
    candidates = [probe, Path("ffprobe"), Path("ffprobe.exe")]
    for p in candidates:
        try:
            out = subprocess.run(
                [str(p), "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0 and out.stdout.strip():
                return int(float(out.stdout.strip()))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return 0


def scan_folder_for_videos(
    folder: Path,
    *,
    max_entries_visited: int = 20000,
) -> list[dict]:
    """Walk `folder` looking for audio/video files and return a list of
    candidate video rows ready for upsert. Does not touch the DB.

    Safety: aborts with a clear error after walking `max_entries_visited`
    entries without finding audio/video — this catches the 'user picked
    D:\\ instead of D:\\Tom Videos' mistake where rglob could otherwise
    churn through the entire drive and hang the UI.
    """
    out: list[dict] = []
    visited = 0
    for p in sorted(folder.rglob("*")):
        visited += 1
        if visited > max_entries_visited and not out:
            raise RuntimeError(
                f"Scanned {visited:,} entries in {folder} without finding "
                f"any audio or video files. Did you pick the wrong folder? "
                f"Navigate INTO the folder containing the .webm / .mp4 / "
                f".mp3 files and click Select there."
            )
        if not p.is_file():
            continue
        if p.suffix.lower() not in _AUDIO_VIDEO_EXTS:
            continue
        yt_id = _parse_video_id(p.stem)
        vid = yt_id or _derive_id_from_path(p)
        title = p.stem
        if yt_id:
            # Strip the "[VIDEO_ID]" suffix from the title for cleanliness
            title = re.sub(rf"[\s\-_\[]*{re.escape(yt_id)}[\]\.]*\s*$", "",
                           title).strip(" -_[]")
        url = (f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "")
        out.append({
            "id": vid,
            "title": title,
            "url": url,
            "source_channel": folder.name,
            "duration_sec": 0,    # probed lazily in ingest_folder below
            "audio_path": str(p),
            "has_yt_id": bool(yt_id),
        })
    return out


def ingest_folder(
    conn: sqlite3.Connection,
    folder: Path,
    model_name: str = "large-v3",
    progress: ProgressFn | None = None,
) -> dict:
    """Ingest every audio/video file in `folder` (recursive). For each one:
    insert a videos row pointing at the existing file, probe duration,
    transcribe with faster-whisper, chunk, and store. Files already
    transcribed (matched by id) are skipped, so re-running is cheap."""
    folder = Path(folder)
    if not folder.is_dir():
        raise RuntimeError(f"{folder} is not a directory")
    if progress:
        progress("Scanning folder", 0, 0)
    candidates = scan_folder_for_videos(folder)

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for c in candidates:
        existing = conn.execute(
            "SELECT transcript_status FROM videos WHERE id = ?", (c["id"],)
        ).fetchone()
        if existing:
            # Update audio_path in case the folder moved; don't overwrite
            # a completed transcription.
            conn.execute(
                "UPDATE videos SET audio_path = ? WHERE id = ?",
                (c["audio_path"], c["id"]),
            )
            continue
        conn.execute(
            "INSERT INTO videos(id, title, url, source_channel, duration_sec,"
            " audio_path, transcript_status, added_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (c["id"], c["title"], c["url"], c["source_channel"],
             c["duration_sec"], c["audio_path"], "downloaded", now),
        )
        added += 1
    conn.commit()

    pending = conn.execute(
        "SELECT id, audio_path FROM videos "
        "WHERE transcript_status IN ('pending','downloaded','failed') "
        "ORDER BY added_at"
    ).fetchall()
    total = len(pending)

    done = 0
    errors: list[tuple[str, str]] = []
    for i, r in enumerate(pending, start=1):
        vid = r["id"]
        audio = Path(r["audio_path"]) if r["audio_path"] else None
        if progress:
            progress(f"Transcribing {vid}", i - 1, total)
        if audio is None or not audio.exists():
            errors.append((vid, "audio file missing"))
            continue
        try:
            # Probe duration once for UI niceness; cheap compared to whisper.
            dur = _probe_duration_sec(audio)
            if dur:
                conn.execute(
                    "UPDATE videos SET duration_sec = ? WHERE id = ?",
                    (dur, vid),
                )
            segs = transcribe(audio, model_name=model_name)
            chunks = chunk_segments(segs)
            store_chunks(conn, vid, chunks)
            conn.execute(
                "UPDATE videos SET transcript_status = 'transcribed', "
                "transcript_error = NULL WHERE id = ?",
                (vid,),
            )
            conn.commit()
            done += 1
        except Exception as exc:
            log.warning("folder-ingest %s failed: %s", vid, exc)
            conn.execute(
                "UPDATE videos SET transcript_status = 'failed', "
                "transcript_error = ? WHERE id = ?",
                (str(exc)[:500], vid),
            )
            conn.commit()
            errors.append((vid, str(exc)[:300]))

    # After transcription finishes, embed the newly-created chunks. Without
    # this step Ask Tom's semantic search can't retrieve video content.
    # Failure here is non-fatal — transcripts survive, embeddings can be
    # built later via File → Build text embeddings.
    embedded = 0
    try:
        from tomslab.ai import registry
        from tomslab import embed_service
        provider = registry.get_embed_provider(conn)
        if progress:
            progress("Embedding video chunks", total, total)
        embedded = embed_service.embed_pending_video_chunks(
            conn, provider,
            progress=lambda d, t, s: (progress("Embedding " + s, d, t)
                                      if progress else None),
        )
        from tomslab.semantic import invalidate_video_cache
        invalidate_video_cache()
    except Exception as exc:
        log.warning("post-transcription embedding failed: %s", exc)

    if progress:
        progress("Done", total, total)
    return {
        "scanned": len(candidates),
        "newly_added_rows": added,
        "processed": done,
        "failed": len(errors),
        "embedded": embedded,
        "errors": errors,
    }
