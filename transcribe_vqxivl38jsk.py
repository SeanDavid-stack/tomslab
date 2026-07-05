"""
TRA-1749: Retranscribe VqXIvL38jsk (corrupt 5-chunk run, now cleared).
CPU large-v3/int8, CUDA disabled.
Log: D:\Toms Lab\logs\transcribe_vqxivl38jsk.log
"""
import os, sys, time, sqlite3, traceback

os.environ["CUDA_VISIBLE_DEVICES"] = ""
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

VIDEO_ID = "VqXIvL38jsk"
AUDIO_PATH = r"D:\Tom Videos\Live Streaming Futures with Tom B at the Traders Lab [VqXIvL38jsk].webm"
DB_PATH = r"D:\Toms Lab\data\tomslab.db"
LOG_PATH = r"D:\Toms Lab\logs\transcribe_vqxivl38jsk.log"
MODEL_SIZE = "large-v3"

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

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

log(f"=== TRA-1749: retranscribe {VIDEO_ID} ===")
log(f"Audio: {AUDIO_PATH}")

log("Loading Whisper large-v3/cpu/int8...")
from faster_whisper import WhisperModel
try:
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    log("Model loaded.")
except Exception as e:
    log(f"FATAL: {e}")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("UPDATE videos SET audio_path=?, transcript_status='downloaded' WHERE id=?",
             (AUDIO_PATH, VIDEO_ID))
conn.commit()

try:
    log("Transcribing...")
    t0 = time.time()
    segments_gen, info = model.transcribe(
        AUDIO_PATH, beam_size=5,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 700},
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
                log(f"  {pct}% ({int(float(s.end)//60)}:{int(float(s.end)%60):02d} / "
                    f"{int(info.duration//60)}:{int(info.duration%60):02d}) elapsed={elapsed:.0f}s")
                last_pct = pct

    elapsed = time.time() - t0
    log(f"Transcription done: {len(segments)} segments, {info.duration:.0f}s audio, {elapsed:.0f}s elapsed")

    chunks = chunk_segments(segments)
    log(f"Chunks: {len(chunks)}")

    conn.execute("DELETE FROM video_chunks WHERE video_id=?", (VIDEO_ID,))
    for i, (start, end, text) in enumerate(chunks):
        conn.execute(
            "INSERT INTO video_chunks (video_id, chunk_index, start_sec, end_sec, text) VALUES (?,?,?,?,?)",
            (VIDEO_ID, i, start, end, text),
        )
    conn.execute(
        "UPDATE videos SET transcript_status='transcribed', transcript_error=NULL, n_chunks=? WHERE id=?",
        (len(chunks), VIDEO_ID),
    )
    conn.commit()
    log(f"DONE: {len(chunks)} chunks stored for {VIDEO_ID}")

    # Verify
    r = conn.execute("SELECT id, transcript_status, n_chunks FROM videos WHERE id=?", (VIDEO_ID,)).fetchone()
    actual = conn.execute("SELECT COUNT(*) FROM video_chunks WHERE video_id=?", (VIDEO_ID,)).fetchone()[0]
    log(f"Verification: {dict(r)}, actual chunk count: {actual}")

except Exception as e:
    log(f"ERROR: {e}")
    log(traceback.format_exc())
    conn.execute("UPDATE videos SET transcript_status='failed', transcript_error=? WHERE id=?",
                 (str(e)[:500], VIDEO_ID))
    conn.commit()
    sys.exit(1)

conn.close()
log("=== Complete ===")
