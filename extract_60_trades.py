"""Extract text from Tom B's 60 Structured Trades PDF, page by page."""
import sys
import pdfplumber
from pathlib import Path

PDF = r"D:\Toms Lab\TomB's 60 Structured Trades.pdf"
OUT = Path(r"D:\Trader Lab AI Agent\tom_youtube\corpus\60-trades-raw-text.md")


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    buf = ["# TomB's 60 Structured Trades — raw text extraction\n"]
    buf.append(f"Source: {PDF}\n")
    with pdfplumber.open(PDF) as pdf:
        total = len(pdf.pages)
        buf.append(f"Total pages: {total}\n\n")
        for i, page in enumerate(pdf.pages, start=1):
            try:
                txt = page.extract_text() or ""
            except Exception as e:
                txt = f"[extract error: {e}]"
            buf.append(f"## Page {i}\n")
            buf.append(txt.strip() + "\n")
            buf.append("\n---\n\n")
            if i % 10 == 0:
                print(f"page {i}/{total}", flush=True)
    OUT.write_text("".join(buf), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
