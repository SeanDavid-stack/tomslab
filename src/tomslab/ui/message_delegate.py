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

from PyQt6.QtCore import QModelIndex, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
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
    # Emitted when a user clicks a chart thumbnail in a feed row.
    thumbnail_clicked = pyqtSignal(str)   # absolute local path
    bookmark_toggled = pyqtSignal(str, bool)   # (message_id, is_now_bookmarked)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._match_terms: list[str] = []
        self._last_width: int = -1
        self._row_thumb_rects: dict[int, list[tuple[QRect, str]]] = {}
        self._row_star_rects: dict[int, QRect] = {}   # click target for bookmark
        # Flash-on-jump state: when MainWindow navigates to a row we paint a
        # gold overlay for a short window, then it fades out.
        self._flash_row_id: str | None = None   # message id to flash
        self._flash_started_ms: int = 0
        # Which message ids are currently bookmarked (hot cache populated by
        # MainWindow); missing = not bookmarked.
        self._bookmarked: set[str] = set()

    def set_match_terms(self, query: str) -> None:
        self._match_terms = _extract_terms(query)

    def flash_message(self, message_id: str) -> None:
        from PyQt6.QtCore import QDateTime
        self._flash_row_id = str(message_id) if message_id else None
        self._flash_started_ms = QDateTime.currentMSecsSinceEpoch()

    def clear_flash(self) -> None:
        self._flash_row_id = None

    def flash_active(self) -> bool:
        if not self._flash_row_id:
            return False
        from PyQt6.QtCore import QDateTime
        return QDateTime.currentMSecsSinceEpoch() - self._flash_started_ms < 2200

    def set_bookmarks(self, ids: set[str]) -> None:
        self._bookmarked = set(ids)

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
        # Selection shades the row's *own* accent color rather than
        # blanketing everything in gray — so a selected Tom card still
        # reads as Tom (gold family, slightly darker), a selected doc
        # still reads as a doc, etc. Previously every selected row
        # collapsed to the hover gray which made Tom's cards look like
        # ordinary user messages the moment you clicked them.
        is_doc = msg.doc_meta is not None
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if is_doc:
            base = COLOR_BG_DOC
        elif msg.is_featured_speaker:
            base = COLOR_BG_GOLD
        else:
            base = COLOR_BG_NORMAL
        bg = base.darker(115) if is_selected else base
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
            rects = _paint_thumbnails(painter, x, y, content_w, thumbs)
            # Remember geometry in ABSOLUTE list coordinates so editorEvent
            # can test click positions against it on the next mouse press.
            self._row_thumb_rects[index.row()] = rects

        # star (bookmark) in the top-right corner of the card
        self._row_star_rects[index.row()] = self._paint_star(painter, rect, msg)

        # bottom divider
        painter.setPen(QPen(COLOR_DIVIDER, 1))
        painter.drawLine(
            rect.x() + PAD_X,
            rect.bottom(),
            rect.right() - PAD_X,
            rect.bottom(),
        )

        # Flash overlay — when MainWindow navigates us here, pulse a gold
        # border for ~2s so the user sees which row they landed on.
        if (
            msg.id
            and self._flash_row_id
            and msg.id == self._flash_row_id
            and self.flash_active()
        ):
            painter.save()
            painter.setPen(QPen(COLOR_GOLD, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(1, 1, -2, -2))
            painter.restore()

        painter.restore()

    # ------------------------------------------------------------------
    # star painter (used for bookmark hit-testing)
    # ------------------------------------------------------------------
    def _paint_star(self, painter: QPainter, rect: QRect, msg: MessageRow) -> QRect:
        # Doc rows don't get the star (nothing to bookmark on a doc).
        if msg.doc_meta is not None or not msg.id:
            return QRect()
        star_size = 22
        x = rect.right() - PAD_X - star_size
        y = rect.y() + PAD_Y
        is_on = msg.id in self._bookmarked
        painter.save()
        f = QFont(painter.font())
        f.setPointSizeF(max(f.pointSizeF() + 4, 13))
        painter.setFont(f)
        painter.setPen(COLOR_GOLD if is_on else COLOR_TEXT_DIM)
        painter.drawText(
            QRect(x, y, star_size, star_size),
            int(Qt.AlignmentFlag.AlignCenter),
            "★" if is_on else "☆",
        )
        painter.restore()
        return QRect(x, y, star_size, star_size)

    # ------------------------------------------------------------------
    # mouse handling — detect clicks on thumbnail / star rects
    # ------------------------------------------------------------------
    def editorEvent(self, event, model, option, index) -> bool:
        if (
            isinstance(event, QMouseEvent)
            and event.type() == QMouseEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            pt = event.position().toPoint()
            # Star takes priority so it's clickable even over any overlap.
            star_rect = self._row_star_rects.get(index.row())
            if star_rect and star_rect.isValid() and star_rect.contains(pt):
                msg: MessageRow | None = index.data(ROLE_MESSAGE)
                if msg and msg.id and msg.doc_meta is None:
                    now_on = msg.id not in self._bookmarked
                    if now_on:
                        self._bookmarked.add(msg.id)
                    else:
                        self._bookmarked.discard(msg.id)
                    self.bookmark_toggled.emit(msg.id, now_on)
                    return True
            rects = self._row_thumb_rects.get(index.row()) or []
            for rect, path in rects:
                if rect.contains(pt):
                    self.thumbnail_clicked.emit(path)
                    return True
        return super().editorEvent(event, model, option, index)


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
    elif msg.is_featured_speaker:
        # Tom gets a small "Author" pill appended to his name so new users
        # know he's the primary subject of the corpus, not just another user.
        display = f"{display}  ✦ Author"
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
) -> list[tuple[QRect, str]]:
    """Paint up to MAX_THUMBS thumbnails. Returns [(rect, path), ...] in the
    same coordinate system as the painter so the delegate can hit-test clicks."""
    shown = thumbs[:MAX_THUMBS]
    extra = len(thumbs) - len(shown)
    cx = x
    row_top = y
    hit_rects: list[tuple[QRect, str]] = []
    for path in shown:
        pix = _load_thumb(path)
        if pix is None or pix.isNull():
            continue
        scaled = _scale_for_thumb(pix, width)
        if cx + scaled.width() > x + width:
            cx = x
            row_top = y
        painter.drawPixmap(cx, row_top, scaled)
        hit_rects.append((QRect(cx, row_top, scaled.width(), scaled.height()), path))
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

    return hit_rects


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
    """Return "Mon D, YYYY · Na ago" when possible, falling back to the
    ISO minute-precision form. Human-friendly without being noisy."""
    if not ts:
        return ""
    iso = ts[:16].replace("T", " ")
    rel = _relative_from_iso(ts)
    return f"{iso}  ·  {rel}" if rel else iso


_MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _relative_from_iso(ts: str) -> str:
    """Return a short relative time like "2y ago" or "3mo ago". Empty on error."""
    try:
        from datetime import datetime, timezone
        # Accept trailing offset or Z.
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m ago"
        hrs = mins // 60
        if hrs < 24:
            return f"{hrs}h ago"
        days = hrs // 24
        if days < 30:
            return f"{days}d ago"
        months = days // 30
        if months < 12:
            return f"{months}mo ago"
        years = days // 365
        extra_months = (days % 365) // 30
        if extra_months:
            return f"{years}y {extra_months}mo ago"
        return f"{years}y ago"
    except Exception:
        return ""
