"""
Retry Whisper transcription for 5 pending TRA-1610 videos.
Run from D:\Toms Lab with: python transcribe_pending.py
"""
import sqlite3
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

DB_PATH = Path(r"D:\Toms Lab\data\tomslab.db")
TOM_VIDEOS = Path(r"D:\Tom Videos")

# Mapping: db id -> audio filename (partial match is fine, using YouTube ID)
PENDING = {
    "Adjustments":  "QBvdndZquYc",
    "Flexibility":  "1BTWkJqvkYU",
    "Post-Tariff":  "wxlRMLVVh74",
    "Pre-Holiday":  "NozNOEKa3do",
    "Range-Bound":  "a3SwWfl_lx0",
}


def find_audio(yt_id: str) -> Path | None:
    matches = list(TOM_VIDEOS.glob(f"*{yt_id}*"))
    for m in matches:
        if m.is_file() and m.suffix.lower() in (".webm", ".mp3", ".m4a", ".opus"):
            return m
    return None


def main():
    import sys
    sys.path.insert(0, str(Path(r"D:\Toms Lab\src")))
    from tomslab.ingest.youtube import transcribe, chunk_segments, store_chunks

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    results = {}
    total_chunks = 0

    for db_id, yt_id in PENDING.items():
        audio = find_audio(yt_id)
        if audio is None:
            log.error("[%s] No audio file found for yt_id=%s", db_id, yt_id)
            results[db_id] = "ERROR: audio not found"
            continue

        log.info("[%s] Found audio: %s", db_id, audio)

        # Update audio_path + set to downloaded
        conn.execute(
            "UPDATE videos SET audio_path=?, transcript_status='downloaded' WHERE id=?",
            (str(audio), db_id),
        )
        conn.commit()

        # Transcribe
        log.info("[%s] Starting Whisper transcription...", db_id)
        try:
            def _on_seg(end_sec, total_sec, _id=db_id):
                if total_sec > 0:
                    pct = int(end_sec * 100 / total_sec)
                    if pct % 10 == 0:
                        log.info("[%s] %d%% (%ds / %ds)", _id, pct, int(end_sec), int(total_sec))

            segs = transcribe(audio, on_segment=_on_seg)
            log.info("[%s] Got %d segments", db_id, len(segs))

            chunks = chunk_segments(segs)
            log.info("[%s] Created %d chunks", db_id, len(chunks))

            n_stored = store_chunks(conn, db_id, chunks)
            conn.execute(
                "UPDATE videos SET transcript_status='transcribed', "
                "transcript_error=NULL, n_chunks=? WHERE id=?",
                (n_stored, db_id),
            )
            conn.commit()
            total_chunks += n_stored
            results[db_id] = f"OK: {n_stored} chunks"
            log.info("[%s] Done. %d chunks stored.", db_id, n_stored)

        except Exception as exc:
            log.error("[%s] FAILED: %s", db_id, exc)
            conn.execute(
                "UPDATE videos SET transcript_status='failed', transcript_error=? WHERE id=?",
                (str(exc)[:500], db_id),
            )
            conn.commit()
            results[db_id] = f"ERROR: {exc}"

    # Verification query
    log.info("=== FINAL VERIFICATION ===")
    rows = conn.execute(
        "SELECT id, transcript_status, n_chunks FROM videos "
        "WHERE id IN ('Post-Tariff','Range-Bound','Pre-Holiday','Flexibility','Adjustments')"
    ).fetchall()
    for r in rows:
        log.info("  %s: status=%s, n_chunks=%d", r["id"], r["transcript_status"], r["n_chunks"])

    chunk_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM video_chunks "
        "WHERE video_id IN ('Post-Tariff','Range-Bound','Pre-Holiday','Flexibility','Adjustments')"
    ).fetchone()["cnt"]
    log.info("Chunk COUNT(*) from video_chunks: %d", chunk_count)
    log.info("Total chunks added this run: %d", total_chunks)
    log.info("Results: %s", results)

    conn.close()
    return results


if __name__ == "__main__":
    main()
