"""
TRA-1749: Retry Whisper transcription for 5 pending TRA-1610 videos — CPU mode.

CUDA disabled intentionally: GPU memory is occupied by TRA-1616 inference jobs.
CPU large-v3/int8 is the proven path from TRA-1610.

Run: D:\Toms Lab\.venv\Scripts\python.exe transcribe_pending_cpu.py
Log: D:\Toms Lab\logs\transcribe_pending_tra1749_cpu.log
"""
import os
import sys
import time
import sqlite3
import traceback
import glob
from pathlib import Path

# Force CPU — CUDA memory is consumed by other running inference processes.
os.environ["CUDA_VISIBLE_DEVICES"] = ""

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = r"D:\Toms Lab\data\tomslab.db"
VIDEOS_DIR = r"D:\Tom Videos"
LOG_PATH = r"D:\Toms Lab\logs\transcribe_pending_tra1749_cpu.log"
MODEL_SIZE = "large-v3"

# Short DB id -> YouTube ID suffix used in filenames
PENDING = {
    "Adjustments":  "QBvdndZquYc",
    "Flexibility":  "1BTWkJqvkYU",
    "Post-Tariff":  "wxlRMLVVh74",
    "Pre-Holiday":  "NozNOEKa3do",
    "Range-Bound":  "a3SwWfl_lx0",
}

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_audio(yt_id):
    for fname in os.listdir(VIDEOS_DIR):
        if f"[{yt_id}]" in fname or fname.startswith(f"{yt_id}."):
            fpath = os.path.join(VIDEOS_DIR, fname)
            if os.path.getsize(fpath) > 1024:
                return fpath
    return None


def chunk_segments(segments, chunk_target_sec=90, overlap_sec=10):
    if not segments:
        return []
    chunks = []
    cur_start = float(segments[0].start)
    cur_end = float(segments[0].end)
    cur_text = [segments[0].text.strip()]
    for s in segments[1:]:
        window = float(s.end) - cur_start
        if window >= chunk_target_sec:
            text = " ".join(cur_text).strip()
            if text:
                chunks.append((cur_start, cur_end, text))
            overlap_back = max(0.0, float(s.start) - overlap_sec)
            cur_start = overlap_back
            cur_text = [s.text.strip()]
            cur_end = float(s.end)
        else:
            cur_text.append(s.text.strip())
            cur_end = float(s.end)
    if cur_text:
        text = " ".join(cur_text).strip()
        if text:
            chunks.append((cur_start, cur_end, text))
    return chunks


log("=== TRA-1749 CPU transcription started ===")
log(f"Model: {MODEL_SIZE}, device: cpu, compute_type: int8")
log(f"Videos: {list(PENDING.keys())}")

log("Loading Whisper model...")
from faster_whisper import WhisperModel
try:
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    log("Model loaded.")
except Exception as e:
    log(f"FATAL: Could not load model: {e}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
results = {}

for db_id, yt_id in PENDING.items():
    log(f"\n--- {db_id} ({yt_id}) ---")
    audio_path = find_audio(yt_id)
    if not audio_path:
        log(f"  SKIP: no audio file found for yt_id={yt_id}")
        results[db_id] = "ERROR:audio_not_found"
        continue

    log(f"  Audio: {os.path.basename(audio_path)}")
    conn.execute(
        "UPDATE videos SET audio_path=?, transcript_status='downloaded' WHERE id=?",
        (audio_path, db_id),
    )
    conn.commit()

    try:
        log(f"  Transcribing (cpu/int8)...")
        t0 = time.time()
        segments_gen, info = model.transcribe(
            audio_path,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700},
            language="en",
        )
        segments = []
        last_pct = -1
        for s in segments_gen:
            segments.append(s)
            if info.duration and info.duration > 0:
                pct = int(float(s.end) * 100 / info.duration)
                if pct // 10 > last_pct // 10:
                    elapsed = time.time() - t0
                    log(f"  {db_id}: {pct}% ({int(float(s.end)//60)}:{int(float(s.end)%60):02d} / {int(info.duration//60)}:{int(info.duration%60):02d}) elapsed={elapsed:.0f}s")
                    last_pct = pct

        elapsed = time.time() - t0
        log(f"  Transcription done: {len(segments)} segments, {info.duration:.0f}s audio, {elapsed:.0f}s elapsed")

        chunks = chunk_segments(segments)
        log(f"  Chunking done: {len(chunks)} chunks")

        # Store chunks
        conn.execute("DELETE FROM video_chunks WHERE video_id=?", (db_id,))
        for i, (start, end, text) in enumerate(chunks):
            conn.execute(
                "INSERT INTO video_chunks (video_id, chunk_index, start_sec, end_sec, text) "
                "VALUES (?,?,?,?,?)",
                (db_id, i, start, end, text),
            )
        conn.execute(
            "UPDATE videos SET transcript_status='transcribed', transcript_error=NULL, n_chunks=? WHERE id=?",
            (len(chunks), db_id),
        )
        conn.commit()
        log(f"  DONE {db_id}: {len(chunks)} chunks stored")
        results[db_id] = f"DONE:{len(chunks)}"

    except Exception as e:
        log(f"  ERROR {db_id}: {e}")
        log(traceback.format_exc())
        conn.execute(
            "UPDATE videos SET transcript_status='failed', transcript_error=? WHERE id=?",
            (str(e)[:500], db_id),
        )
        conn.commit()
        results[db_id] = f"ERROR:{e}"

# Final verification
log("\n=== VERIFICATION ===")
rows = conn.execute(
    "SELECT id, transcript_status, n_chunks FROM videos "
    "WHERE id IN ('Post-Tariff','Range-Bound','Pre-Holiday','Flexibility','Adjustments')"
).fetchall()
for r in rows:
    log(f"  {r['id']}: status={r['transcript_status']}, n_chunks={r['n_chunks']}")

chunk_count = conn.execute(
    "SELECT COUNT(*) FROM video_chunks "
    "WHERE video_id IN ('Post-Tariff','Range-Bound','Pre-Holiday','Flexibility','Adjustments')"
).fetchone()[0]
log(f"Total chunks in video_chunks: {chunk_count}")

conn.close()

log("\n=== SUMMARY ===")
for vid_id, status in results.items():
    log(f"  {vid_id}: {status}")
done = [v for v, s in results.items() if s.startswith("DONE")]
failed = [v for v, s in results.items() if not s.startswith("DONE")]
log(f"Success: {len(done)}, Failed: {len(failed)}")
