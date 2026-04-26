"""Markdown -> PDF converter for the Tom's Lab demo handouts.

Handles the markdown features used in docs 1-4:
  # / ## / ###            headings
  *italic* / **bold**     inline
  `code`                  inline (monospace)
  ---                     horizontal rule
  -   / *                 bullet lists
  - [ ] / - [x]           checkbox lists
  > block quote
  | tbl | tbl |           tables (with |---|---| header separator)
  emoji 🗣 🖱 ❓           rewritten to bold text labels
  blank lines             vertical space (preserves Doc B reply lines)
"""

from __future__ import annotations

import re
import sys
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


_FONTS_DIR = Path("C:/Windows/Fonts")
pdfmetrics.registerFont(TTFont("Body", str(_FONTS_DIR / "arial.ttf")))
pdfmetrics.registerFont(TTFont("Body-Bold", str(_FONTS_DIR / "arialbd.ttf")))
pdfmetrics.registerFont(TTFont("Body-Italic", str(_FONTS_DIR / "ariali.ttf")))
pdfmetrics.registerFont(TTFont("Body-BoldItalic", str(_FONTS_DIR / "arialbi.ttf")))
pdfmetrics.registerFont(TTFont("Mono", str(_FONTS_DIR / "consola.ttf")))
pdfmetrics.registerFontFamily(
    "Body",
    normal="Body",
    bold="Body-Bold",
    italic="Body-Italic",
    boldItalic="Body-BoldItalic",
)


INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
INLINE_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
INLINE_CODE = re.compile(r"`([^`]+)`")

EMOJI_MAP = {
    "🗣": "<b>SAY</b>",
    "🖱": "<b>DO</b>",
    "❓": "<b>ASK</b>",
    "✅": "[x]",
    "⚠️": "[!]",
    "—": "—",  # ensure em-dash is preserved
}


def preprocess_emoji(text: str) -> str:
    for k, v in EMOJI_MAP.items():
        text = text.replace(k, v)
    return text


