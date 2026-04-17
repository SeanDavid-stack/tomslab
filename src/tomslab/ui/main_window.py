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
from tomslab import embed_service, semantic
from tomslab.ingest.importer import ImportResult
from tomslab.paths import database_path
from tomslab.search import SearchMode
from tomslab.ui.embed_worker import EmbedWorker
from tomslab.ui.import_worker import ImportWorker
from tomslab.ui.message_delegate import MessageDelegate
from tomslab.ui.message_model import MAX_BROWSE_ROWS, MessageListModel
from tomslab.ui.settings_dialog import SettingsDialog


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
        self._embed_worker: EmbedWorker | None = None

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

        self._build_embed_action = QAction("&Build embeddings…", self)
        self._build_embed_action.triggered.connect(self._on_build_embeddings)
        file_menu.addAction(self._build_embed_action)

        settings_action = QAction("&Settings…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

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
        self._mode_combo.addItem("Semantic", userData="semantic")
        self._mode_combo.addItem("Visual (Phase 4)", userData="visual")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._mode_combo.model().item(2).setEnabled(False)  # visual arrives in Phase 4
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
        mode_str = self._mode_combo.currentData() or "keyword"
        mode = SearchMode(mode_str)
        self._delegate.set_match_terms(query if mode == SearchMode.KEYWORD else "")
        self._model.set_query(query, mode=mode)
        err = self._model.last_error()
        if err:
            QMessageBox.warning(self, "Search failed", err)
        self._list.scrollToTop()
        self._list.viewport().update()
        self._refresh_status()

    def _on_mode_changed(self, _idx: int) -> None:
        mode_str = self._mode_combo.currentData() or "keyword"
        mode = SearchMode(mode_str)
        if mode == SearchMode.SEMANTIC and not semantic.available(self._conn):
            QMessageBox.information(
                self,
                "Embeddings not built",
                "Semantic search needs embeddings.\n\n"
                "Go to File → Build embeddings… to create them. "
                "With Ollama's nomic-embed-text on a 3080-class GPU this takes ~5 min "
                "for the full Bookmap corpus.",
            )
            # revert to keyword
            idx = self._mode_combo.findData("keyword")
            if idx >= 0:
                self._mode_combo.blockSignals(True)
                self._mode_combo.setCurrentIndex(idx)
                self._mode_combo.blockSignals(False)
            return
        if self._search.text().strip():
            self._apply_search()

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
    # embeddings + settings
    # ------------------------------------------------------------------
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._conn, self)
        dlg.exec()
        # refresh status (embed/chat provider name may have changed)
        self._refresh_status()

    def _on_build_embeddings(self) -> None:
        if self._embed_worker is not None and self._embed_worker.isRunning():
            QMessageBox.information(self, "Already running", "An embedding run is already in progress.")
            return
        pending = embed_service.pending_count(self._conn)
        if pending == 0:
            QMessageBox.information(
                self,
                "Nothing to embed",
                "All conversation windows are already embedded.",
            )
            return
        reply = QMessageBox.question(
            self,
            "Build embeddings",
            f"Create embeddings for {pending:,} conversation windows?\n\n"
            "This enables Semantic search. Uses the embedding provider configured in "
            "Settings → AI Providers (Ollama by default). "
            "Runs in the background; you can keep browsing.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText("Embedding…")

        self._embed_worker = EmbedWorker(self)
        self._embed_worker.progress.connect(self._on_embed_progress)
        self._embed_worker.finished_ok.connect(self._on_embed_finished)
        self._embed_worker.failed.connect(self._on_embed_failed)
        self._embed_worker.start()

    def _on_embed_progress(self, done: int, total: int, status: str) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(done)
            self._status_label.setText(f"Embedding: {status}")
        else:
            self._status_label.setText(f"Embedding: {status}")

    def _on_embed_finished(self, n: int) -> None:
        self._progress_bar.setVisible(False)
        self._embed_worker = None
        semantic.invalidate_cache()
        self._refresh_status()
        QMessageBox.information(
            self, "Embeddings done", f"Embedded {n:,} new windows."
        )

    def _on_embed_failed(self, err: str) -> None:
        self._progress_bar.setVisible(False)
        self._embed_worker = None
        QMessageBox.critical(self, "Embedding failed", err)
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

        # Note embeddings status at tail end
        pending = embed_service.pending_count(self._conn)
        embed_note = ""
        if pending > 0:
            embed_note = f"   ·   {pending:,} windows not yet embedded (File → Build embeddings…)"

        if query:
            matches = self._model.total_matches()
            loadable = self._model.total_loadable()
            mode = self._model.current_mode().value
            if matches > loadable:
                text = (
                    f"{matches:,} {mode} matches for “{query}”   ·   "
                    f"showing top {loadable:,}   ·   {total:,} in DB"
                )
            else:
                text = f"{matches:,} {mode} matches for “{query}”   ·   {total:,} messages in DB"
            self._status_label.setText(text + embed_note)
        else:
            shown = min(total, MAX_BROWSE_ROWS)
            self._status_label.setText(
                f"{total:,} messages in DB   ·   showing newest {shown:,}" + embed_note
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
        if self._embed_worker is not None and self._embed_worker.isRunning():
            self._embed_worker.wait(2000)
        self._conn.close()
        super().closeEvent(event)
