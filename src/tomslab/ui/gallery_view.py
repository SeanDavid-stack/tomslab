"""Gallery tab — grid of chart thumbnails with visual-search filtering."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QIcon, QPixmap, QPixmapCache
from PyQt6.QtWidgets import QLabel, QListView, QVBoxLayout, QWidget

from tomslab import visual


ROLE_MESSAGE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_ATT_ID = Qt.ItemDataRole.UserRole + 2
ROLE_PATH = Qt.ItemDataRole.UserRole + 3
ROLE_SCORE = Qt.ItemDataRole.UserRole + 4

THUMB_SIZE = 220
MAX_ROWS = 600


@dataclass
class GalleryItem:
    attachment_id: str
    message_id: str
    local_path: str
    filename: str
    timestamp: str
    author: str
    is_featured_speaker: bool
    score: float | None = None


class GalleryModel(QAbstractListModel):
    """Search-driven: shows charts from the messages / doc pages currently
    matching the user's Feed search. If there's no search and no visual
    query, the model is empty — no "browse everything" mode.
    """

    def __init__(self, conn: sqlite3.Connection, parent: Any = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._items: list[GalleryItem] = []
        self._query: str = ""
        self._scope_message_ids: list[str] | None = None  # restrict to these message ids

    # ------------------------------------------------------------------
    def set_query(self, query: str) -> None:
        self._query = (query or "").strip()
        self.reload()

    def set_message_scope(self, ids: list[str] | None) -> None:
        """Restrict gallery to attachments on these specific message ids.

        Used by MainWindow to mirror the Feed's search results into the
        Gallery — type "VPOC" in Keyword mode and the Gallery shows the
        charts attached to those matching Tom/Discord messages.
        """
        self._scope_message_ids = list(ids) if ids else None
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self._items = self._load_items()
        self.endResetModel()

    def count(self) -> int:
        return len(self._items)

    def current_query(self) -> str:
        return self._query

    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            date = (item.timestamp or "")[:10]
            return f"{date}  {item.author}"
        if role == Qt.ItemDataRole.DecorationRole:
            return _load_thumb_icon(item.local_path)
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = [
                f"<b>{item.author}</b>  {item.timestamp[:19].replace('T', ' ')}",
                item.filename,
            ]
            if item.score is not None:
                tip.append(f"score: {item.score:.3f}")
            return "<br>".join(tip)
        if role == ROLE_MESSAGE_ID:
            return item.message_id
        if role == ROLE_ATT_ID:
            return item.attachment_id
        if role == ROLE_PATH:
            return item.local_path
        if role == ROLE_SCORE:
            return item.score
        return None

    # ------------------------------------------------------------------
    def _load_items(self) -> list[GalleryItem]:
        # Three modes, in priority order:
        # 1. Scoped to message IDs set by the Feed search (Keyword/Semantic mode)
        # 2. Visual-search text query (Visual mode)
        # 3. Nothing -> empty
        if self._scope_message_ids is not None:
            return self._load_by_message_scope()
        if self._query:
            return self._load_visual_search()
        return []

    def _load_by_message_scope(self) -> list[GalleryItem]:
        ids = self._scope_message_ids or []
        if not ids:
            return []
        # Cap to avoid blowing SQLite's parameter limit (usually 32K, well above this).
        ids = ids[:MAX_ROWS]
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"""
            SELECT a.id AS aid, a.message_id AS mid, a.local_path AS path, a.filename AS fn,
                   m.timestamp AS ts, m.author_nickname AS nick, m.author_name AS aname,
                   m.is_featured_speaker AS feat
              FROM attachments a
              JOIN messages m ON m.id = a.message_id
             WHERE a.message_id IN ({placeholders})
               AND a.local_path IS NOT NULL AND a.local_path != ''
               AND (
                    lower(a.filename) LIKE '%.png'
                 OR lower(a.filename) LIKE '%.jpg'
                 OR lower(a.filename) LIKE '%.jpeg'
                 OR lower(a.filename) LIKE '%.gif'
                 OR lower(a.filename) LIKE '%.webp'
               )
             ORDER BY m.timestamp DESC
            """,
            ids,
        ).fetchall()
        # Preserve the message_id order (search relevance rank) above timestamp
        order = {mid: i for i, mid in enumerate(ids)}
        rows = sorted(rows, key=lambda r: (order.get(r["mid"], 10**9),))
        return [
            GalleryItem(
                attachment_id=r["aid"],
                message_id=r["mid"],
                local_path=r["path"],
                filename=r["fn"] or "",
                timestamp=r["ts"] or "",
                author=(r["nick"] or r["aname"] or "?"),
                is_featured_speaker=bool(r["feat"]),
            )
            for r in rows
        ]

    def _load_visual_search(self) -> list[GalleryItem]:
        try:
            hits = visual.visual_search(self._conn, self._query, limit=MAX_ROWS)
        except Exception:
            return []
        if not hits:
            return []
        ids = [h.attachment_id for h in hits]
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"""
            SELECT a.id AS aid, a.message_id AS mid, a.local_path AS path, a.filename AS fn,
                   m.timestamp AS ts, m.author_nickname AS nick, m.author_name AS aname,
                   m.is_featured_speaker AS feat
              FROM attachments a
              JOIN messages m ON m.id = a.message_id
             WHERE a.id IN ({placeholders})
            """,
            ids,
        ).fetchall()
        by_id = {r["aid"]: r for r in rows}
        out: list[GalleryItem] = []
        for h in hits:
            r = by_id.get(h.attachment_id)
            if r is None:
                continue
            out.append(
                GalleryItem(
                    attachment_id=r["aid"],
                    message_id=r["mid"],
                    local_path=r["path"],
                    filename=r["fn"] or "",
                    timestamp=r["ts"] or "",
                    author=(r["nick"] or r["aname"] or "?"),
                    is_featured_speaker=bool(r["feat"]),
                    score=h.score,
                )
            )
        return out


def _load_thumb_icon(path: str) -> QIcon | None:
    if not path:
        return None
    key = f"tomslab_gallery_thumb:{path}"
    pix = QPixmapCache.find(key)
    if pix is None:
        pix = QPixmap(path)
        if pix.isNull():
            return None
        pix = pix.scaled(
            THUMB_SIZE,
            THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        QPixmapCache.insert(key, pix)
    return QIcon(pix)


class GalleryView(QWidget):
    """Grid of thumbnails. Double-click or Enter opens the full-size
    image viewer — previously this jumped to the source message which
    was confusing when users are browsing charts."""

    image_opened = pyqtSignal(str)      # emits local_path on double-click / Enter
    message_activated = pyqtSignal(str)  # kept for compat but unused by default

    def __init__(self, conn: sqlite3.Connection, parent: Any = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._model = GalleryModel(conn, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setSpacing(12)
        self._list.setUniformItemSizes(True)
        self._list.setWordWrap(True)
        self._list.setGridSize(QSize(THUMB_SIZE + 12, THUMB_SIZE + 48))
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._list.setStyleSheet(
            "QListView { background: #1E1F22; color: #DBDEE1; border: none; }"
            "QListView::item { color: #DBDEE1; }"
        )
        # Only listen to one activation signal — on Windows both
        # doubleClicked and activated fire for a double-click, which
        # would fire the handler twice and briefly open the viewer twice.
        self._list.activated.connect(self._on_activated)
        layout.addWidget(self._list, stretch=1)

        self._empty_hint = QLabel(
            "No charts yet — import a DCE export with attachments."
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color: #949BA4; padding: 40px;")
        layout.addWidget(self._empty_hint)

        self._list.setVisible(False)
        self._empty_hint.setVisible(True)

    # ------------------------------------------------------------------
    def set_query(self, query: str) -> None:
        self._model.set_query(query)
        self._refresh_empty()

    def set_message_scope(self, ids: list[str] | None) -> None:
        self._model.set_message_scope(ids)
        self._refresh_empty()

    def reload(self) -> None:
        self._model.reload()
        self._refresh_empty()

    def count(self) -> int:
        return self._model.count()

    def _refresh_empty(self) -> None:
        n = self._model.count()
        self._list.setVisible(n > 0)
        if n > 0:
            self._empty_hint.setVisible(False)
            return
        self._empty_hint.setVisible(True)
        q = self._model.current_query()
        scoped = self._model._scope_message_ids is not None  # benign peek
        if q:
            self._empty_hint.setText(
                f"No charts matched “{q}”. Try a different phrase or "
                "rebuild image embeddings in File → Build image (CLIP) embeddings…"
            )
        elif scoped:
            self._empty_hint.setText(
                "The current search has no matching charts. "
                "Try a broader query, or switch to Visual mode."
            )
        else:
            self._empty_hint.setText(
                "Type a search above or switch mode to Visual — "
                "matching charts will appear here."
            )

    def _on_activated(self, idx: QModelIndex) -> None:
        path = self._model.data(idx, ROLE_PATH)
        if path:
            self.image_opened.emit(str(path))
