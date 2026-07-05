"""
TRA-1749 restart: Whisper CPU transcription for 4 remaining videos.
Skips QBvdndZquYc (Adjustments) — already done.
Writes chunks to both short-ID rows AND YouTube-ID rows.
Log: D:\Toms Lab\logs\transcribe_pending_tra1749_cpu2.log
"""
import os
import sys
import time
import sqlite3
import traceback

os.environ["CUDA_VISIBLE_DEVICES"] = ""

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = r"D:\Toms Lab\data\tomslab.db"
VIDEOS_DIR = r"D:\Tom Videos"
LOG_PATH = r"D:\Toms Lab\logs\transcribe_pending_tra1749_cpu2.log"
MODEL_SIZE = "large-v3"

# short_id -> (youtube_id, audio_search_pattern)
REMAINING = {
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


def store_for_id(conn, row_id, chunks, audio_path):
    conn.execute("DELETE FROM video_chunks WHERE video_id=?", (row_id,))
    for i, (start, end, text) in enumerate(chunks):
        conn.execute(
            "INSERT INTO video_chunks (video_id, chunk_index, start_sec, end_sec, text) VALUES (?,?,?,?,?)",
            (row_id, i, start, end, text),
        )
    conn.execute(
        "UPDATE videos SET transcript_status='transcribed', transcript_error=NULL, "
        "n_chunks=?, audio_path=? WHERE id=?",
        (len(chunks), audio_path, row_id),
    )
    conn.commit()


log("=== TRA-1749 CPU2 restart ===")
log(f"Model: {MODEL_SIZE}/cpu/int8  Videos: {list(REMAINING.keys())}")

log("Loading Whisper model...")
from faster_whisper import WhisperModel
try:
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    log("Model loaded.")
except Exception as e:
    log(f"FATAL: {e}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
results = {}

for short_id, yt_id in REMAINING.items():
    log(f"\n--- {short_id} ({yt_id}) ---")
    audio_path = find_audio(yt_id)
    if not audio_path:
        log(f"  SKIP: no audio for {yt_id}")
        results[short_id] = "ERROR:audio_not_found"
        continue

    log(f"  Audio: {os.path.basename(audio_path)}")

    # Mark both rows as downloading
    for row_id in (short_id, yt_id):
        conn.execute(
            "UPDATE videos SET audio_path=?, transcript_status='downloaded' WHERE id=? AND transcript_status IN ('pending','downloaded')",
            (audio_path, row_id),
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
                    log(f"  {short_id}: {pct}% ({int(float(s.end)//60)}:{int(float(s.end)%60):02d} / "
                        f"{int(info.duration//60)}:{int(info.duration%60):02d}) elapsed={elapsed:.0f}s")
                    last_pct = pct

        elapsed = time.time() - t0
        log(f"  Segments: {len(segments)}, duration: {info.duration:.0f}s, elapsed: {elapsed:.0f}s")

        chunks = chunk_segments(segments)
        log(f"  Chunks: {len(chunks)}")

        # Write to short-ID row
        store_for_id(conn, short_id, chunks, audio_path)
        # Write to YouTube-ID row if it exists and isn't already transcribed
        yt_row = conn.execute("SELECT transcript_status FROM videos WHERE id=?", (yt_id,)).fetchone()
        if yt_row and yt_row["transcript_status"] not in ("transcribed",):
            store_for_id(conn, yt_id, chunks, audio_path)
            log(f"  Also wrote {len(chunks)} chunks to YouTube-ID row {yt_id}")

        log(f"  DONE {short_id}: {len(chunks)} chunks → short-ID row")
        results[short_id] = f"DONE:{len(chunks)}"

    except Exception as e:
        log(f"  ERROR {short_id}: {e}")
        log(traceback.format_exc())
        for row_id in (short_id, yt_id):
            conn.execute(
                "UPDATE videos SET transcript_status='failed', transcript_error=? WHERE id=?",
                (str(e)[:500], row_id),
            )
        conn.commit()
        results[short_id] = f"ERROR:{e}"

# Verification
log("\n=== VERIFICATION ===")
all_ids = ["Adjustments", "Flexibility", "Post-Tariff", "Pre-Holiday", "Range-Bound"]
for r in conn.execute(
    "SELECT id, transcript_status, n_chunks FROM videos WHERE id IN ('Post-Tariff','Range-Bound','Pre-Holiday','Flexibility','Adjustments')"
).fetchall():
    log(f"  {r['id']}: {r['transcript_status']}, {r['n_chunks']} chunks")

total = conn.execute(
    "SELECT COUNT(*) FROM video_chunks WHERE video_id IN ('Post-Tariff','Range-Bound','Pre-Holiday','Flexibility','Adjustments')"
).fetchone()[0]
log(f"Total short-ID chunks in video_chunks: {total}")

conn.close()
log("\n=== SUMMARY ===")
for k, v in results.items():
    log(f"  {k}: {v}")
done = sum(1 for v in results.values() if v.startswith("DONE"))
log(f"Success: {done}/4, Failed: {4-done}")
