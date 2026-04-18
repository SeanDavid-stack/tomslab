"""Docs tab — browse Tom's PDFs (and the third-party references) inside the app.

Left: compact list of ingested documents.
Right: scrollable grid of every rendered page thumbnail for the selected doc.
Activate a page (double-click / Enter) → emits ``page_opened(path)`` so
MainWindow can route it into the existing ImageViewerDialog.
"""
from __future__ import annotations

import sqlite3

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPixmapCache
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


# Palette (matches the rest of the app).
_COLOR_BG = "#1E1F22"
_COLOR_CARD = "#2B2D31"
_COLOR_TEXT = "#DBDEE1"
_COLOR_DIM = "#949BA4"
_COLOR_GOLD = "#FFC857"
_COLOR_BLUE = "#6AA1FF"
_COLOR_BORDER = "#3F4147"


ROLE_PATH = Qt.ItemDataRole.UserRole + 1
ROLE_PAGE_ID = Qt.ItemDataRole.UserRole + 2
THUMB_SIZE = 240


# ---------------------------------------------------------------------------
# page-grid model for one document
# ---------------------------------------------------------------------------
class _PageGridModel(QAbstractListModel):
    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._doc_id: int | None = None
        self._rows: list[sqlite3.Row] = []

    def load_document(self, doc_id: int | None) -> None:
        self.beginResetModel()
        self._doc_id = doc_id
        if doc_id is None:
            self._rows = []
        else:
            self._rows = self._conn.execute(
                """
                SELECT id AS page_id, page_num, rendered_path, text_source,
                       COALESCE(NULLIF(ocr_text,''), extracted_text) AS preview
                  FROM document_pages
                 WHERE document_id = ?
                 ORDER BY page_num
                """,
                (doc_id,),
            ).fetchall()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        r = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"page {r['page_num']}"
        if role == Qt.ItemDataRole.DecorationRole:
            return _load_thumb_icon(r["rendered_path"])
        if role == Qt.ItemDataRole.ToolTipRole:
            preview = (r["preview"] or "").strip()
            if len(preview) > 280:
                preview = preview[:277] + "…"
            return (
                f"<b>page {r['page_num']}</b> "
                f"<span style='color:{_COLOR_DIM};'>· {r['text_source'] or '—'}</span>"
                f"<br><br>{preview or '<i>(no text on this page)</i>'}"
            )
        if role == ROLE_PATH:
            return r["rendered_path"]
        if role == ROLE_PAGE_ID:
            return int(r["page_id"])
        return None


def _load_thumb_icon(path: str) -> QIcon | None:
    if not path:
        return None
    key = f"tomslab_docpage_thumb:{path}"
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


# ---------------------------------------------------------------------------
# main Docs view
# ---------------------------------------------------------------------------
class DocsView(QWidget):
    page_opened = pyqtSignal(str)       # absolute path of the rendered page PNG

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._model = _PageGridModel(conn, self)
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(
            f"QSplitter::handle {{ background: {_COLOR_BORDER}; width: 1px; }}"
        )

        # ---- left: document list -------------------------------------
        left = QWidget()
        left.setStyleSheet(f"background: {_COLOR_CARD};")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 10, 8, 10)
        left_lay.setSpacing(6)

        title = QLabel("Tom's reference PDFs")
        title.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-weight: 600; font-size: 12px; padding: 4px 6px;"
        )
        left_lay.addWidget(title)

        self._doc_list = QListWidget()
        self._doc_list.setStyleSheet(
            f"QListWidget {{ background: transparent; color: {_COLOR_TEXT};"
            f" border: none; padding: 4px; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 8px 8px; border-radius: 6px; }}"
            f"QListWidget::item:hover {{ background: {_COLOR_BG}; }}"
            f"QListWidget::item:selected {{ background: #3A3320; color: {_COLOR_GOLD}; }}"
        )
        self._doc_list.currentItemChanged.connect(self._on_doc_selected)
        left_lay.addWidget(self._doc_list, stretch=1)
        split.addWidget(left)

        # ---- right: page grid ----------------------------------------
        right = QWidget()
        right.setStyleSheet(f"background: {_COLOR_BG};")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(12, 10, 12, 10)
        right_lay.setSpacing(6)

        self._heading = QLabel("")
        self._heading.setStyleSheet(
            f"color: {_COLOR_TEXT}; font-weight: 600; font-size: 14px;"
        )
        right_lay.addWidget(self._heading)
        self._subheading = QLabel("")
        self._subheading.setStyleSheet(f"color: {_COLOR_DIM}; font-size: 11px;")
        right_lay.addWidget(self._subheading)

        self._grid = QListView()
        self._grid.setModel(self._model)
        self._grid.setViewMode(QListView.ViewMode.IconMode)
        self._grid.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._grid.setResizeMode(QListView.ResizeMode.Adjust)
        self._grid.setMovement(QListView.Movement.Static)
        self._grid.setSpacing(10)
        self._grid.setUniformItemSizes(True)
        self._grid.setWordWrap(True)
        self._grid.setGridSize(QSize(THUMB_SIZE + 12, THUMB_SIZE + 40))
        self._grid.setStyleSheet(
            f"QListView {{ background: {_COLOR_BG}; color: {_COLOR_DIM};"
            f" border: none; }}"
        )
        self._grid.activated.connect(self._on_page_activated)
        right_lay.addWidget(self._grid, stretch=1)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, stretch=1)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._doc_list.blockSignals(True)
        self._doc_list.clear()

        rows = self._conn.execute(
            """
            SELECT id, title, filename, author, doc_type, page_count
              FROM documents
             ORDER BY (CASE author WHEN 'tom_b' THEN 0 WHEN 'community_forum'
                                    THEN 1 WHEN 'third_party' THEN 2 ELSE 3 END),
                      title
            """
        ).fetchall()
        for r in rows:
            author = (r["author"] or "").strip()
            icon_prefix = "⭐" if author == "tom_b" else (
                "🌐" if author == "community_forum" else "📘"
            )
            title = r["title"] or r["filename"] or "(untitled)"
            item = QListWidgetItem(
                f"{icon_prefix} {title}\n     {r['page_count'] or 0} pages · {author or 'unknown'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, int(r["id"]))
            item.setToolTip(
                f"<b>{title}</b><br>"
                f"{r['filename']}<br>{r['page_count'] or 0} pages · author: {author or 'unknown'}"
            )
            self._doc_list.addItem(item)
        self._doc_list.blockSignals(False)
        if self._doc_list.count():
            self._doc_list.setCurrentRow(0)
        else:
            self._heading.setText("No documents ingested yet")
            self._subheading.setText("")
            self._model.load_document(None)

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def _on_doc_selected(self, current, _previous) -> None:
        if current is None:
            self._model.load_document(None)
            self._heading.setText("")
            self._subheading.setText("")
            return
        doc_id = int(current.data(Qt.ItemDataRole.UserRole))
        row = self._conn.execute(
            "SELECT title, filename, author, page_count, source_path "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return
        self._heading.setText(row["title"] or row["filename"] or "(untitled)")
        tail = ""
        if row["source_path"]:
            sp = row["source_path"]
            if sp.startswith("http"):
                tail = f"   ·   {sp}"
        self._subheading.setText(
            f"{row['page_count'] or 0} pages · author: {row['author'] or 'unknown'}{tail}"
        )
        self._model.load_document(doc_id)

    def _on_page_activated(self, idx: QModelIndex) -> None:
        path = self._model.data(idx, ROLE_PATH)
        if path:
            self.page_opened.emit(str(path))
