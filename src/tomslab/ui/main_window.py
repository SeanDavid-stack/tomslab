"""Main application window — Phase 1.

Shows a list of imported messages and provides File → Import (and drag-and-drop)
for DCE JSON files. Search, highlighting, chart rendering, and the other
goodies arrive in Phase 2+.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
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
from tomslab.ui.message_model import MAX_ROWS, MessageListModel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.resize(1100, 700)
        self.setAcceptDrops(True)

        self._conn = dbmod.connect()
        dbmod.initialise(self._conn)

        self._model = MessageListModel(self._conn, self)
        self._worker: ImportWorker | None = None

        self._build_menu()
        self._build_ui()
        self._refresh_status()

    # ---- UI construction ----------------------------------------------
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

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        self._empty_hint = QLabel(
            "No messages yet. Drag a DCE JSON file here, or File → Import DCE JSON (Ctrl+I)."
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color: #888; padding: 40px;")
        layout.addWidget(self._empty_hint)

        self._list = QListView()
        self._list.setModel(self._model)
        self._list.setUniformItemSizes(True)
        self._list.setAlternatingRowColors(True)
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._list.setStyleSheet(
            "QListView { font-family: Consolas, 'Courier New', monospace; font-size: 12px; }"
        )
        layout.addWidget(self._list, stretch=1)

        self.setCentralWidget(central)

        sb = QStatusBar()
        self._status_label = QLabel("")
        sb.addWidget(self._status_label, stretch=1)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(240)
        self._progress_bar.setVisible(False)
        sb.addPermanentWidget(self._progress_bar)
        self.setStatusBar(sb)

    # ---- drag and drop -------------------------------------------------
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

    # ---- import flow ---------------------------------------------------
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
        self._progress_bar.setRange(0, 0)  # indeterminate until count is known
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
        msg = (
            f"Import complete.\n\n"
            f"Added: {result.messages_added:,} messages, "
            f"{result.attachments_added:,} attachments.\n"
            f"Skipped (already in DB): {result.messages_skipped:,}.\n"
            f"Conversation windows built: {result.windows_built:,}."
        )
        QMessageBox.information(self, "Import complete", msg)

    def _on_import_failed(self, err: str) -> None:
        self._progress_bar.setVisible(False)
        self._worker = None
        QMessageBox.critical(self, "Import failed", err)
        self._refresh_status()

    # ---- status bar ----------------------------------------------------
    def _refresh_status(self) -> None:
        total = self._model.total_in_db()
        self._empty_hint.setVisible(total == 0)
        self._list.setVisible(total > 0)
        if total == 0:
            self._status_label.setText("Database empty. Import a DCE JSON to begin.")
            return
        shown = min(total, MAX_ROWS)
        self._status_label.setText(
            f"{total:,} messages in DB   ·   showing newest {shown:,}   ·   {database_path()}"
        )

    # ---- about ---------------------------------------------------------
    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            f"About {__app_name__}",
            f"<b>{__app_name__}</b> v{__version__}<br><br>"
            "Desktop study tool for the Bookmap Discord<br>"
            "<code>traders-lab-tom-b</code> channel.<br><br>"
            f"Database: <code>{database_path()}</code>",
        )

    # ---- teardown ------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        self._conn.close()
        super().closeEvent(event)
