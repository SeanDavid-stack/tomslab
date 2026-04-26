"""Re-render TomB's 60 Structured Trades PDF at 300 DPI for legible annotation reading.

Splits each page into TWO halves (left and right) so the resulting PNGs stay below
Read-tool size limits while keeping per-pixel detail of Tom's Bookmap callouts.
"""
import pdfplumber
import os
from PIL import Image

PDF = r"D:\Toms Lab\TomB's 60 Structured Trades.pdf"
OUT = r"D:\Trader Lab AI Agent\tom_youtube\corpus\60-trades-pages-300dpi"
DPI = 300

os.makedirs(OUT, exist_ok=True)

with pdfplumber.open(PDF) as pdf:
    print(f"Total pages: {len(pdf.pages)}")
    for i, page in enumerate(pdf.pages, start=1):
        # Render full page at 300 DPI
        im = page.to_image(resolution=DPI).original  # PIL Image
        w, h = im.size
        # Save full page (compressed)
        full_path = os.path.join(OUT, f"page-{i:02d}.png")
        # Limit max width to ~2400 to keep files reasonable but text still legible
        if w > 2400:
            ratio = 2400 / w
            im_resized = im.resize((2400, int(h * ratio)), Image.LANCZOS)
        else:
            im_resized = im
        im_resized.save(full_path, "PNG", optimize=True)

        # Also create LEFT and RIGHT halves at full 300 DPI for fine annotation reading
        mid = w // 2
        left = im.crop((0, 0, mid + 100, h))  # 100 px overlap
        right = im.crop((mid - 100, 0, w, h))
        # Resize halves to ~1800 wide if needed
        for half_im, suffix in [(left, "L"), (right, "R")]:
            hw, hh = half_im.size
            if hw > 1800:
                r = 1800 / hw
                half_im = half_im.resize((1800, int(hh * r)), Image.LANCZOS)
            half_im.save(os.path.join(OUT, f"page-{i:02d}-{suffix}.png"),
                         "PNG", optimize=True)
        print(f"  page {i:02d} ok  full={im_resized.size}")
print("Done.")
