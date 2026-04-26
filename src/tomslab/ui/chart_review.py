"""Review UI for borderline chart classifications.

The chart classifier auto-decides obvious cases (score > 0.70 or < 0.30)
and leaves the middle band (chart_decision IS NULL) for the user to
adjudicate here. This dialog shows those borderline images as a grid
of thumbnails sorted by chart-likeness score (highest first) so the
user scans top-down and calls 'Keep' or 'Discard' on ambiguous ones.

Bulk actions on the toolbar let the user nuke the remainder as keep-
or discard-all once they've eyeballed the top N. Nothing is deleted
from disk — decisions only update ``attachments.chart_decision``.
The separate File → Purge action is what actually moves files."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QSize,
    Qt,
)
from PyQt6.QtGui import QIcon, QPixmap, QPixmapCache, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ROLE_ATT_ID = Qt.ItemDataRole.UserRole + 1
ROLE_PATH   = Qt.ItemDataRole.UserRole + 2
ROLE_SCORE  = Qt.ItemDataRole.UserRole + 3

THUMB_SIZE = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReviewItem:
    attachment_id: str
    local_path: str
    filename: str
    score: float
    decision: str | None


class ReviewModel(QAbstractListModel):
    def __init__(self, conn: sqlite3.Connection, parent: Any = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self._items: list[ReviewItem] = []
        self._filter: str = "review"   # 'review' | 'auto_keep' | 'auto_discard' | 'all'

    def set_filter(self, f: str) -> None:
        self._filter = f
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self._items = self._load()
        self.endResetModel()

    def count(self) -> int:
        return len(self._items)

    def item_at(self, row: int) -> ReviewItem | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def _load(self) -> list[ReviewItem]:
        if self._filter == "review":
            where = (
                "chart_decision IS NULL "
                "AND local_path IS NOT NULL AND local_path != '' "
                "AND chart_score IS NOT NULL"
            )
            order = "chart_score DESC"
        elif self._filter == "auto_keep":
            where = "chart_decision IN ('auto_keep','keep')"
            order = "chart_score DESC"
        elif self._filter == "auto_discard":
            where = "chart_decision IN ('auto_discard','discard')"
            order = "chart_score DESC"
        else:
            where = "local_path IS NOT NULL AND local_path != ''"
            order = "COALESCE(chart_score, 0) DESC"
        rows = self._conn.execute(
            f"SELECT id, local_path, filename, COALESCE(chart_score, 0) AS s, "
            f"       chart_decision AS d "
            f"  FROM attachments WHERE {where} "
            f"  ORDER BY {order} "
            f"  LIMIT 2000"
        ).fetchall()
        return [
            ReviewItem(
                attachment_id=r["id"],
                local_path=r["local_path"] or "",
                filename=r["filename"] or "",
                score=float(r["s"] or 0.0),
                decision=r["d"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Qt model interface
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, idx: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not idx.isValid():
            return None
        item = self._items[idx.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            tag = item.decision or "review"
            return f"{item.score:.2f}  [{tag}]\n{item.filename}"
        if role == Qt.ItemDataRole.DecorationRole:
            return _load_thumb_icon(item.local_path)
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{item.filename}\n"
                f"score={item.score:.3f}  decision={item.decision or 'unset'}\n"
                f"{item.local_path}"
            )
        if role == ROLE_ATT_ID:
            return item.attachment_id
        if role == ROLE_PATH:
            return item.local_path
        if role == ROLE_SCORE:
            return item.score
        return None


def _load_thumb_icon(path: str) -> QIcon | None:
    if not path:
        return None
    key = f"tomslab_review_thumb:{path}"
    pix = QPixmapCache.find(key)
    if pix is None:
        pix = QPixmap(path)
        if pix.isNull():
            return None
        pix = pix.scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        QPixmapCache.insert(key, pix)
    return QIcon(pix)


class ChartReviewDialog(QDialog):
    """Modal dialog for reviewing borderline chart classifications.

    Workflow:
      1. Filter defaults to 'needs review' — borderline images sorted by
         score, highest first (most likely to be charts at the top).
      2. User multi-selects thumbnails and clicks Keep / Discard.
      3. Toolbar buttons 'Keep all remaining' / 'Discard all remaining'
         let the user batch-action the rest once they've eyeballed the top.
      4. Close → decisions are persisted; the separate Purge action
         actually moves discarded files off disk.
    """

    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle("Tom's Lab — Review chart classifications")
        self.setModal(True)
        self.resize(1100, 780)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        # --- summary strip -------------------------------------------------
        self._summary = QLabel()
        self._summary.setStyleSheet(
            "color: #DBDEE1; font-size: 12px; padding: 4px 2px;"
        )
        outer.addWidget(self._summary)

        # --- filter + action row ------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("Showing:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("Needs review (middle band)", "review")
        self._filter_combo.addItem("Auto-kept (likely charts)",  "auto_keep")
        self._filter_combo.addItem("Auto-discarded (likely junk)", "auto_discard")
        self._filter_combo.addItem("All classified",             "all")
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        row.addWidget(self._filter_combo)

        row.addStretch(1)

        self._keep_btn = QPushButton("Keep selected (K)")
        self._keep_btn.setStyleSheet(self._btn_style(primary=True))
        self._keep_btn.clicked.connect(lambda: self._mark_selected("keep"))
        row.addWidget(self._keep_btn)

        self._discard_btn = QPushButton("Discard selected (D)")
        self._discard_btn.setStyleSheet(self._btn_style())
        self._discard_btn.clicked.connect(lambda: self._mark_selected("discard"))
        row.addWidget(self._discard_btn)

        self._reset_btn = QPushButton("Revert (R)")
        self._reset_btn.setStyleSheet(self._btn_style())
        self._reset_btn.clicked.connect(lambda: self._mark_selected(None))
        row.addWidget(self._reset_btn)

        outer.addLayout(row)

        # --- bulk row ------------------------------------------------------
        row2 = QHBoxLayout()
        row2.addStretch(1)
        self._keep_all_btn = QPushButton("Keep ALL remaining in view")
        self._keep_all_btn.setStyleSheet(self._btn_style())
        self._keep_all_btn.clicked.connect(lambda: self._mark_all_visible("keep"))
        row2.addWidget(self._keep_all_btn)

        self._discard_all_btn = QPushButton("Discard ALL remaining in view")
        self._discard_all_btn.setStyleSheet(self._btn_style())
        self._discard_all_btn.clicked.connect(
            lambda: self._mark_all_visible("discard")
        )
        row2.addWidget(self._discard_all_btn)
        outer.addLayout(row2)

        # --- grid ----------------------------------------------------------
        self._model = ReviewModel(conn, self)
        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setSpacing(10)
        self._list.setUniformItemSizes(True)
        self._list.setGridSize(QSize(THUMB_SIZE + 14, THUMB_SIZE + 54))
        self._list.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._list.setStyleSheet(
            "QListView { background: #1E1F22; color: #DBDEE1; border: 1px solid #3F4147; }"
            "QListView::item { color: #DBDEE1; }"
            "QListView::item:selected { background: #4E5058; color: white; }"
        )
        outer.addWidget(self._list, stretch=1)

        # --- close ---------------------------------------------------------
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton("Done")
        close.setStyleSheet(self._btn_style(primary=True))
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        outer.addLayout(close_row)

        # --- keyboard ------------------------------------------------------
        QShortcut(QKeySequence("K"), self,
                  activated=lambda: self._mark_selected("keep"))
        QShortcut(QKeySequence("D"), self,
                  activated=lambda: self._mark_selected("discard"))
        QShortcut(QKeySequence("R"), self,
                  activated=lambda: self._mark_selected(None))

        self._model.reload()
        self._refresh_summary()

    # ------------------------------------------------------------------
    def _btn_style(self, *, primary: bool = False) -> str:
        if primary:
            return (
                "QPushButton { background: #FFC857; color: #1E1F22;"
                " padding: 8px 16px; border: none; border-radius: 6px;"
                " font-weight: 600; font-size: 12px; }"
                "QPushButton:hover:enabled { background: #FFD87A; }"
            )
        return (
            "QPushButton { background: transparent; color: #DBDEE1;"
            " padding: 8px 14px; border: 1px solid #3F4147;"
            " border-radius: 6px; font-size: 12px; }"
            "QPushButton:hover { border-color: #DBDEE1; }"
        )

    # ------------------------------------------------------------------
    def _on_filter_changed(self) -> None:
        val = self._filter_combo.currentData()
        self._model.set_filter(val)
        self._refresh_summary()

    def _selected_ids(self) -> list[str]:
        ids: list[str] = []
        for idx in self._list.selectionModel().selectedIndexes():
            item = self._model.item_at(idx.row())
            if item:
                ids.append(item.attachment_id)
        return ids

    def _mark_selected(self, decision: str | None) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        self._apply(ids, decision)

    def _mark_all_visible(self, decision: str) -> None:
        ids = [
            self._model.item_at(i).attachment_id  # type: ignore[union-attr]
            for i in range(self._model.count())
            if self._model.item_at(i) is not None
        ]
        if not ids:
            return
        label = "keep" if decision == "keep" else "discard"
        if QMessageBox.question(
            self,
            f"Bulk {label}",
            f"Apply '{label}' to all {len(ids)} images currently shown?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._apply(ids, decision)

    def _apply(self, ids: list[str], decision: str | None) -> None:
        placeholders = ",".join("?" * len(ids))
        params: list[Any] = [decision, _now()] + ids
        self._conn.execute(
            f"UPDATE attachments SET chart_decision = ?, classified_at = ? "
            f"WHERE id IN ({placeholders})",
            params,
        )
        self._conn.commit()
        self._model.reload()
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        row = self._conn.execute(
            """
            SELECT
              SUM(CASE WHEN chart_decision IN ('keep','auto_keep') THEN 1 ELSE 0 END) AS keep_n,
              SUM(CASE WHEN chart_decision IN ('discard','auto_discard') THEN 1 ELSE 0 END) AS discard_n,
              SUM(CASE WHEN chart_decision IS NULL AND chart_score IS NOT NULL THEN 1 ELSE 0 END) AS review_n,
              SUM(CASE WHEN chart_score IS NULL THEN 1 ELSE 0 END) AS unscored_n,
              COUNT(*) AS total_n
            FROM attachments
            WHERE local_path IS NOT NULL AND local_path != ''
            """
        ).fetchone()
        keep   = int(row["keep_n"] or 0)
        disc   = int(row["discard_n"] or 0)
        review = int(row["review_n"] or 0)
        unsc   = int(row["unscored_n"] or 0)
        total  = int(row["total_n"] or 0)
        showing = self._model.count()
        self._summary.setText(
            f"<b>{total:,}</b> attachments total · "
            f"<span style='color:#43B581;'>{keep:,} keep</span> · "
            f"<span style='color:#ED4245;'>{disc:,} discard</span> · "
            f"<span style='color:#FFC857;'>{review:,} needs review</span> · "
            f"{unsc:,} not yet scored · "
            f"showing {showing:,}"
        )
