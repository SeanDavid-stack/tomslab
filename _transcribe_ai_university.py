"""One-off: download + transcribe a single YouTube video for HindSight planning.

Downloads audio for video ID LnyjLNizKHw into D:\\Tom Videos, then runs
faster-whisper (small.en, CPU int8) and writes the full transcript to
D:\\Toms Lab\\_ai_university_LnyjLNizKHw_transcript.txt.

Run from D:\\Toms Lab with: .venv\\Scripts\\python.exe _transcribe_ai_university.py
"""
import subprocess
import sys
import time
from pathlib import Path

VIDEO_ID = "LnyjLNizKHw"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
TARGET_DIR = Path(r"D:\Tom Videos")
OUTPUT_TXT = Path(r"D:\Toms Lab") / f"_ai_university_{VIDEO_ID}_transcript.txt"
PY = sys.executable


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_audio() -> Path | None:
    for p in TARGET_DIR.glob(f"*{VIDEO_ID}*"):
        if p.is_file() and p.suffix.lower() in (".webm", ".m4a", ".mp3", ".opus"):
            return p
    return None


def download() -> Path:
    existing = find_audio()
    if existing:
        log(f"Audio already on disk: {existing.name}")
        return existing
    log("Downloading audio with yt-dlp...")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "-m", "yt_dlp",
        "--format", "bestaudio[ext=webm]/bestaudio/best",
        "--no-overwrites", "--continue",
        "-o", str(TARGET_DIR / "%(title)s [%(id)s].%(ext)s"),
        URL,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log("yt-dlp stderr:\n" + r.stderr)
        raise SystemExit(f"yt-dlp failed (exit {r.returncode})")
    audio = find_audio()
    if audio is None:
        raise SystemExit("Download reported success but no audio file found.")
    log(f"Downloaded: {audio.name} ({audio.stat().st_size/1024/1024:.1f} MB)")
    return audio


def transcribe(audio: Path) -> str:
    log("Loading faster-whisper small.en (CPU int8)...")
    from faster_whisper import WhisperModel
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    log("Transcribing...")
    segments, info = model.transcribe(
        str(audio),
        beam_size=1,
        vad_filter=True,
        language="en",
    )
    parts: list[str] = []
    last_log = 0.0
    total = info.duration or 1.0
    for seg in segments:
        parts.append(f"[{seg.start:7.2f}s] {seg.text.strip()}")
        if seg.end - last_log > 60:
            pct = int(seg.end * 100 / total)
            log(f"  ...{pct}% ({seg.end:.0f}s / {total:.0f}s)")
            last_log = seg.end
    log(f"Done. {len(parts)} segments.")
    return "\n".join(parts)


def main() -> None:
    audio = download()
    text = transcribe(audio)
    OUTPUT_TXT.write_text(text, encoding="utf-8")
    log(f"Wrote transcript: {OUTPUT_TXT}  ({len(text)} chars)")


if __name__ == "__main__":
    main()
