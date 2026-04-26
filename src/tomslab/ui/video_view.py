"""TomTube tab — browse indexed Tom B YouTube videos + their transcript chunks.

Layout:
  Top bar:        search + status filter chips + sort dropdown + count badge
  Left pane:      video list with clean info hierarchy
                  (title, status icon, duration, chunk count)
  Right pane:     selected video's transcript chunks with ▶ YouTube jumps
  Timestamp hint: dismissible one-time tip near the top of the right pane

Key interactions:
  * Type in search box → filters BOTH the video list (shows only videos
    whose transcripts match) AND the right pane (shows the matching
    chunks). Previously the right pane got hijacked and the left pane
    stayed unchanged, which was disorienting.
  * Status chips filter the video list.
  * Sort dropdown reorders the list.
  * Right-pane chunk cards: ▶ timestamp is now a large clickable button.
  * PgUp / PgDn jumps between chunks in the right pane.
"""
from __future__ import annotations

import sqlite3

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tomslab import db as dbmod
from tomslab.chat import _fmt_timestamp, _youtube_link
from tomslab.ui.browser_open import open_browser


_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_CARD_ALT = "#313338"
# Lighter card shade used to mark the "active" video group in the
# cross-video search results — a subtle shadow-highlight so the user
# can see at a glance which group corresponds to the row they just
# clicked (and vice versa) without garish colors.
_COLOR_CARD_HI = "#3A3D44"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_GOLD_HOVER = "#FFD87A"
_COLOR_BORDER = "#3F4147"
_COLOR_BORDER_SOFT = "#313338"
_COLOR_ACCENT = "#5865F2"
_COLOR_OK = "#43B581"
_COLOR_WARN = "#FAA61A"
_COLOR_DANGER = "#ED4245"


# Status → glyph + color for the filter chips and list rows
_STATUS_META = {
    "transcribed": ("✅", _COLOR_OK, "Transcribed"),
    "downloaded":  ("⏳", _COLOR_WARN, "Downloaded, not yet transcribed"),
    "pending":     ("·",  _COLOR_DIM, "Pending"),
    "failed":      ("⚠",  _COLOR_DANGER, "Failed"),
}


def _format_duration(seconds: int | float | None) -> str:
    """Duration as 'Xh YYm' for long videos, 'Y min' for short ones."""
    s = int(seconds or 0)
    if s <= 0:
        return "— min"
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m} min"


def _fts_phrase(q: str) -> str:
    """Turn a raw user search string into a safe FTS5 phrase query.

    Escapes embedded double quotes (FTS5 uses ``""`` as a literal
    double-quote inside a phrase) and wraps the whole thing in double
    quotes so multi-word queries like ``ib failure`` match as a phrase
    rather than ORing the terms. Empty input returns a query that
    never matches so caller can pass it safely.
    """
    t = (q or "").strip().replace('"', '""')
    return f'"{t}"' if t else '""'


