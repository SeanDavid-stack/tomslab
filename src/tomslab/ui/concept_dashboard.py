"""Per-concept dashboard — single-page view of every source type for one
glossary term: top Discord posts, Tom's PDF pages, and TomTube clips.

Where the evolution dialog organises content by *time* (how Tom's framing
changed), this dashboard organises by *source* (what the app has on this
concept, ranked best-first). Complementary, not redundant — the user can
jump between them from the same concept chip.
"""
from __future__ import annotations

import html
import sqlite3
from typing import Callable

from tomslab.ui.browser_open import open_browser

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)


_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_BLUE = "#8FA1FF"
_COLOR_RED = "#FF6B6B"


class ConceptDashboard(QDialog):
    """Rich single-concept view: Tom's own explanations (PDFs) first,
    then video clips, then Discord posts — each bucketed and capped so
    the dashboard stays scannable.

    Clicking a citation delegates to the caller via ``on_citation_clicked``
    (msg / doc routes to detail dialog). Video clips open YouTube at
    the timestamp inline via webbrowser.
    """

    _PDF_CAP = 8
    _VIDEO_CAP = 10
    _DISCORD_CAP = 15

    def __init__(
        self,
        conn: sqlite3.Connection,
        concept: str,
        on_citation_clicked: Callable[[str, str], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._concept = concept
        self._on_citation = on_citation_clicked
        self.setWindowTitle(f"Dashboard — {concept}")
        self.resize(1000, 780)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        heading = QLabel(f"<b>{html.escape(self._concept)}</b> — everything Tom's Lab has")
        heading.setStyleSheet(f"color: {_COLOR_TEXT}; font-size: 16px;")
        outer.addWidget(heading)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {_COLOR_DIM}; font-size: 11px;")
        outer.addWidget(self._summary)

        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.setOpenLinks(False)
        self._body.anchorClicked.connect(self._on_anchor)
        self._body.setStyleSheet(
            f"QTextBrowser {{ background: {_COLOR_BG}; color: {_COLOR_TEXT};"
            f" border: 1px solid #3F4147; border-radius: 8px;"
            f" padding: 10px 14px; font-size: 12px; }}"
        )
        outer.addWidget(self._body, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        evo = QPushButton("View timeline evolution →")
        evo.clicked.connect(self._open_evolution)
        evo.setStyleSheet(self._btn_style())
        btn_row.addWidget(evo)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        close.setStyleSheet(self._btn_style())
        btn_row.addWidget(close)
        outer.addLayout(btn_row)

    def _btn_style(self) -> str:
        return (
            f"QPushButton {{ background: {_COLOR_CARD}; color: {_COLOR_TEXT};"
            f" padding: 6px 16px; border: 1px solid #3F4147;"
            f" border-radius: 4px; }}"
            f"QPushButton:hover {{ color: white; }}"
        )

    # ------------------------------------------------------------------
    def _load(self) -> None:
        concept = self._concept
        # --- Tom's PDFs ------------------------------------------------
        # Prioritise Tom-authored docs by filtering author field. The
        # schema stores it on documents; doc_pages joins back.
        pdf_rows = self._conn.execute(
            """
            SELECT p.id AS pid, p.page_num AS pnum,
                   COALESCE(NULLIF(p.ocr_text,''), p.extracted_text) AS text,
                   d.title AS title, d.filename AS fn, d.author AS author
              FROM document_pages p
              JOIN documents d ON d.id = p.document_id
             WHERE COALESCE(NULLIF(p.ocr_text,''), p.extracted_text)
                   LIKE ? COLLATE NOCASE
             ORDER BY CASE WHEN d.author = 'Tom B' THEN 0 ELSE 1 END,
                      d.id, p.page_num
             LIMIT ?
            """,
            (f"%{concept}%", self._PDF_CAP),
        ).fetchall()

        # --- video chunks ---------------------------------------------
        vid_rows = self._conn.execute(
            """
            SELECT c.id AS cid, c.start_sec AS ss, c.text AS text,
                   v.title AS title, v.url AS url, v.added_at AS added
              FROM video_chunks c
              JOIN videos v ON v.id = c.video_id
             WHERE c.text LIKE ? COLLATE NOCASE
             ORDER BY v.added_at DESC, c.chunk_index
             LIMIT ?
            """,
            (f"%{concept}%", self._VIDEO_CAP),
        ).fetchall()

        # --- Discord posts --------------------------------------------
        # FTS5 with rank so top hits come back first.
        msg_rows = self._conn.execute(
            """
            SELECT m.id, m.author_nickname, m.author_name, m.timestamp,
                   m.content, bm25(messages_fts) AS rank
              FROM messages_fts
              JOIN messages m ON m.id = messages_fts.id
             WHERE messages_fts MATCH ?
             ORDER BY rank
             LIMIT ?
            """,
            (f'"{concept}"', self._DISCORD_CAP),
        ).fetchall()

        self._summary.setText(
            f"{len(pdf_rows):,} PDF page(s) · {len(vid_rows):,} video chunk(s) · "
            f"{len(msg_rows):,} Discord post(s). "
            "Sources that are likely Tom's own voice (authored PDFs, "
            "TomTube clips) are prioritised; Discord is filtered for "
            "top keyword relevance."
        )

        parts: list[str] = [self._section_pdfs(pdf_rows),
                            self._section_videos(vid_rows),
                            self._section_discord(msg_rows)]
        if not any(parts):
            self._body.setHtml(
                f"<p style='color:{_COLOR_DIM};'>Nothing in the corpus "
                f"mentions <b>{html.escape(concept)}</b> yet.</p>"
            )
            return
        self._body.setHtml("".join(parts))

    def _section_header(self, icon: str, title: str, color: str, n: int) -> str:
        return (
            f'<div style="margin: 16px 0 6px 0; padding-left: 6px;'
            f' border-left: 3px solid {color};'
            f' font-size: 13px; font-weight: 600; color: {color};">'
            f'{icon} {html.escape(title)}'
            f'&nbsp;&nbsp;<span style="color: {_COLOR_DIM}; font-weight: 400;'
            f' font-size: 11px;">({n} shown)</span></div>'
        )

    def _card(self, href: str, pill_fg: str, pill_bg: str,
              pill_label: str, preview: str) -> str:
        return (
            f'<div style="margin: 6px 0 10px 22px; padding: 10px 12px;'
            f' background: {_COLOR_CARD}; border-radius: 6px;">'
            f'<div style="display: inline-block; margin-bottom: 4px;">'
            f'<a href="{html.escape(href)}" style="color: {pill_fg};'
            f' text-decoration: none; background: {pill_bg};'
            f' padding: 2px 8px; border-radius: 4px; font-size: 11px;">'
            f'{pill_label}</a></div>'
            f'<div style="margin-top: 4px; line-height: 1.5;">{preview}</div>'
            f'</div>'
        )

    def _section_pdfs(self, rows) -> str:
        if not rows:
            return ""
        out = [self._section_header("📄", "From Tom's PDFs", _COLOR_GOLD, len(rows))]
        for r in rows:
            text = _truncate((r["text"] or "").strip())
            title = r["title"] or r["fn"] or "doc"
            label = f"{html.escape(title)} · p{int(r['pnum'])}"
            out.append(self._card(
                f"doc:{int(r['pid'])}",
                _COLOR_GOLD, "rgba(255,200,87,0.12)",
                label, html.escape(text),
            ))
        return "".join(out)

    def _section_videos(self, rows) -> str:
        if not rows:
            return ""
        out = [self._section_header("▶", "From TomTube transcripts", _COLOR_RED, len(rows))]
        for r in rows:
            text = _truncate((r["text"] or "").strip())
            ss = float(r["ss"] or 0.0)
            mm = int(ss // 60)
            title = (r["title"] or "")[:60]
            label = f"▶ {mm}:{int(ss % 60):02d} · {html.escape(title)}"
            out.append(self._card(
                f"vid:{int(r['cid'])}",
                _COLOR_RED, "rgba(255,77,77,0.14)",
                label, html.escape(text),
            ))
        return "".join(out)

    def _section_discord(self, rows) -> str:
        if not rows:
            return ""
        out = [self._section_header("💬", "From Discord", _COLOR_BLUE, len(rows))]
        for r in rows:
            nick = r["author_nickname"] or r["author_name"] or "?"
            date = (r["timestamp"] or "")[:10]
            text = _truncate((r["content"] or "").strip())
            label = f"{html.escape(nick)} · {date}"
            out.append(self._card(
                f"msg:{r['id']}",
                _COLOR_BLUE, "rgba(88,101,242,0.14)",
                label, html.escape(text),
            ))
        return "".join(out)

    # ------------------------------------------------------------------
    def _on_anchor(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("vid:"):
            try:
                cid = int(href[len("vid:"):])
            except ValueError:
                return
            row = self._conn.execute(
                """
                SELECT v.url AS url, c.start_sec AS ss
                  FROM video_chunks c JOIN videos v ON v.id = c.video_id
                 WHERE c.id = ?
                """,
                (cid,),
            ).fetchone()
            if row:
                from tomslab.chat import _youtube_link
                open_browser(_youtube_link(row["url"] or "",
                                           float(row["ss"] or 0.0)))
            return
        if ":" in href and self._on_citation:
            kind, raw = href.split(":", 1)
            self._on_citation(kind, raw)

    def _open_evolution(self) -> None:
        """Bridge to the sister dialog — same concept, time-grouped view."""
        from tomslab.ui.evolution_dialog import EvolutionDialog
        dlg = EvolutionDialog(self._conn, self._concept,
                              on_citation_clicked=self._on_citation,
                              parent=self.parent())
        dlg.exec()


def _truncate(text: str, limit: int = 300) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
