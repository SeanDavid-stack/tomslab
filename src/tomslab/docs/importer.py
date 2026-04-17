"""Ingest a folder of PDFs into the documents + document_pages tables.

For each PDF:
  1. Insert a `documents` row (idempotent on filename).
  2. Render every page to PNG under %APPDATA%/TomsLab/data/doc_images/<doc_id>/.
  3. Try pdfplumber for per-page extracted text.
  4. If a page has very little extracted text, run Gemini Vision OCR.
  5. Store one row per page in `document_pages`.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import pdfplumber

from tomslab import db as dbmod
from tomslab.docs.ocr import OCRRateLimited, make_ocr
from tomslab.docs.pdf_render import render_pdf_to_pngs
from tomslab.paths import data_dir

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]   # (status, current, total)

OCR_THRESHOLD = 50   # if extracted_text is below this many chars, fall back to OCR


# Classification used to set documents.author / doc_type. Keeps Tom's work
# separated from third-party books so we can prioritise it in retrieval.
_TOM_AUTHORED = {
    "Trader_Lab_Glossary.pdf",
    "Toms_Bookmap_Settings.pdf",
    "Auction Market Theory-101.pdf",
    "Stats by Target.pdf",
    "Mean_Reversion_Structured_Trade.pdf",
    "Opening_Context_Alignment.pdf",
    "TomB's 60 Structured Trades.pdf",
    "Market_Structure.pdf",
}
_THIRD_PARTY_HINTS = (
    "best loser wins",
    "trade your way",
    "tharp",
    "hougaard",
)


def classify(filename: str) -> tuple[str, str]:
    """Return (author, doc_type)."""
    if filename in _TOM_AUTHORED:
        return ("tom_b", "authoritative")
    low = filename.lower()
    if any(h in low for h in _THIRD_PARTY_HINTS):
        return ("third_party", "reference")
    return ("unknown", "reference")


@dataclass
class DocImportResult:
    document_id: int
    filename: str
    pages_added: int
    pages_skipped: int
    ocr_pages: int


def _noop(_s: str, _c: int, _t: int) -> None:
    pass


def import_folder(
    folder: Path,
    conn: sqlite3.Connection | None = None,
    progress: ProgressFn = _noop,
    use_ocr: bool = True,
) -> list[DocImportResult]:
    folder = Path(folder)
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        return []

    own_conn = conn is None
    if conn is None:
        conn = dbmod.connect()
        dbmod.initialise(conn)

    try:
        results = []
        for i, pdf in enumerate(pdfs, start=1):
            progress(f"Importing {pdf.name}", i - 1, len(pdfs))
            try:
                res = import_pdf(pdf, conn=conn, use_ocr=use_ocr,
                                 progress=lambda s, c, t: progress(f"{pdf.name}: {s}", c, t))
                results.append(res)
            except Exception as exc:
                log.error("Failed to import %s: %s", pdf.name, exc)
        progress("Done", len(pdfs), len(pdfs))
        return results
    finally:
        if own_conn:
            conn.close()


def import_pdf(
    pdf_path: Path,
    conn: sqlite3.Connection | None = None,
    use_ocr: bool = True,
    progress: ProgressFn = _noop,
) -> DocImportResult:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    own_conn = conn is None
    if conn is None:
        conn = dbmod.connect()
        dbmod.initialise(conn)

    try:
        return _run_import(pdf_path, conn, use_ocr=use_ocr, progress=progress)
    finally:
        if own_conn:
            conn.close()


def _get_or_create_document(
    conn: sqlite3.Connection, pdf_path: Path, page_count: int
) -> int:
    row = conn.execute(
        "SELECT id FROM documents WHERE filename = ?", (pdf_path.name,)
    ).fetchone()
    if row:
        return int(row["id"])
    author, doc_type = classify(pdf_path.name)
    now = datetime.now(timezone.utc).isoformat()
    title = pdf_path.stem.replace("_", " ")
    cur = conn.execute(
        "INSERT INTO documents(title, filename, author, doc_type, source_path, page_count, added_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (title, pdf_path.name, author, doc_type, str(pdf_path), page_count, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def _run_import(
    pdf_path: Path, conn: sqlite3.Connection, use_ocr: bool, progress: ProgressFn
) -> DocImportResult:
    progress("Rendering pages", 0, 0)

    # pdfplumber for text, pypdfium2 for images
    text_by_page: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pages = len(pdf.pages)
        for pg in pdf.pages:
            text_by_page.append((pg.extract_text() or "").strip())

    doc_id = _get_or_create_document(conn, pdf_path, total_pages)

    # Destination for PNGs: %APPDATA%/TomsLab/data/doc_images/<doc_id>/
    img_root = data_dir() / "doc_images" / str(doc_id)
    png_paths = render_pdf_to_pngs(pdf_path, img_root, dpi=150)

    # Map existing pages to their stored OCR text so we can retry if empty.
    existing_by_num: dict[int, sqlite3.Row] = {
        int(r["page_num"]): r
        for r in conn.execute(
            "SELECT page_num, ocr_text, extracted_text FROM document_pages WHERE document_id = ?",
            (doc_id,),
        )
    }

    ocr = None
    ocr_needs_rate_limit = False
    if use_ocr:
        try:
            ocr = make_ocr(conn)
            ocr_needs_rate_limit = type(ocr).__name__ == "GeminiOCR"
        except Exception as exc:
            log.warning("OCR unavailable — image pages will have no text: %s", exc)
            ocr = None

    pages_added = 0
    pages_skipped = 0
    ocr_pages = 0
    now = datetime.now(timezone.utc).isoformat()
    t0_last_ocr = 0.0
    # Only Gemini's free tier needs pacing.
    rate_gap = 60.0 / 8 if ocr_needs_rate_limit else 0.0

    for page_num, png in enumerate(png_paths, start=1):
        extracted = text_by_page[page_num - 1] if page_num - 1 < len(text_by_page) else ""
        existing = existing_by_num.get(page_num)

        # Decide: skip entirely, OCR-retry in place, or fresh insert.
        if existing is not None:
            existing_ocr = existing["ocr_text"] or ""
            existing_ext = existing["extracted_text"] or ""
            # skip if we already have text OR extracted is sufficient
            if existing_ocr or len(existing_ext) >= OCR_THRESHOLD:
                pages_skipped += 1
                progress(f"Page {page_num}/{total_pages} (skip)", page_num, total_pages)
                continue
            # Need to retry OCR — fall through to OCR path below.

        ocr_text = ""
        text_source = "extracted"

        need_ocr = ocr is not None and len(extracted) < OCR_THRESHOLD
        if need_ocr:
            wait = rate_gap - (time.monotonic() - t0_last_ocr)
            if wait > 0:
                time.sleep(wait)
            try:
                ocr_text = ocr.ocr_image(png)
                t0_last_ocr = time.monotonic()
            except OCRRateLimited as exc:
                log.warning("giving up on page %d of %s after retries: %s",
                            page_num, pdf_path.name, exc)
                t0_last_ocr = time.monotonic()
                # leave/ensure row without OCR — we can re-run later
                ocr_text = ""
            if ocr_text:
                ocr_pages += 1
                text_source = "ocr" if not extracted else "combined"

        # Don't write a row with nothing useful — that way the next run retries.
        has_useful = bool(ocr_text) or len(extracted) >= 1
        if not has_useful and existing is None:
            progress(f"Page {page_num}/{total_pages} (no text, deferred)", page_num, total_pages)
            continue

        if existing is not None:
            # UPDATE path: refresh OCR on the existing row
            conn.execute(
                "UPDATE document_pages SET ocr_text = ?, text_source = ?, added_at = ? "
                "WHERE document_id = ? AND page_num = ?",
                (ocr_text, text_source, now, doc_id, page_num),
            )
        else:
            conn.execute(
                "INSERT INTO document_pages("
                "document_id, page_num, rendered_path, extracted_text, ocr_text, text_source, added_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (doc_id, page_num, str(png), extracted, ocr_text, text_source, now),
            )
            pages_added += 1
        conn.commit()
        progress(f"Page {page_num}/{total_pages}  ({text_source})", page_num, total_pages)

    return DocImportResult(
        document_id=doc_id,
        filename=pdf_path.name,
        pages_added=pages_added,
        pages_skipped=pages_skipped,
        ocr_pages=ocr_pages,
    )


def best_text(page_row: sqlite3.Row) -> str:
    """Return the best available text for a doc page (OCR preferred over extracted)."""
    ocr = (page_row["ocr_text"] or "").strip()
    if ocr:
        return ocr
    return (page_row["extracted_text"] or "").strip()