class TomTubeView(QWidget):
    """Browse indexed Tom B videos; open timestamps on YouTube."""

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._current_video_id: str | None = None
        self._current_video_url: str = ""
        self._status_filter: str = "all"    # 'all' | 'transcribed' | 'downloaded' | 'pending' | 'failed'
        self._sort_mode: str = "added_desc"  # 'added_desc' | 'added_asc' | 'dur_desc' | 'chunks_desc' | 'title_asc'
        self._search_query: str = ""
        # Set by _clear_video_selection, cleared when the user clicks a
        # video. While True, reload() skips auto-select-row-0 and renders
        # cross-video search results in the right pane instead.
        self._no_selection_mode: bool = False
        # The video currently "associated" while browsing cross-video
        # results. Clicking a row in the left list or a transcript card
        # on the right sets this; the render layer brightens the matching
        # group so the visual link between the two panes is obvious.
        self._active_video_id: str | None = None
        self._hint_dismissed: bool = (
            (dbmod.get_setting(self._conn, "tomtube_hint_dismissed", "0")
             or "0") == "1"
        )
        self._build_ui()
        self.reload()

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_top_bar())
        outer.addWidget(self._build_filter_row())
        outer.addWidget(self._build_splitter(), stretch=1)

    def _build_top_bar(self) -> QWidget:
        """Search input + count badge."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 4)
        row.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search every transcript — e.g. 'naked VPOC', 'IB high reject', "
            "'absorption on the offer'"
        )
        self._search.setStyleSheet(
            f"QLineEdit {{ background: {_COLOR_CARD}; color: {_COLOR_TEXT};"
            f" border: 1px solid {_COLOR_BORDER}; border-radius: 8px;"
            f" padding: 8px 12px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border: 1px solid {_COLOR_GOLD}; }}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search, stretch=1)

        self._count_badge = QLabel("")
        self._count_badge.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 11px; padding: 0 8px;"
        )
        row.addWidget(self._count_badge)
        return bar

    def _build_filter_row(self) -> QWidget:
        """Status filter chips + sort dropdown."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 10)
        row.setSpacing(6)

        self._status_chips: dict[str, QPushButton] = {}
        for key, label in [
            ("all",         "All"),
            ("transcribed", "✅ Transcribed"),
            ("downloaded",  "⏳ Downloaded"),
            ("pending",     "· Pending"),
            ("failed",      "⚠ Failed"),
        ]:
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(key == "all")
            b.setStyleSheet(self._chip_style())
            b.clicked.connect(lambda _c=False, k=key: self._set_status_filter(k))
            self._status_chips[key] = b
            row.addWidget(b)

        row.addStretch(1)

        sort_lbl = QLabel("Sort:")
        sort_lbl.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 11px;"
        )
        row.addWidget(sort_lbl)

        self._sort_combo = QComboBox()
        for label, value in [
            ("Newest first",   "added_desc"),
            ("Oldest first",   "added_asc"),
            ("Longest first",  "dur_desc"),
            ("Most chunks",    "chunks_desc"),
            ("Title A–Z",      "title_asc"),
        ]:
            self._sort_combo.addItem(label, userData=value)
        self._sort_combo.setStyleSheet(
            f"QComboBox {{ background: {_COLOR_CARD}; color: {_COLOR_TEXT};"
            f" border: 1px solid {_COLOR_BORDER}; border-radius: 6px;"
            f" padding: 4px 10px; font-size: 11px; }}"
        )
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        row.addWidget(self._sort_combo)
        return bar

    def _build_splitter(self) -> QSplitter:
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(
            f"QSplitter::handle {{ background: {_COLOR_BORDER_SOFT}; width: 1px; }}"
        )

        split.addWidget(self._build_left_pane())
        split.addWidget(self._build_right_pane())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        return split

    def _build_left_pane(self) -> QWidget:
        left = QWidget()
        left.setStyleSheet(f"background: {_COLOR_CARD};")
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._left_header = QLabel("")
        self._left_header.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 11px; font-weight: 600;"
            f" padding: 10px 14px 6px 14px; text-transform: uppercase;"
            f" letter-spacing: 0.5px;"
        )
        lay.addWidget(self._left_header)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {_COLOR_TEXT};"
            f" border: none; padding: 4px 8px; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 10px 10px; border-radius: 6px;"
            f" margin: 2px 0; }}"
            f"QListWidget::item:hover {{ background: {_COLOR_BG}; }}"
            f"QListWidget::item:selected {{ background: #3A3320;"
            f" color: {_COLOR_GOLD}; }}"
        )
        self._list.currentItemChanged.connect(self._on_video_selected)
        lay.addWidget(self._list, stretch=1)
        return left

    def _build_right_pane(self) -> QWidget:
        right = QWidget()
        right.setStyleSheet(f"background: {_COLOR_BG};")
        lay = QVBoxLayout(right)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(6)

        self._heading = QLabel("")
        self._heading.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-weight: 600; font-size: 16px;"
        )
        self._heading.setWordWrap(True)
        lay.addWidget(self._heading)

        self._subheading = QLabel("")
        self._subheading.setStyleSheet(
            f"color: {_COLOR_DIM}; font-size: 11px;"
        )
        self._subheading.setWordWrap(True)
        self._subheading.setTextFormat(Qt.TextFormat.RichText)
        self._subheading.setOpenExternalLinks(True)
        lay.addWidget(self._subheading)

        # Escape hatch back to the cross-video view. Without this, once a
        # user clicks a single video the search box scopes results to that
        # video only and there's no obvious way to widen it again.
        self._back_to_all = QPushButton("← All videos")
        self._back_to_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_to_all.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_COLOR_GOLD};"
            f" border: 1px solid {_COLOR_BORDER}; border-radius: 4px;"
            f" padding: 4px 10px; font-size: 11px; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {_COLOR_GOLD};"
            f" background: rgba(255, 200, 87, 0.05); }}"
        )
        self._back_to_all.clicked.connect(self._clear_video_selection)
        self._back_to_all.setVisible(False)
        lay.addWidget(self._back_to_all)

        # Dismissible one-time timestamp hint
        self._hint_bar = QWidget()
        hint_lay = QHBoxLayout(self._hint_bar)
        hint_lay.setContentsMargins(10, 8, 10, 8)
        hint_lay.setSpacing(8)
        self._hint_bar.setStyleSheet(
            f"QWidget {{ background: #3A3320; border-left: 3px solid {_COLOR_GOLD};"
            f" border-radius: 4px; }}"
        )
        hint_label = QLabel(
            "<b>Tip:</b> ▶ timestamps land at the start of a ~90-second "
            "window. Listen forward from that mark — Tom's cited phrase "
            "is within the next minute or so."
        )
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-size: 11px; background: transparent;"
            f" border: none;"
        )
        hint_lay.addWidget(hint_label, stretch=1)
        dismiss = QPushButton("✕")
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_COLOR_DIM};"
            f" border: none; font-size: 14px; padding: 0 6px; }}"
            f"QPushButton:hover {{ color: {_COLOR_TEXT}; }}"
        )
        dismiss.clicked.connect(self._dismiss_hint)
        hint_lay.addWidget(dismiss)
        self._hint_bar.setVisible(not self._hint_dismissed)
        lay.addWidget(self._hint_bar)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_anchor)
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background: {_COLOR_BG}; color: {_COLOR_TEXT};"
            f" border: none; padding: 4px; font-size: 13px; line-height: 1.5;"
            f" selection-background-color: {_COLOR_ACCENT}; }}"
        )
        # QTextBrowser adds blue underlines to every <a> tag by default,
        # even when inline styles say otherwise. Set a document-wide
        # stylesheet that strips anchor underlines + resets color inherit
        # so our cross-video result cards don't look like a pile of
        # hyperlinks.
        self._browser.document().setDefaultStyleSheet(
            "a { text-decoration: none; color: inherit; }"
            "a:link { text-decoration: none; color: inherit; }"
            "a:visited { text-decoration: none; color: inherit; }"
            "a:hover { text-decoration: none; }"
        )
        lay.addWidget(self._browser, stretch=1)

        # Keyboard navigation: PgUp / PgDn jump between chunks
        QShortcut(QKeySequence("PgDown"), self._browser,
                  activated=self._jump_next_chunk)
        QShortcut(QKeySequence("PgUp"), self._browser,
                  activated=self._jump_prev_chunk)
        # Escape clears the current video selection so the user can get
        # back to cross-video search without having to hunt for the
        # ← All videos button.
        QShortcut(QKeySequence("Esc"), right,
                  activated=self._clear_video_selection)
        return right

    # ==================================================================
    # Style helpers
    # ==================================================================
    def _chip_style(self) -> str:
        return (
            f"QPushButton {{ background: transparent; color: {_COLOR_DIM};"
            f" padding: 5px 12px; border: 1px solid {_COLOR_BORDER};"
            f" border-radius: 12px; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {_COLOR_TEXT};"
            f" border-color: {_COLOR_TEXT}; }}"
            f"QPushButton:checked {{ background: {_COLOR_GOLD};"
            f" color: #1E1F22; border-color: {_COLOR_GOLD};"
            f" font-weight: 600; }}"
        )

    # ==================================================================
    # Interaction handlers
    # ==================================================================
    def _set_status_filter(self, key: str) -> None:
        self._status_filter = key
        for k, btn in self._status_chips.items():
            btn.setChecked(k == key)
        self.reload()

    def _on_sort_changed(self) -> None:
        self._sort_mode = self._sort_combo.currentData() or "added_desc"
        self.reload()

    def _dismiss_hint(self) -> None:
        self._hint_dismissed = True
        self._hint_bar.setVisible(False)
        dbmod.set_setting(self._conn, "tomtube_hint_dismissed", "1")

    # ==================================================================
    # Data loading
    # ==================================================================
    def reload(self) -> None:
        """Reload the left-pane video list based on the current filter,
        sort mode, and search query. If search is active, only videos
        with matching transcript chunks are shown."""
        sort_sql = {
            "added_desc":  "v.added_at DESC",
            "added_asc":   "v.added_at ASC",
            "dur_desc":    "v.duration_sec DESC NULLS LAST",
            "chunks_desc": "n_chunks DESC",
            "title_asc":   "v.title ASC",
        }.get(self._sort_mode, "v.added_at DESC")

        where_clauses = []
        params: list = []
        if self._status_filter != "all":
            where_clauses.append("v.transcript_status = ?")
            params.append(self._status_filter)
        if self._search_query:
            # FTS5 is much faster than LIKE '%q%' over 30K chunks. Use
            # a quoted phrase so multi-word queries like "ib failure"
            # match as a phrase rather than OR-ing the terms.
            fts_q = _fts_phrase(self._search_query)
            where_clauses.append(
                "EXISTS (SELECT 1 FROM video_chunks_fts f "
                "        WHERE f.video_id = v.id AND video_chunks_fts MATCH ?)"
            )
            params.append(fts_q)
        where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # n_chunks is now a cached column on videos (kept in sync by the
        # transcription worker + on-launch backfill). Reading it
        # directly avoids the per-row COUNT(*) subquery that used to
        # run on every search keystroke.
        sql = f"""
            SELECT v.id, v.title, v.duration_sec, v.transcript_status, v.url,
                   COALESCE(v.n_chunks, 0) AS n_chunks,
                   v.added_at
              FROM videos v
              {where}
             ORDER BY {sort_sql}
        """
        rows = self._conn.execute(sql, params).fetchall()
        self._populate_list(rows)
        self._update_count_badge(rows)

    def _populate_list(self, rows) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for r in rows:
            glyph, color, _tip = _STATUS_META.get(
                r["transcript_status"] or "pending",
                _STATUS_META["pending"],
            )
            title = r["title"] or r["id"]
            dur = _format_duration(r["duration_sec"])
            n = r["n_chunks"] or 0
            # Two-line layout: title on top (bold tone via size), meta below.
            # QListWidgetItem doesn't take rich text, so we use \n and
            # rely on whitespace-based hierarchy.
            meta_line = f"    {glyph}  {dur}  ·  {n:,} chunks"
            item = QListWidgetItem(f"{title}\n{meta_line}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, r["url"])
            item.setToolTip(
                f"{title}\nStatus: {r['transcript_status'] or 'pending'}\n"
                f"Duration: {dur}\nTranscript chunks: {n:,}"
            )
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count():
            if self._no_selection_mode:
                # User hit ← All videos / Escape; keep them out of any
                # single-video view and render the cross-video search in
                # the right pane instead. Stays in effect until they
                # explicitly click a video in the list.
                if self._search_query:
                    self._render_cross_video_chunks(self._search_query)
                else:
                    self._render_cross_video_prompt()
            else:
                self._list.setCurrentRow(0)
        else:
            self._render_empty_state()

    def _update_count_badge(self, rows) -> None:
        n = len(rows)
        total = self._conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        self._left_header.setText(
            f"Tom's videos — {n:,} of {total:,}"
            if n != total else f"Tom's videos — {total:,} indexed"
        )
        if self._search_query:
            self._count_badge.setText(
                f"🔍 {n:,} video(s) match “{self._search_query}”"
            )
        elif self._status_filter != "all":
            filter_name = {
                "transcribed": "transcribed",
                "downloaded":  "downloaded",
                "pending":     "pending",
                "failed":      "failed",
            }.get(self._status_filter, "")
            self._count_badge.setText(f"{n:,} {filter_name}")
        else:
            self._count_badge.setText(f"{n:,} videos")

    def _render_empty_state(self) -> None:
        if self._search_query:
            self._heading.setText(f"No videos match “{self._search_query}”")
            self._subheading.setText(
                "Try a looser phrase — transcripts may spell acronyms "
                "differently than Tom writes them in Discord."
            )
        elif self._status_filter != "all":
            self._heading.setText(f"No videos with status “{self._status_filter}”")
            self._subheading.setText(
                "Switch to All to see everything, or wait for the current "
                "processing to complete."
            )
        else:
            self._heading.setText("No videos ingested yet")
            self._subheading.setText(
                "File → Import → YouTube videos from a folder (recommended). "
                "Drop Tom's audio files into D:/Tom Videos (or wherever) and "
                "Tom's Lab transcribes them in the background."
            )
        self._browser.setHtml("")

    # ==================================================================
    # Selection / chunk rendering
    # ==================================================================
    def _on_video_selected(self, current, _previous) -> None:
        if current is None:
            self._current_video_id = None
            self._current_video_url = ""
            self._browser.setHtml("")
            self._back_to_all.setVisible(False)
            return
        vid = current.data(Qt.ItemDataRole.UserRole)
        url = current.data(Qt.ItemDataRole.UserRole + 1) or ""

        # In cross-video search mode, clicking a row in the left list
        # just scrolls the right pane to that video's chunk group and
        # keeps the user browsing results. Drill-in still happens via
        # the "open this video transcript →" link inside each group.
        if self._no_selection_mode:
            self._active_video_id = vid
            # Re-render to apply the active-group shadow highlight, then
            # scroll so the highlighted group is visible.
            if self._search_query:
                self._render_cross_video_chunks(self._search_query)
            self._browser.scrollToAnchor(f"vid-{vid}")
            return

        self._current_video_id = vid
        self._current_video_url = url
        row = self._conn.execute(
            "SELECT title, duration_sec, transcript_status FROM videos WHERE id=?",
            (vid,),
        ).fetchone()
        if row is None:
            return
        dur = _format_duration(row["duration_sec"])
        status = row["transcript_status"] or "pending"
        self._heading.setText(row["title"] or vid)
        self._subheading.setText(
            f"{dur}  ·  status: {status}  ·  "
            f"<a href='{_youtube_link(url, 0)}' style='color:#6AA1FF;'>"
            f"open on YouTube ↗</a>"
        )
        self._back_to_all.setVisible(True)
        self._render_chunks(vid, url, highlight=self._search_query)

    def _clear_video_selection(self) -> None:
        """Drop the currently-selected video so search goes back to
        matching across all videos. Triggered by the ← All videos button
        and the Escape key."""
        self._list.clearSelection()
        self._list.setCurrentItem(None)
        # clearSelection alone doesn't always fire currentItemChanged with
        # None on every Qt version; reset state explicitly as a belt.
        self._current_video_id = None
        self._current_video_url = ""
        self._back_to_all.setVisible(False)
        self._heading.setText("")
        self._subheading.setText("")
        # Stay in no-selection mode until the user clicks a video again.
        # Without this, any reload (e.g. typing in the search box) would
        # auto-reselect row 0 and undo the clear.
        self._no_selection_mode = True
        self.reload()

    def _render_cross_video_prompt(self) -> None:
        """Right-pane empty state when no video is selected and no search
        query is active. Nudges the user toward the two ways forward."""
        self._heading.setText("")
        self._subheading.setText("")
        self._browser.setHtml(
            f'<div style="color: {_COLOR_DIM}; margin-top: 24px;">'
            f'Pick a video on the left to see its transcript, or type in '
            f'the search box above to match chunks across every video.'
            f'</div>'
        )

    def _render_cross_video_chunks(self, query: str, limit: int = 200) -> None:
        """Search every video's transcript for ``query`` via FTS5 and
        render the matching chunks in the right pane, grouped by video
        with the video title as a clickable header. Lets the user scan
        matches across the whole corpus without having to pick one
        video first."""
        fts_q = _fts_phrase(query)
        rows = self._conn.execute(
            """
            SELECT vc.chunk_index, vc.start_sec, vc.end_sec, vc.text,
                   v.id AS vid, v.title AS vtitle, v.url AS vurl
              FROM video_chunks_fts f
              JOIN video_chunks vc ON vc.id = f.chunk_id
              JOIN videos v ON v.id = vc.video_id
             WHERE f.video_chunks_fts MATCH ?
             ORDER BY v.title, vc.chunk_index
             LIMIT ?
            """,
            (fts_q, limit),
        ).fetchall()

        self._heading.setText(f"🔍 Matches across all videos")
        self._subheading.setText(
            f"<span style='color:{_COLOR_DIM};'>"
            f"{len(rows):,} chunk(s) match "
            f"<b style='color:{_COLOR_TEXT};'>{_escape(query)}</b>. "
            f"Click a video title to open just that transcript, or a "
            f"timestamp to jump to YouTube."
            f"</span>"
        )

        if not rows:
            self._browser.setHtml(
                f'<div style="color: {_COLOR_DIM}; margin-top: 24px;">'
                f'No transcripts contain <b>{_escape(query)}</b>.</div>'
            )
            return

        # Group by video, render one block per video.
        parts: list[str] = []
        current_vid = None
        for r in rows:
            vid = r["vid"]
            if vid != current_vid:
                current_vid = vid
                is_active = (vid == self._active_video_id)
                # Active group: brighter card + gold accent stripe so the
                # user can see at a glance which video in the right pane
                # corresponds to their current focus in the left list
                # (and vice versa).
                if is_active:
                    header_bg = _COLOR_CARD_HI
                    stripe = _COLOR_GOLD
                    stripe_w = 5
                else:
                    header_bg = _COLOR_CARD
                    stripe = _COLOR_ACCENT
                    stripe_w = 3
                parts.append(
                    # Named anchor so the list-click path can scroll here.
                    f'<a name="vid-{vid}"></a>'
                    f'<div style="margin: 18px 0 6px 0; padding: 10px 12px;'
                    f' background: {header_bg};'
                    f' border-left: {stripe_w}px solid {stripe};'
                    f' border-radius: 6px;">'
                    f'<div style="font-weight: 600; color: {_COLOR_TEXT};'
                    f' font-size: 13px;">{_escape(r["vtitle"] or vid)}</div>'
                    f'<a href="tomtube-select:{vid}" style="color: {_COLOR_ACCENT};'
                    f' font-size: 11px;">open this video transcript →</a>'
                    f'</div>'
                )
            ts = _fmt_timestamp(r["start_sec"])
            ytl = _youtube_link(r["vurl"] or "", r["start_sec"])
            text = (r["text"] or "").replace("\n", " ")
            text_html = _highlight_query(text, query)
            parts.append(
                f'<div style="margin: 6px 0 6px 14px; padding: 10px 12px;'
                f' background: {_COLOR_CARD}; border-left: 2px solid {_COLOR_GOLD};'
                f' border-radius: 6px;">'
                # ▶ timestamp button: opens YouTube at the chunk start.
                f'<a href="{ytl}" style="display: inline-block;'
                f' background: {_COLOR_GOLD}; color: #1E1F22;'
                f' padding: 3px 9px; border-radius: 4px;'
                f' text-decoration: none; font-weight: 600; font-size: 11px;">'
                f'▶  {ts}</a>'
                # Transcript text: clicking it scrolls/highlights the
                # parent video in the left list without drilling into
                # single-video mode, so the user can keep scanning the
                # cross-video results.
                f'<a href="tomtube-highlight:{vid}" style="display: block;'
                f' margin-top: 8px; color: {_COLOR_TEXT}; font-size: 13px;'
                f' text-decoration: none;">'
                f'{text_html}'
                f'</a>'
                f'</div>'
            )
        self._browser.setHtml("".join(parts))

    def _render_chunks(
        self, vid: str, url: str, *, highlight: str = "",
    ) -> None:
        """Render every chunk as a card. If ``highlight`` is set (search
        query), matching chunks are bold-highlighted and listed first."""
        if highlight:
            # Search mode: only show chunks that match the query,
            # highlight the matched span. FTS5 joins via chunk_id keep
            # this sub-millisecond regardless of corpus size.
            chunks = self._conn.execute(
                "SELECT vc.chunk_index, vc.start_sec, vc.end_sec, vc.text "
                "  FROM video_chunks_fts f "
                "  JOIN video_chunks vc ON vc.id = f.chunk_id "
                " WHERE f.video_id = ? AND video_chunks_fts MATCH ? "
                " ORDER BY vc.chunk_index",
                (vid, _fts_phrase(highlight)),
            ).fetchall()
        else:
            chunks = self._conn.execute(
                "SELECT chunk_index, start_sec, end_sec, text FROM video_chunks "
                "WHERE video_id=? ORDER BY chunk_index",
                (vid,),
            ).fetchall()

        if not chunks:
            if highlight:
                body = (
                    f'<div style="color: {_COLOR_DIM}; margin-top: 24px;">'
                    f'No matches for <b>{highlight}</b> in this video. '
                    f'Clear the search to see all chunks.</div>'
                )
            else:
                body = (
                    f'<div style="color: {_COLOR_DIM}; margin-top: 24px;">'
                    f"This video hasn't been transcribed yet.</div>"
                )
            self._browser.setHtml(body)
            return

        parts: list[str] = []
        for c in chunks:
            ts = _fmt_timestamp(c["start_sec"])
            ytl = _youtube_link(url, c["start_sec"])
            text = (c["text"] or "").replace("\n", " ")
            text_html = _highlight_query(text, highlight) if highlight else _escape(text)
            parts.append(
                f'<div style="margin: 10px 0; padding: 12px 14px;'
                f' background: {_COLOR_CARD}; border-left: 3px solid {_COLOR_GOLD};'
                f' border-radius: 8px;">'
                # Big clickable timestamp button
                f'<a href="{ytl}" style="display: inline-block;'
                f' background: {_COLOR_GOLD}; color: #1E1F22;'
                f' padding: 4px 10px; border-radius: 4px;'
                f' text-decoration: none; font-weight: 600;'
                f' font-size: 12px;">▶  {ts}</a>'
                f'  <span style="color: {_COLOR_DIM}; font-size: 11px;">'
                f'opens YouTube at this moment</span>'
                f'<div style="margin-top: 8px; line-height: 1.55;">'
                f'{text_html}</div></div>'
            )
        self._browser.setHtml("\n".join(parts))

    # ==================================================================
    # Search
    # ==================================================================
    def _on_search_changed(self, text: str) -> None:
        """Live filter. Typing a query puts the right pane into the
        cross-video view (matches across every video in the corpus);
        clearing the query drops back into single-video mode. User can
        always click a specific video in the left list to drill in.
        """
        self._search_query = (text or "").strip()
        if self._search_query:
            # Typing a search = user is looking across the corpus, not
            # inside one video. Flip to no-selection mode so the right
            # pane renders cross-video matches immediately.
            self._current_video_id = None
            self._current_video_url = ""
            self._active_video_id = None
            self._no_selection_mode = True
            self._back_to_all.setVisible(False)
            self._list.clearSelection()
            self._list.setCurrentItem(None)
        else:
            # Clearing the query should return to the normal single-video
            # default so the app doesn't land on an empty right pane.
            self._no_selection_mode = False
            self._active_video_id = None
        self.reload()

    # ==================================================================
    # Anchors / keyboard nav
    # ==================================================================
    def _on_anchor(self, url) -> None:
        href = url.toString()
        if href.startswith("http"):
            # Use the shared helper: tells Windows to hand focus to the
            # next process that asks, then opens via QDesktopServices.
            # Without this, the browser opens behind Tom's Lab on
            # Windows when Tom's Lab is the foreground window.
            open_browser(href)
            return
        if href.startswith("tomtube-select:"):
            # Cross-video search result cards expose an "open this video
            # transcript →" link; clicking it drills into single-video
            # mode for that video. We have to flip out of no-selection
            # mode first, otherwise _on_video_selected would interpret
            # the row change as a cross-video scroll and never drill in.
            vid = href.split(":", 1)[1]
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == vid:
                    self._no_selection_mode = False
                    self._list.setCurrentRow(i)
                    return
        if href.startswith("tomtube-highlight:"):
            # Clicking transcript text in cross-video results: scroll
            # the left list to that video, highlight it, and apply the
            # shadow-highlight on the matching group in the right pane
            # (so the association is obvious both directions). Blocks
            # the currentItemChanged signal so this doesn't drill in.
            vid = href.split(":", 1)[1]
            self._active_video_id = vid
            if self._search_query:
                self._render_cross_video_chunks(self._search_query)
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == vid:
                    self._list.blockSignals(True)
                    try:
                        self._list.setCurrentItem(item)
                        self._list.scrollToItem(
                            item, self._list.ScrollHint.PositionAtCenter,
                        )
                    finally:
                        self._list.blockSignals(False)
                    return

    def _jump_next_chunk(self) -> None:
        """Page down to the next chunk card by finding the next anchor."""
        sb = self._browser.verticalScrollBar()
        sb.setValue(min(sb.maximum(), sb.value() + 220))

    def _jump_prev_chunk(self) -> None:
        sb = self._browser.verticalScrollBar()
        sb.setValue(max(0, sb.value() - 220))


# =====================================================================
# Text helpers
# =====================================================================
def _escape(text: str) -> str:
    import html as _html
    return _html.escape(text)


def _highlight_query(text: str, query: str) -> str:
    """Case-insensitive bold-highlight of ``query`` inside ``text`` without
    mangling surrounding HTML entities."""
    import html as _html
    import re as _re
    safe = _html.escape(text)
    if not query:
        return safe
    safe_q = _html.escape(query)
    pattern = _re.compile(_re.escape(safe_q), _re.IGNORECASE)
    return pattern.sub(
        lambda m: f"<b style='background: #3A3320; color: #FFC857;'>{m.group(0)}</b>",
        safe,
    )
