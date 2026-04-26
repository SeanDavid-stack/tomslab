"""Render USER_MANUAL.md to a polished, branded PDF.

The demo/_build_pdfs.py produces a workmanlike PDF for internal demo
materials. This script is for the USER_MANUAL specifically — it gets a
proper cover page, gold accent rules under H1 sections, alternating-row
tables, and yellow callout boxes around the ⚠️ warning sections that
the dry-Markdown source can't visually emphasise.

Output: pack-out/USER_MANUAL.pdf

Run from repo root:
    .\\.venv\\Scripts\\python.exe packaging\\build_user_manual_pdf.py
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------
GOLD = colors.HexColor("#C9A227")           # Tom-accent gold (a hair darker than #FFC857 for print)
GOLD_FAINT = colors.HexColor("#F6E9B7")
BLUE = colors.HexColor("#3D58D6")           # SDES brand blue
INK = colors.HexColor("#1B1B1F")
GREY = colors.HexColor("#5C5C66")
RULE = colors.HexColor("#E1E1E6")
ROW_ALT = colors.HexColor("#F7F7F9")
WARN_BG = colors.HexColor("#FFF4D6")
WARN_BORDER = colors.HexColor("#E8C063")


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
_FONTS_DIR = Path("C:/Windows/Fonts")
pdfmetrics.registerFont(TTFont("Body", str(_FONTS_DIR / "calibri.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", str(_FONTS_DIR / "calibrib.ttf")))
pdfmetrics.registerFont(TTFont("Body-Italic", str(_FONTS_DIR / "calibrii.ttf")))
pdfmetrics.registerFont(TTFont("Body-BoldItalic", str(_FONTS_DIR / "calibriz.ttf")))
pdfmetrics.registerFont(TTFont("Mono", str(_FONTS_DIR / "consola.ttf")))
pdfmetrics.registerFontFamily(
    "Body",
    normal="Body",
    bold="Body-Bold",
    italic="Body-Italic",
    boldItalic="Body-BoldItalic",
)


# ---------------------------------------------------------------------------
# Inline markdown
# ---------------------------------------------------------------------------
INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
INLINE_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
INLINE_CODE = re.compile(r"`([^`]+)`")
INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def inline(text: str) -> str:
    code_spans: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans)-1}\x00"

    text = INLINE_CODE.sub(_stash_code, text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # links: [label](url) -> ReportLab <link> tag
    text = INLINE_LINK.sub(
        lambda m: f'<link href="{m.group(2)}" color="#3D58D6"><u>{m.group(1)}</u></link>',
        text,
    )
    text = INLINE_BOLD.sub(r"<b>\1</b>", text)
    text = INLINE_ITALIC.sub(r"<i>\1</i>", text)
    for idx, raw in enumerate(code_spans):
        safe = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(
            f"\x00CODE{idx}\x00",
            f'<font name="Mono" size="9.5" backColor="#F0F0F2">&#160;{safe}&#160;</font>',
        )
    return text


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base, fontName="Body-Bold",
            fontSize=44, leading=52, alignment=1, textColor=INK,
            spaceBefore=0, spaceAfter=4,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle", parent=base, fontName="Body",
            fontSize=18, leading=22, alignment=1, textColor=GREY,
            spaceAfter=22,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta", parent=base, fontName="Body",
            fontSize=11, leading=14, alignment=1, textColor=GREY,
            spaceAfter=2,
        ),
        "cover_meta_strong": ParagraphStyle(
            "cover_meta_strong", parent=base, fontName="Body-Bold",
            fontSize=12, leading=15, alignment=1, textColor=INK,
            spaceAfter=2,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base, fontName="Body-Bold",
            fontSize=22, leading=26, textColor=INK,
            spaceBefore=8, spaceAfter=2, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName="Body-Bold",
            fontSize=15, leading=18, textColor=BLUE,
            spaceBefore=14, spaceAfter=4, keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base, fontName="Body-Bold",
            fontSize=12, leading=15, textColor=INK,
            spaceBefore=10, spaceAfter=2, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "body", parent=base, fontName="Body",
            fontSize=11, leading=15, textColor=INK,
            spaceAfter=4,
        ),
        "intro": ParagraphStyle(
            "intro", parent=base, fontName="Body-Italic",
            fontSize=11, leading=15, textColor=GREY,
            spaceAfter=8,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base, fontName="Body",
            fontSize=11, leading=15, textColor=INK,
            leftIndent=18, bulletIndent=4, spaceAfter=2,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base, fontName="Body-Italic",
            fontSize=11, leading=15, leftIndent=20, rightIndent=20,
            spaceBefore=4, spaceAfter=6, textColor=GREY,
        ),
        "warn": ParagraphStyle(
            "warn", parent=base, fontName="Body",
            fontSize=10.5, leading=14, textColor=INK,
            leftIndent=12, rightIndent=12, spaceBefore=2, spaceAfter=2,
        ),
        "tbl_cell": ParagraphStyle(
            "tbl_cell", parent=base, fontName="Body",
            fontSize=10, leading=13, textColor=INK,
        ),
        "tbl_head": ParagraphStyle(
            "tbl_head", parent=base, fontName="Body-Bold",
            fontSize=10, leading=13, textColor=colors.white,
        ),
    }


# ---------------------------------------------------------------------------
# Cover + page header
# ---------------------------------------------------------------------------
def cover_story(version: str, date_label: str, S) -> list:
    out: list = []
    out.append(Spacer(1, 1.7 * inch))
    out.append(Paragraph("Tom's Lab", S["cover_title"]))
    out.append(Paragraph("User Manual", S["cover_subtitle"]))
    out.append(Spacer(1, 0.3 * inch))
    out.append(HRFlowable(
        width="35%", thickness=2.4, color=GOLD,
        hAlign="CENTER", spaceBefore=2, spaceAfter=18,
    ))
    out.append(Paragraph(f"Version {version}", S["cover_meta_strong"]))
    out.append(Paragraph(date_label, S["cover_meta"]))
    out.append(Spacer(1, 0.5 * inch))
    out.append(Paragraph(
        "A free desktop library / searchable encyclopedia "
        "of Tom B's publicly-shared trading material.",
        ParagraphStyle(
            "cover_lede", parent=S["body"], fontSize=12, leading=16,
            alignment=1, textColor=GREY,
            leftIndent=0.7 * inch, rightIndent=0.7 * inch,
        ),
    ))
    out.append(Spacer(1, 1.5 * inch))
    out.append(HRFlowable(
        width="55%", thickness=0.4, color=RULE,
        hAlign="CENTER", spaceBefore=2, spaceAfter=10,
    ))
    out.append(Paragraph(
        "Tom B has not reviewed or endorsed this app. "
        "Independent third-party project published by SDE-Software (SDES.DEV).",
        ParagraphStyle(
            "cover_disc", parent=S["intro"], fontSize=9, leading=12,
            alignment=1, textColor=GREY,
            leftIndent=0.6 * inch, rightIndent=0.6 * inch,
        ),
    ))
    out.append(PageBreak())
    return out


def _page_decor(canvas, doc):
    """Footer with page number + small wordmark."""
    canvas.saveState()
    page_num = canvas.getPageNumber()
    if page_num == 1:
        canvas.restoreState()
        return
    canvas.setFont("Body", 8.5)
    canvas.setFillColor(GREY)
    canvas.drawString(0.85 * inch, 0.55 * inch, "Tom's Lab — User Manual")
    canvas.drawRightString(LETTER[0] - 0.85 * inch, 0.55 * inch, str(page_num))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(0.85 * inch, 0.72 * inch, LETTER[0] - 0.85 * inch, 0.72 * inch)
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Markdown -> flowables
# ---------------------------------------------------------------------------
def parse_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_table_separator(line: str) -> bool:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    body = s.strip("|")
    return all(re.match(r":?-{2,}:?", c.strip()) for c in body.split("|"))


def render_md(md_path: Path, S) -> list:
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Blank line
        if not stripped:
            i += 1
            continue

        # Horizontal rule
        if re.fullmatch(r"-{3,}\s*", stripped):
            out.append(HRFlowable(
                width="100%", thickness=0.6, color=RULE,
                spaceBefore=4, spaceAfter=8,
            ))
            i += 1
            continue

        # Tables
        if (
            stripped.startswith("|") and stripped.endswith("|")
            and i + 1 < len(lines) and is_table_separator(lines[i + 1])
        ):
            header = parse_table_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(parse_table_row(lines[i]))
                i += 1
            tbl_data = [
                [Paragraph(inline(c), S["tbl_head"]) for c in header],
            ] + [
                [Paragraph(inline(c), S["tbl_cell"]) for c in row]
                for row in rows
            ]
            ncols = len(header)
            col_widths = [(LETTER[0] - 1.7 * inch) / ncols] * ncols
            tbl = Table(tbl_data, colWidths=col_widths, hAlign="LEFT")
            tstyle = TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Body-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, BLUE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.4, RULE),
            ])
            for r in range(1, len(tbl_data)):
                if r % 2 == 0:
                    tstyle.add("BACKGROUND", (0, r), (-1, r), ROW_ALT)
            tbl.setStyle(tstyle)
            out.append(Spacer(1, 4))
            out.append(tbl)
            out.append(Spacer(1, 8))
            continue

        # Headings — h1 = section, gets gold rule + page break before
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            if out:  # not the first one
                out.append(PageBreak())
            out.append(Paragraph(inline(heading), S["h1"]))
            out.append(HRFlowable(
                width="22%", thickness=2.4, color=GOLD,
                hAlign="LEFT", spaceBefore=2, spaceAfter=14,
            ))
            i += 1
            continue
        if stripped.startswith("## "):
            out.append(Paragraph(inline(stripped[3:].strip()), S["h2"]))
            i += 1
            continue
        if stripped.startswith("### "):
            out.append(Paragraph(inline(stripped[4:].strip()), S["h3"]))
            i += 1
            continue

        # Blockquote
        if stripped.startswith("> "):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                quote_lines.append(lines[i].strip()[2:])
                i += 1
            quote_text = " ".join(quote_lines)
            # Detect "callout" — leading ⚠️ becomes a yellow box
            if quote_text.startswith("⚠"):
                callout = Table(
                    [[Paragraph(inline(quote_text), S["warn"])]],
                    colWidths=[LETTER[0] - 1.7 * inch],
                )
                callout.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), WARN_BG),
                    ("BOX", (0, 0), (-1, -1), 0.8, WARN_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]))
                out.append(Spacer(1, 4))
                out.append(callout)
                out.append(Spacer(1, 6))
            else:
                out.append(Paragraph(inline(quote_text), S["quote"]))
            continue

        # Bulleted list
        if re.match(r"^[-*]\s+", stripped):
            list_items: list[str] = []
            while i < len(lines):
                bs = lines[i].strip()
                if re.match(r"^[-*]\s+", bs):
                    list_items.append(re.sub(r"^[-*]\s+", "", bs))
                    i += 1
                elif bs == "" and i + 1 < len(lines) and re.match(r"^[-*]\s+", lines[i + 1].strip()):
                    i += 1
                else:
                    break
            for item in list_items:
                out.append(Paragraph(
                    inline(item), S["bullet"], bulletText="•",
                ))
            out.append(Spacer(1, 2))
            continue

        # Numbered list
        if re.match(r"^\d+\.\s+", stripped):
            num_items: list[str] = []
            while i < len(lines):
                ns = lines[i].strip()
                if re.match(r"^\d+\.\s+", ns):
                    num_items.append(re.sub(r"^\d+\.\s+", "", ns))
                    i += 1
                else:
                    break
            for n, item in enumerate(num_items, 1):
                out.append(Paragraph(
                    inline(item), S["bullet"], bulletText=f"{n}.",
                ))
            out.append(Spacer(1, 2))
            continue

        # Default: paragraph (collect continuation lines)
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt
                or nxt.startswith(("#", "-", "*", ">", "|", "```"))
                or re.match(r"^\d+\.\s+", nxt)
                or re.fullmatch(r"-{3,}\s*", nxt)):
                break
            para_lines.append(nxt)
            i += 1
        out.append(Paragraph(inline(" ".join(para_lines)), S["body"]))

    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    md_path = repo_root / "USER_MANUAL.md"
    if not md_path.exists():
        raise SystemExit(f"USER_MANUAL.md not found at {md_path}")

    out_dir = repo_root / "pack-out"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "USER_MANUAL.pdf"

    # Read app version from src/tomslab/__init__.py so the cover stays in sync
    init_text = (repo_root / "src" / "tomslab" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    version = m.group(1) if m else "1.0.0"

    from datetime import datetime
    date_label = datetime.now().strftime("%B %Y")

    S = styles()
    story = cover_story(version, date_label, S) + render_md(md_path, S)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.95 * inch,
        bottomMargin=0.95 * inch,
        title="Tom's Lab — User Manual",
        author="SDE-Software",
        subject=f"User Manual v{version}",
    )
    doc.build(story, onFirstPage=_page_decor, onLaterPages=_page_decor)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
