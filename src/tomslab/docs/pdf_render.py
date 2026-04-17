"""Render PDF pages to PNG files on disk using pypdfium2."""
from __future__ import annotations

import logging
from pathlib import Path

import pypdfium2 as pdfium

log = logging.getLogger(__name__)


def render_pdf_to_pngs(
    pdf_path: Path,
    out_dir: Path,
    dpi: int = 150,
) -> list[Path]:
    """Render every page of ``pdf_path`` into ``out_dir`` as PNG.

    Returns the list of written PNG paths, in page order (1-indexed in filename).
    Existing files are preserved — idempotent.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(pdf_path))
    scale = dpi / 72.0   # 72 = PDF default DPI
    out: list[Path] = []
    for i, page in enumerate(pdf, start=1):
        target = out_dir / f"page_{i:04d}.png"
        if not target.exists():
            pil = page.render(scale=scale).to_pil()
            # JPEG-quality-tier PNG is overkill; straight PIL save is fine
            pil.save(str(target), format="PNG", optimize=True)
        out.append(target)
    pdf.close()
    return out


def page_count(pdf_path: Path) -> int:
    pdf = pdfium.PdfDocument(str(pdf_path))
    n = len(pdf)
    pdf.close()
    return n
