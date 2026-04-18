"""Custom-painted delegate that renders each message as a Discord-style card.

Featured speaker (Tom) is highlighted in gold.  Replies show a small preview
of the parent message.  Inline chart thumbnails are drawn for attachments
that resolved to a local file.  Match terms from the search box are
bolded in the body text.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QPixmapCache,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from tomslab.ui.message_model import ROLE_MESSAGE, MessageRow

# ---- palette -----------------------------------------------------------------
COLOR_BG_NORMAL = QColor("#2B2D31")   # Discord-ish dark background
COLOR_BG_HOVER  = QColor("#32353C")
COLOR_BG_GOLD   = QColor("#3A3320")   # subtle gold tint for Tom
COLOR_BG_DOC    = QColor("#1F2A3A")   # muted blue tint for doc pages
COLOR_TEXT      = QColor("#DBDEE1")
COLOR_TEXT_DIM  = QColor("#949BA4")
COLOR_AUTHOR    = QColor("#F2F3F5")
COLOR_GOLD      = QColor("#FFC857")   # gold accent for Tom's name
COLOR_DOC_BLUE  = QColor("#6AA1FF")   # accent for doc pages
COLOR_REPLY_BAR = QColor("#4E5058")
COLOR_DIVIDER   = QColor(255, 255, 255, 16)

# ---- layout constants --------------------------------------------------------
PAD_X = 14
PAD_Y = 12
LINE_GAP = 4
HEADER_H = 26          # author + timestamp line
REPLY_H = 22           # reply preview line
THUMB_MAX_H = 180
THUMB_MAX_W = 360
THUMB_GAP = 6
MAX_THUMBS = 2
AVATAR_SIZE = 34
AVATAR_GAP = 12        # between avatar and text column


# A palette of vibrant-but-readable hues used to colour author avatars when
# the user isn't the featured speaker (Tom gets the gold treatment via the
# existing code path).
_AVATAR_PALETTE = (
    "#5865F2",  # blurple
    "#3498DB",  # blue
    "#1ABC9C",  # teal
    "#2ECC71",  # green
    "#F39C12",  # amber
    "#E67E22",  # orange
    "#E74C3C",  # red
    "#E91E63",  # pink
    "#9B59B6",  # purple
    "#7289DA",  # soft blurple
    "#11806A",  # dark teal
    "#1F8B4C",  # dark green
)


def _avatar_color(author_name: str) -> QColor:
    """Deterministic colour per author."""
    h = hashlib.md5((author_name or "?").lower().encode("utf-8")).hexdigest()
    return QColor(_AVATAR_PALETTE[int(h, 16) % len(_AVATAR_PALETTE)])


def _avatar_letter(author: str) -> str:
    a = (author or "?").strip()
    if not a:
        return "?"
    # Discord usernames often start with emoji; fall back to the first letter.
    for ch in a:
        if ch.isalpha() or ch.isdigit():
            return ch.upper()
    return a[0]


class MessageDelegate(QStyledItemDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._match_terms: list[str] = []
        self._last_width: int = -1

    def set_match_terms(self, query: str) -> None:
        self._match_terms = _extract_terms(query)

    # ------------------------------------------------------------------
    # sizing
    # ------------------------------------------------------------------
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        msg: MessageRow = index.data(ROLE_MESSAGE)
        if msg is None:
            return super().sizeHint(option, index)

        width = max(option.rect.width(), 480)
        # Avatar column steals space from the content column.
        content_w = width - 2 * PAD_X - AVATAR_SIZE - AVATAR_GAP

        h = PAD_Y + HEADER_H + LINE_GAP
        if msg.reply_to_id and msg.reply_to_author:
            h += REPLY_H + LINE_GAP

        if msg.content.strip():
            doc = _make_content_doc(msg.content, self._match_terms, content_w)
            h += int(doc.size().height()) + LINE_GAP

        thumbs = _thumb_paths(msg)
        if thumbs:
            thumb_h = _calc_thumb_row_height(thumbs, content_w)
            h += thumb_h + LINE_GAP

        h += PAD_Y
        # Ensure the card is at least avatar-height + padding.
        return QSize(width, max(h, PAD_Y * 2 + AVATAR_SIZE + 6))

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        msg: MessageRow = index.data(ROLE_MESSAGE)
        if msg is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect: QRect = option.rect
        # --- background --------------------------------------------------
        is_doc = msg.doc_meta is not None
        if option.state & QStyle.StateFlag.State_Selected:
            bg = COLOR_BG_HOVER.darker(110)
        elif is_doc:
            bg = COLOR_BG_DOC
        elif msg.is_featured_speaker:
            bg = COLOR_BG_GOLD
        else:
            bg = COLOR_BG_NORMAL
        painter.fillRect(rect, bg)

        # left accent bar: gold for Tom Discord, blue for doc pages, none otherwise.
        if is_doc:
            painter.fillRect(rect.x(), rect.y(), 3, rect.height(), COLOR_DOC_BLUE)
        elif msg.is_featured_speaker:
            painter.fillRect(rect.x(), rect.y(), 3, rect.height(), COLOR_GOLD)

        # --- avatar ------------------------------------------------------
        av_x = rect.x() + PAD_X
        av_y = rect.y() + PAD_Y
        _paint_avatar(painter, av_x, av_y, msg)

        # --- layout ------------------------------------------------------
        x = av_x + AVATAR_SIZE + AVATAR_GAP
        y = rect.y() + PAD_Y
        content_w = rect.width() - (x - rect.x()) - PAD_X

        # reply preview
        if msg.reply_to_id and msg.reply_to_author:
            _paint_reply_line(painter, x, y, content_w, msg)
            y += REPLY_H + LINE_GAP

        # header: author + timestamp
        _paint_header(painter, x, y, content_w, msg)
        y += HEADER_H + LINE_GAP

        # body
        if msg.content.strip():
            doc = _make_content_doc(msg.content, self._match_terms, content_w)
            painter.save()
            painter.translate(x, y)
            doc.drawContents(painter)
            painter.restore()
            y += int(doc.size().height()) + LINE_GAP

        # attachment thumbnails
        thumbs = _thumb_paths(msg)
        if thumbs:
            _paint_thumbnails(painter, x, y, content_w, thumbs)

        # bottom divider
        painter.setPen(QPen(COLOR_DIVIDER, 1))
        painter.drawLine(
            rect.x() + PAD_X,
            rect.bottom(),
            rect.right() - PAD_X,
            rect.bottom(),
        )

        painter.restore()


# -----------------------------------------------------------------------------
# painting helpers
# -----------------------------------------------------------------------------
def _paint_avatar(painter: QPainter, x: int, y: int, msg: MessageRow) -> None:
    """Draw the rounded author badge (circle + initial)."""
    painter.save()
    # choose colour
    if msg.doc_meta is not None:
        bg = COLOR_DOC_BLUE
        letter = "📄"          # Unicode emoji, not a letter
    elif msg.is_featured_speaker:
        bg = COLOR_GOLD
        letter = "T"
    else:
        bg = _avatar_color(msg.author_name or msg.author_nickname or "?")
        letter = _avatar_letter(msg.author_nickname or msg.author_name or "?")

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QBrush(bg))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(x, y, AVATAR_SIZE, AVATAR_SIZE)

    # letter
    text_color = QColor("#111214") if bg.lightnessF() > 0.55 else QColor("#FFFFFF")
    painter.setPen(text_color)
    f = QFont(painter.font())
    f.setBold(True)
    f.setPointSizeF(max(f.pointSizeF() + 2, 11))
    painter.setFont(f)
    painter.drawText(
        QRect(x, y, AVATAR_SIZE, AVATAR_SIZE),
        int(Qt.AlignmentFlag.AlignCenter),
        letter,
    )
    painter.restore()


def _paint_header(
    painter: QPainter, x: int, y: int, width: int, msg: MessageRow
) -> None:
    painter.save()
    name_font = QFont(painter.font())
    name_font.setBold(True)
    painter.setFont(name_font)

    is_doc = msg.doc_meta is not None
    if is_doc:
        name_color = COLOR_DOC_BLUE
    elif msg.is_featured_speaker:
        name_color = COLOR_GOLD
    else:
        name_color = COLOR_AUTHOR
    painter.setPen(name_color)

    display = msg.author_nickname or msg.author_name or "?"
    if is_doc:
        display = f"📄 {display}"
    fm = painter.fontMetrics()
    name_rect = QRect(x, y, width, HEADER_H)
    painter.drawText(
        name_rect,
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        display,
    )
    name_w = fm.horizontalAdvance(display)

    ts_font = QFont(painter.font())
    ts_font.setBold(False)
    ts_font.setPointSizeF(max(ts_font.pointSizeF() - 1, 8))
    painter.setFont(ts_font)
    painter.setPen(COLOR_TEXT_DIM)
    ts_x = x + name_w + 10
    ts_text = _fmt_timestamp(msg.timestamp)
    if msg.is_pinned:
        ts_text = "📌 " + ts_text
    painter.drawText(
        QRect(ts_x, y, width - (ts_x - x), HEADER_H),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        ts_text,
    )
    painter.restore()


def _paint_reply_line(
    painter: QPainter, x: int, y: int, width: int, msg: MessageRow
) -> None:
    painter.save()
    # left bar
    painter.fillRect(x, y + 4, 2, REPLY_H - 8, COLOR_REPLY_BAR)
    painter.setPen(COLOR_TEXT_DIM)
    f = QFont(painter.font())
    f.setPointSizeF(max(f.pointSizeF() - 1, 8))
    painter.setFont(f)
    author = msg.reply_to_author or ""
    snippet = (msg.reply_to_snippet or "").strip().replace("\n", " ")
    if len(snippet) > 90:
        snippet = snippet[:87] + "…"
    label = f"↪ replying to {author}"
    if snippet:
        label += f": {snippet}"
    painter.drawText(
        QRect(x + 8, y, width - 8, REPLY_H),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        label,
    )
    painter.restore()


def _paint_thumbnails(
    painter: QPainter, x: int, y: int, width: int, thumbs: list[str]
) -> None:
    shown = thumbs[:MAX_THUMBS]
    extra = len(thumbs) - len(shown)
    cx = x
    row_top = y
    for path in shown:
        pix = _load_thumb(path)
        if pix is None or pix.isNull():
            continue
        scaled = _scale_for_thumb(pix, width)
        if cx + scaled.width() > x + width:
            # wrap if we'd overflow
            cx = x
            row_top = y  # Phase 2 stays single-row; overflow just clips
        painter.drawPixmap(cx, row_top, scaled)
        cx += scaled.width() + THUMB_GAP

    if extra > 0:
        painter.save()
        painter.setPen(COLOR_TEXT_DIM)
        painter.drawText(
            QRect(cx, row_top, 200, THUMB_MAX_H),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"+{extra} more",
        )
        painter.restore()


# -----------------------------------------------------------------------------
# content document (rich text for match highlighting)
# -----------------------------------------------------------------------------
def _make_content_doc(
    text: str, match_terms: list[str], width: int
) -> QTextDocument:
    doc = QTextDocument()
    doc.setDefaultFont(_content_font())
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WordWrap)
    doc.setDefaultTextOption(opt)
    doc.setTextWidth(width)

    cursor = QTextCursor(doc)
    default_fmt = QTextCharFormat()
    default_fmt.setForeground(QBrush(COLOR_TEXT))
    cursor.insertText(text, default_fmt)

    if match_terms:
        _apply_match_highlight(doc, match_terms)
    return doc


def _apply_match_highlight(doc: QTextDocument, terms: list[str]) -> None:
    highlight_fmt = QTextCharFormat()
    highlight_fmt.setFontWeight(700)
    highlight_fmt.setForeground(QBrush(COLOR_GOLD))
    cursor = QTextCursor(doc)

    plain = doc.toPlainText()
    plain_lower = plain.lower()
    for term in terms:
        t = term.lower()
        if not t:
            continue
        start = 0
        while True:
            idx = plain_lower.find(t, start)
            if idx < 0:
                break
            end = idx + len(t)
            cursor.setPosition(idx)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(highlight_fmt)
            start = end


def _content_font() -> QFont:
    f = QFont("Segoe UI", 10)
    return f


def _extract_terms(query: str) -> list[str]:
    """Split a user query into individual highlight terms (drops quotes/operators)."""
    out: list[str] = []
    for m in re.finditer(r'"([^"]+)"|([A-Za-z0-9_]{2,})', query or ""):
        term = m.group(1) or m.group(2)
        if term and term.lower() not in {"and", "or", "not"}:
            out.append(term)
    return out


# -----------------------------------------------------------------------------
# thumbnails
# -----------------------------------------------------------------------------
def _thumb_paths(msg: MessageRow) -> list[str]:
    out: list[str] = []
    for a in msg.attachments:
        if not a.local_path:
            continue
        p = Path(a.local_path)
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            out.append(str(p))
    return out


def _load_thumb(path: str) -> QPixmap | None:
    key = f"tomslab_thumb:{path}"
    pix = QPixmapCache.find(key)
    if pix is not None:
        return pix
    pix = QPixmap(path)
    if pix.isNull():
        return None
    QPixmapCache.insert(key, pix)
    return pix


def _scale_for_thumb(pix: QPixmap, max_width_total: int) -> QPixmap:
    max_w = min(THUMB_MAX_W, max_width_total)
    max_h = THUMB_MAX_H
    if pix.width() <= max_w and pix.height() <= max_h:
        return pix
    return pix.scaled(
        max_w,
        max_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _calc_thumb_row_height(thumbs: list[str], width: int) -> int:
    """Best-effort height estimate for the thumb row without loading pixmaps twice."""
    max_h = 0
    for p in thumbs[:MAX_THUMBS]:
        pix = _load_thumb(p)
        if pix is None or pix.isNull():
            continue
        scaled = _scale_for_thumb(pix, width)
        if scaled.height() > max_h:
            max_h = scaled.height()
    return max_h or 0


# -----------------------------------------------------------------------------
# timestamp helper
# -----------------------------------------------------------------------------
def _fmt_timestamp(ts: str) -> str:
    # ISO 8601 -> "YYYY-MM-DD HH:MM"
    if not ts:
        return ""
    return ts[:16].replace("T", " ")
