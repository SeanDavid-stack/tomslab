"""Main application window — Phase 2.

Phase 1 gave us import + a plain list.  Phase 2 adds:
  * search bar (keyword mode via FTS5; semantic/visual placeholders)
  * Discord-styled cards (gold accent for Tom, replies, inline charts,
    in-body match highlighting)
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QPalette,
    QPixmapCache,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from tomslab import __app_name__, __version__
from tomslab import db as dbmod
from tomslab.ingest.importer import ImportResult
from tomslab.paths import database_path
from tomslab.ui.import_worker import ImportWorker
from tomslab.ui.message_delegate import MessageDelegate
from tomslab.ui.message_model import MAX_BROWSE_ROWS, MessageListModel


# cap the image cache so 10K messages worth of charts don't eat all RAM
QPixmapCache.setCacheLimit(256 * 1024)  # 256 MB


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.resize(1200, 820)
        self.setAcceptDrops(True)
        self._apply_dark_palette()

        self._conn = dbmod.connect()
        dbmod.initialise(self._conn)

        self._model = MessageListModel(self._conn, self)
        self._delegate = MessageDelegate(self)
        self._worker: ImportWorker | None = None

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self._apply_search)

        self._build_menu()
        self._build_ui()
        self._refresh_status()

    # ------------------------------------------------------------------
    # palette
    # ------------------------------------------------------------------
    def _apply_dark_palette(self) -> None:
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#1E1F22"))
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#DBDEE1"))
        pal.setColor(QPalette.ColorRole.Base, QColor("#2B2D31"))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#2B2D31"))
        pal.setColor(QPalette.ColorRole.Text, QColor("#DBDEE1"))
        pal.setColor(QPalette.ColorRole.Button, QColor("#2B2D31"))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor("#DBDEE1"))
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#5865F2"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2B2D31"))
        pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#DBDEE1"))
        self.setPalette(pal)

    # ------------------------------------------------------------------
    # menus
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        import_action = QAction("&Import DCE JSON…", self)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(self._on_import_clicked)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = menu.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------------
    # main widgets
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 6)
        outer.setSpacing(6)

        # --- search bar ------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search messages (keyword) — e.g. VPOC absorption, overnight inventory…"
        )
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.returnPressed.connect(self._apply_search)
        self._search.setStyleSheet(
            "QLineEdit { background: #1E1F22; color: #DBDEE1; padding: 6px 10px;"
            " border: 1px solid #3F4147; border-radius: 6px; }"
            "QLineEdit:focus { border: 1px solid #5865F2; }"
        )
        bar.addWidget(self._search, stretch=1)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Keyword", userData="keyword")
        self._mode_combo.addItem("Semantic (Phase 3)", userData="semantic")
        self._mode_combo.addItem("Visual (Phase 4)", userData="visual")
        # disable the future modes so users don't think they're broken
        self._mode_combo.model().item(1).setEnabled(False)
        self._mode_combo.model().item(2).setEnabled(False)
        self._mode_combo.setStyleSheet(
            "QComboBox { background: #1E1F22; color: #DBDEE1;"
            " padding: 6px 10px; border: 1px solid #3F4147; border-radius: 6px; }"
        )
        bar.addWidget(self._mode_combo)
        outer.addLayout(bar)

        # --- empty state hint ------------------------------------------
        self._empty_hint = QLabel(
            "No messages yet. Drag a DCE JSON file here, or File → Import DCE JSON (Ctrl+I)."
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color: #949BA4; padding: 40px;")
        outer.addWidget(self._empty_hint)

        # --- list view -------------------------------------------------
        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setItemDelegate(self._delegate)
        self._list.setUniformItemSizes(False)   # our delegate returns variable heights
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._list.setSpacing(0)
        self._list.setStyleSheet(
            "QListView { background: #1E1F22; border: none; }"
            "QListView::item { padding: 0; }"
        )
        outer.addWidget(self._list, stretch=1)

        self.setCentralWidget(central)

        # status bar
        sb = QStatusBar()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #949BA4;")
        sb.addWidget(self._status_label, stretch=1)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(240)
        self._progress_bar.setVisible(False)
        sb.addPermanentWidget(self._progress_bar)
        self.setStatusBar(sb)

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def _on_search_text_changed(self, _text: str) -> None:
        self._search_debounce.start()

    def _apply_search(self) -> None:
        query = self._search.text().strip()
        self._delegate.set_match_terms(query)
        self._model.set_query(query)
        # Reset scroll on each new search
        self._list.scrollToTop()
        self._list.viewport().update()
        self._refresh_status()

    # ------------------------------------------------------------------
    # drag and drop
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".json"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".json"):
                self._start_import(Path(path))
                return

    # ------------------------------------------------------------------
    # import flow
    # ------------------------------------------------------------------
    def _on_import_clicked(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Import DCE JSON", "", "DCE JSON export (*.json)"
        )
        if path_str:
            self._start_import(Path(path_str))

    def _start_import(self, json_path: Path) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Import running", "Another import is still running.")
            return
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText(f"Importing {json_path.name}…")

        self._worker = ImportWorker(json_path, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_import_finished)
        self._worker.failed.connect(self._on_import_failed)
        self._worker.start()

    def _on_progress(self, phase: str, current: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._status_label.setText(f"{phase}: {current:,} / {total:,}")
        else:
            self._progress_bar.setRange(0, 0)
            self._status_label.setText(phase)

    def _on_import_finished(self, result: ImportResult) -> None:
        self._progress_bar.setVisible(False)
        self._worker = None
        self._model.reload()
        self._refresh_status()
        QMessageBox.information(
            self,
            "Import complete",
            f"Import complete.\n\n"
            f"Added: {result.messages_added:,} messages, "
            f"{result.attachments_added:,} attachments.\n"
            f"Skipped (already in DB): {result.messages_skipped:,}.\n"
            f"Conversation windows built: {result.windows_built:,}.",
        )

    def _on_import_failed(self, err: str) -> None:
        self._progress_bar.setVisible(False)
        self._worker = None
        QMessageBox.critical(self, "Import failed", err)
        self._refresh_status()

    # ------------------------------------------------------------------
    # status bar
    # ------------------------------------------------------------------
    def _refresh_status(self) -> None:
        total = self._model.total_in_db()
        query = self._model.current_query()
        self._empty_hint.setVisible(total == 0)
        self._list.setVisible(total > 0)
        if total == 0:
            self._status_label.setText("Database empty. Import a DCE JSON to begin.")
            return
        if query:
            matches = self._model.total_matches()
            loadable = self._model.total_loadable()
            if matches > loadable:
                text = (
                    f"{matches:,} matches for “{query}”   ·   "
                    f"showing top {loadable:,}   ·   {total:,} in DB"
                )
            else:
                text = f"{matches:,} matches for “{query}”   ·   {total:,} messages in DB"
            self._status_label.setText(text)
        else:
            shown = min(total, MAX_BROWSE_ROWS)
            self._status_label.setText(
                f"{total:,} messages in DB   ·   showing newest {shown:,}"
            )

    # ------------------------------------------------------------------
    # about
    # ------------------------------------------------------------------
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {__app_name__}",
            f"<b>{__app_name__}</b> v{__version__}<br><br>"
            "Desktop study tool for the Bookmap Discord<br>"
            "<code>traders-lab-tom-b</code> channel.<br><br>"
            f"Database: <code>{database_path()}</code>",
        )

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        self._conn.close()
        super().closeEvent(event)
