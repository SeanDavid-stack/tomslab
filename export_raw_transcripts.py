"""
One-off: export RAW transcripts for all transcribed videos ingested up to
end of April 2026 (added_at <= 2026-04-30, since published_at is empty).

Writes:
  D:\Toms Lab\transcripts_export\<safe_title> [id].txt   (one per video, raw text)
  D:\Toms Lab\transcripts_export\_ALL_TRANSCRIPTS.txt     (everything concatenated)
  D:\Toms Lab\transcripts_export\_MANIFEST.txt            (summary of what was included)

Run: D:\Toms Lab\.venv\Scripts\python.exe export_raw_transcripts.py
"""
import sqlite3
import re
from pathlib import Path

DB = Path(r"D:\Toms Lab\data\tomslab.db")
OUT = Path(r"D:\Toms Lab\transcripts_export")
CUTOFF = "2026-04-30"  # inclusive

OUT.mkdir(parents=True, exist_ok=True)
man = []
def m(s=""):
    print(s, flush=True)
    man.append(str(s))

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def safe(s, n=90):
    s = re.sub(r'[\\/:*?"<>|]+', "_", str(s)).strip()
    return (s[:n] or "untitled")

vids = cur.execute(
    "SELECT * FROM videos WHERE transcript_status='transcribed' "
    "AND substr(added_at,1,10) <= ? ORDER BY added_at, title",
    (CUTOFF,),
).fetchall()

m(f"Cutoff (added_at inclusive): {CUTOFF}")
m(f"Transcribed videos on/before cutoff: {len(vids)}")
m("")

combined = []
exported = 0
empty = []

for v in vids:
    vid, title = v["id"], v["title"]
    chunks = cur.execute(
        "SELECT text FROM video_chunks WHERE video_id=? ORDER BY chunk_index",
        (vid,),
    ).fetchall()
    body = "\n".join((c["text"] or "").strip() for c in chunks).strip()
    if not body:
        empty.append(title)
        continue
    header = (
        f"TITLE: {title}\n"
        f"VIDEO_ID: {vid}\n"
        f"URL: {v['url']}\n"
        f"ADDED: {v['added_at']}\n"
        f"DURATION_SEC: {v['duration_sec']}\n"
        + "=" * 70 + "\n"
    )
    (OUT / f"{safe(title)} [{safe(vid,20)}].txt").write_text(
        header + body + "\n", encoding="utf-8"
    )
    combined.append(header + body + "\n")
    exported += 1

(OUT / "_ALL_TRANSCRIPTS.txt").write_text(
    ("\n\n" + "#" * 70 + "\n\n").join(combined), encoding="utf-8"
)

m(f"EXPORTED files: {exported}")
m(f"Skipped (no chunk text): {len(empty)}")
for e in empty:
    m(f"   - {e}")
m("")
m(f"Output folder: {OUT}")
m(f"Combined file: {OUT / '_ALL_TRANSCRIPTS.txt'}")

(OUT / "_MANIFEST.txt").write_text("\n".join(man) + "\n", encoding="utf-8")
conn.close()
print("DONE")
