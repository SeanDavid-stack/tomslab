"""Render top-strip (banner region) of each page at high DPI for title reading."""
import pypdfium2 as pdfium
from pathlib import Path
from PIL import Image

PDF = r"D:\Toms Lab\TomB's 60 Structured Trades.pdf"
OUT = Path(r"D:\Toms Lab\banners")
OUT.mkdir(parents=True, exist_ok=True)

pdf = pdfium.PdfDocument(PDF)
scale = 300 / 72  # 300 DPI for banners
for i in range(len(pdf)):
    page = pdf[i]
    pil = page.render(scale=scale).to_pil()
    # Crop top 15% of page (banner strip)
    w, h = pil.size
    top = pil.crop((0, 0, w, int(h * 0.16)))
    # Keep file sizes small — resize to max 1600px wide
    if top.width > 1600:
        ratio = 1600 / top.width
        top = top.resize((1600, int(top.height * ratio)))
    top.save(OUT / f"banner-{i+1:02d}.png")
    if (i + 1) % 10 == 0:
        print(f"banner {i+1}", flush=True)
print(f"Done {len(pdf)} banners in {OUT}")
