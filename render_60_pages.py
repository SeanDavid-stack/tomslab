"""Render all 65 pages of the 60 Structured Trades PDF at 150 DPI to target dir."""
import pypdfium2 as pdfium
from pathlib import Path

PDF = r"D:\Toms Lab\TomB's 60 Structured Trades.pdf"
OUT = Path(r"D:\Trader Lab AI Agent\tom_youtube\corpus\60-trades-pages")
OUT.mkdir(parents=True, exist_ok=True)

pdf = pdfium.PdfDocument(PDF)
scale = 150 / 72  # 150 DPI
for i in range(len(pdf)):
    page = pdf[i]
    pil = page.render(scale=scale).to_pil()
    # Downscale if huge to fit read tool limit (~2000px max dim tends to be fine)
    max_dim = 1800
    w, h = pil.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        pil = pil.resize((int(w * ratio), int(h * ratio)))
    pil.save(OUT / f"page-{i+1:02d}.png")
    if (i + 1) % 10 == 0:
        print(f"rendered {i+1}/{len(pdf)}", flush=True)
print(f"Done. {len(pdf)} pages in {OUT}")