def inline(text: str) -> str:
    """Markdown inline -> reportlab markup. Order: code (no further processing inside), then bold/italic."""
    text = preprocess_emoji(text)
    # extract code spans first to protect them from escaping
    code_spans: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code_spans.append(m.group(1))
        return f"\x00CODE{len(code_spans)-1}\x00"

    text = INLINE_CODE.sub(_stash_code, text)

    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = INLINE_BOLD.sub(r"<b>\1</b>", text)
    text = INLINE_ITALIC.sub(r"<i>\1</i>", text)

    # restore <SAY>/<DO>/<ASK> bold tags that emoji_map injected pre-escape
    text = text.replace("&lt;b&gt;SAY&lt;/b&gt;", "<b>SAY</b>")
    text = text.replace("&lt;b&gt;DO&lt;/b&gt;", "<b>DO</b>")
    text = text.replace("&lt;b&gt;ASK&lt;/b&gt;", "<b>ASK</b>")

    # restore code spans, formatted in monospace
    for idx, raw in enumerate(code_spans):
        safe = (raw.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
        text = text.replace(
            f"\x00CODE{idx}\x00",
            f'<font name="Mono" size="9.5">{safe}</font>',
        )

    return text


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    return {
        "h1": ParagraphStyle(
            "h1", parent=base, fontName="Body-Bold",
            fontSize=20, leading=24, spaceBefore=0, spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName="Body-Bold",
            fontSize=13, leading=16, spaceBefore=14, spaceAfter=4,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base, fontName="Body-Bold",
            fontSize=11, leading=14, spaceBefore=10, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base, fontName="Body",
            fontSize=10.5, leading=14, spaceAfter=4,
        ),
        "intro": ParagraphStyle(
            "intro", parent=base, fontName="Body-Italic",
            fontSize=10, leading=13, spaceAfter=8, textColor="#333333",
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base, fontName="Body",
            fontSize=10.5, leading=14, leftIndent=14, bulletIndent=2,
            spaceAfter=2,
        ),
        "reply": ParagraphStyle(
            "reply", parent=base, fontName="Body",
            fontSize=10.5, leading=18,
        ),
        "quote": ParagraphStyle(
            "quote", parent=base, fontName="Body-Italic",
            fontSize=10.5, leading=15, leftIndent=18, rightIndent=18,
            spaceBefore=4, spaceAfter=6, textColor="#222222",
        ),
        "tbl_cell": ParagraphStyle(
            "tbl_cell", parent=base, fontName="Body",
            fontSize=9.5, leading=12,
        ),
        "tbl_head": ParagraphStyle(
            "tbl_head", parent=base, fontName="Body-Bold",
            fontSize=9.5, leading=12,
        ),
    }


def parse_table_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def is_table_separator(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    return bool(re.match(r"^\|[\s\-:|]+\|$", s))


def convert_md(md_path: Path, styles: dict[str, ParagraphStyle]) -> list:
    """Return a list of flowables for one markdown file."""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    story: list = []

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            story.append(Spacer(1, 6))
            i += 1
            continue

        if stripped == "---":
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.5, color="#888888"))
            story.append(Spacer(1, 4))
            i += 1
            continue

        # Tables: line starts with | and the next line is a |---| separator
        if stripped.startswith("|") and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            header = parse_table_row(stripped)
            i += 2  # skip header + separator
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(parse_table_row(lines[i]))
                i += 1

            data = [
                [Paragraph(inline(c), styles["tbl_head"]) for c in header],
                *[[Paragraph(inline(c), styles["tbl_cell"]) for c in r] for r in rows],
            ]
            tbl = Table(data, hAlign="LEFT", repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(Spacer(1, 4))
            story.append(tbl)
            story.append(Spacer(1, 6))
            continue

        if line.startswith("### "):
            story.append(Paragraph(inline(line[4:]), styles["h3"]))
            i += 1
            continue
        if line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), styles["h2"]))
            i += 1
            continue
        if line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), styles["h1"]))
            i += 1
            continue

        # Block quote (one or more lines starting with >)
        if stripped.startswith(">"):
            qlines = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                qlines.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            story.append(Paragraph(inline(" ".join(qlines)), styles["quote"]))
            continue

        # Checkbox / bullet list
        if stripped.startswith(("- ", "* ")):
            while i < len(lines) and lines[i].lstrip().startswith(("- ", "* ")):
                item = lines[i].lstrip()[2:]
                # checkbox?
                if item.startswith("[ ] "):
                    bullet_char = "☐"
                    item = item[4:]
                elif item.lower().startswith("[x] "):
                    bullet_char = "☒"
                    item = item[4:]
                else:
                    bullet_char = "•"
                story.append(Paragraph(inline(item), styles["bullet"], bulletText=bullet_char))
                i += 1
            continue

        # whole-line italic *...*
        if (stripped.startswith("*") and stripped.endswith("*")
                and not stripped.startswith("**")
                and len(stripped) > 2):
            story.append(Paragraph(inline(stripped[1:-1]), styles["intro"]))
            i += 1
            continue

        # Doc B "Your answer:" reply blocks
        if stripped.startswith("**Your answer:**"):
            story.append(Spacer(1, 4))
            story.append(Paragraph(inline(stripped), styles["reply"]))
            story.append(Spacer(1, 32))
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            continue

        story.append(Paragraph(inline(line), styles["body"]))
        i += 1

    return story


def build_pdf(out_path: Path, mds: list[Path], title: str) -> None:
    styles = build_styles()
    story: list = []
    for idx, md in enumerate(mds):
        if idx > 0:
            story.append(PageBreak())
        story.extend(convert_md(md, styles))

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=title,
        author="SDE-Software",
    )
    doc.build(story)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    here = Path(__file__).parent

    individual = [
        ("01_capabilities_one_pager.md", "01_capabilities_one_pager.pdf", "Tom's Lab — Capabilities"),
        ("02_questions_for_tom.md", "02_questions_for_tom.pdf", "Questions for Tom"),
        ("03_pre_flight_checklist.md", "03_pre_flight_checklist.pdf", "Pre-flight Checklist"),
        ("04_demo_script.md", "04_demo_script.pdf", "Demo Script"),
        ("05_what_is_toms_lab.md", "05_what_is_toms_lab.pdf", "Tom's Lab — What it is"),
        ("06_discord_announcement.md", "06_discord_announcement.pdf", "Discord Announcement"),
    ]
    for md, pdf, title in individual:
        build_pdf(here / pdf, [here / md], title)

    # Combined run-through pack: just script + checklist (Sean's two)
    build_pdf(
        here / "00_run_through_pack.pdf",
        [here / "03_pre_flight_checklist.md", here / "04_demo_script.md"],
        "Tom's Lab — Run-through Pack",
    )
