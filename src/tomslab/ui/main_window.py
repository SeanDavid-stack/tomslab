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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tomslab import __app_name__, __version__
from tomslab import db as dbmod
from tomslab import embed_service, image_embed_service, semantic, visual
from tomslab.ingest.importer import ImportResult
from tomslab.paths import database_path
from tomslab.search import SearchMode
from tomslab import bookmarks as bmmod
from tomslab.ui.bookmarks_view import BookmarksView
from tomslab.ui.chat_view import ChatView
from tomslab.ui.concept_bar import ConceptChipBar
from tomslab.ui.detail_dialog import DetailDialog
from tomslab.ui.embed_worker import EmbedWorker
from tomslab.ui.gallery_view import GalleryView, ROLE_PATH as GALLERY_ROLE_PATH
from tomslab.ui.image_embed_worker import ImageEmbedWorker
from tomslab.ui.image_viewer import ImageViewerDialog
from tomslab.ui.import_worker import ImportWorker
from tomslab.ui.message_delegate import MessageDelegate
from tomslab.ui.message_model import MAX_BROWSE_ROWS, MessageListModel, ROLE_MESSAGE
from tomslab.ui.settings_dialog import SettingsDialog


# cap the image cache so 10K messages worth of charts don't eat all RAM
QPixmapCache.setCacheLimit(256 * 1024)  # 256 MB


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt_ym(iso_ts: str) -> str:
    """"2022-01-20T11:45:50-05:00" -> "Jan 2022". Forgiving on malformed input."""
    try:
        y = int(iso_ts[:4])
        m = int(iso_ts[5:7])
        return f"{_MONTHS[m - 1]} {y}"
    except Exception:
        return iso_ts[:7] if iso_ts else ""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.resize(1200, 820)
        self.setAcceptDrops(True)
        self._apply_dark_palette()

        self._conn = dbmod.connect()
        dbmod.initialise(self._conn)

        # Bookmarks schema (idempotent, fast)
        bmmod.ensure_schema(self._conn)

        self._model = MessageListModel(self._conn, self)
        self._delegate = MessageDelegate(self)
        self._delegate.thumbnail_clicked.connect(self._open_image_viewer)
        self._delegate.bookmark_toggled.connect(self._on_bookmark_toggled)
        self._delegate.set_bookmarks(bmmod.all_message_ids(self._conn))
        self._worker: ImportWorker | None = None
        self._embed_worker: EmbedWorker | None = None
        self._image_embed_worker: ImageEmbedWorker | None = None
        self._image_viewer: ImageViewerDialog | None = None
        self._detail_dialog: DetailDialog | None = None

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

        self._build_embed_action = QAction("&Build text embeddings…", self)
        self._build_embed_action.triggered.connect(self._on_build_embeddings)
        file_menu.addAction(self._build_embed_action)

        self._build_image_embed_action = QAction("Build &image (CLIP) embeddings…", self)
        self._build_image_embed_action.triggered.connect(self._on_build_image_embeddings)
        file_menu.addAction(self._build_image_embed_action)

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

        disclaimer_action = QAction("&Disclaimer / Legal", self)
        disclaimer_action.triggered.connect(self._show_disclaimer)
        help_menu.addAction(disclaimer_action)

    # ------------------------------------------------------------------
    # main widgets
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- channel header band ---------------------------------------
        header = QWidget()
        header.setStyleSheet(
            "QWidget { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #1E1F22, stop:1 #2B2D31); border-bottom: 1px solid #3F4147; }"
        )
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 10, 16, 10)
        badge = QLabel("📒")
        badge.setStyleSheet("font-size: 20px;")
        title = QLabel("<b>Traders Lab</b> — <span style='color:#949BA4;'>traders-lab-tom-b</span><br>"
                       "<span style='color:#949BA4; font-size:11px;'>"
                       "Bookmap Discord · Tom B's channel · reference PDFs live in the pinned section</span>")
        title.setStyleSheet("color: #F2F3F5; font-size: 14px;")
        hlay.addWidget(badge)
        hlay.addSpacing(12)
        hlay.addWidget(title, stretch=1)

        self._header_counts = QLabel("")
        self._header_counts.setStyleSheet(
            "color: #949BA4; font-size: 11px; padding: 4px 10px;"
            " background: #1E1F22; border-radius: 12px;"
        )
        hlay.addWidget(self._header_counts)

        # Feed noise filter toggle — on by default, kills one-word replies,
        # emoji-only messages, "lol"/"ok"/etc. from the browse feed.
        from PyQt6.QtWidgets import QPushButton
        self._noise_toggle = QPushButton()
        self._noise_toggle.setCheckable(True)
        self._noise_toggle.setChecked(self._model.hide_noise())
        self._noise_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._noise_toggle.setToolTip(
            "Hide reactions and one-word replies from the feed.\n"
            "Tom's messages and messages with charts are always kept."
        )
        self._noise_toggle.setStyleSheet(
            "QPushButton { background: #1E1F22; color: #DBDEE1;"
            " font-size: 11px; padding: 5px 11px;"
            " border: 1px solid #3F4147; border-radius: 12px; }"
            "QPushButton:checked { background: #3A3320; color: #FFC857;"
            " border: 1px solid #FFC857; }"
            "QPushButton:hover { color: white; }"
        )
        self._noise_toggle.clicked.connect(self._on_noise_toggle)
        hlay.addSpacing(6)
        hlay.addWidget(self._noise_toggle)
        self._update_noise_toggle_label()

        outer.addWidget(header)

        # Body layout resumes with its own margins
        body = QWidget()
        body_outer = QVBoxLayout(body)
        body_outer.setContentsMargins(10, 8, 10, 6)
        body_outer.setSpacing(6)
        outer.addWidget(body, stretch=1)
        outer = body_outer   # keep the rest of the function intact

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
        self._mode_combo.addItem("Visual", userData="visual")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._mode_combo.setStyleSheet(
            "QComboBox { background: #1E1F22; color: #DBDEE1;"
            " padding: 6px 10px; border: 1px solid #3F4147; border-radius: 6px; }"
        )
        bar.addWidget(self._mode_combo)
        outer.addLayout(bar)

        # --- concept chips (Tom's glossary) ----------------------------
        self._concept_bar = ConceptChipBar(self._conn, self)
        self._concept_bar.concept_clicked.connect(self._on_concept_clicked)
        outer.addWidget(self._concept_bar)

        # --- empty state hint ------------------------------------------
        self._empty_hint = QLabel(
            "No messages yet. Drag a DCE JSON file here, or File → Import DCE JSON (Ctrl+I)."
        )
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color: #949BA4; padding: 40px;")
        outer.addWidget(self._empty_hint)

        # --- feed tab --------------------------------------------------
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

        # --- gallery tab ------------------------------------------------
        self._gallery = GalleryView(self._conn, self)
        # Double-click or Enter on a gallery thumbnail opens the image
        # viewer instead of jumping to the message — the user is in
        # "look at charts" mode when they're in the Gallery tab.
        self._gallery.image_opened.connect(self._open_image_viewer)

        # --- ask tab ----------------------------------------------------
        self._chat = ChatView(self._conn, self)
        self._chat.citation_clicked.connect(self._on_citation)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane { border: 0; }"
            "QTabBar::tab { background: #2B2D31; color: #DBDEE1; padding: 6px 14px; border: none; }"
            "QTabBar::tab:selected { background: #5865F2; color: white; }"
        )
        # --- bookmarks tab ---------------------------------------------
        self._bookmarks = BookmarksView(self._conn, self)
        self._bookmarks.message_activated.connect(self._jump_to_message)
        self._bookmarks.citation_clicked.connect(self._on_citation)

        self._tabs.addTab(self._list, "Feed")
        self._tabs.addTab(self._gallery, "Gallery")
        self._tabs.addTab(self._chat, "Ask Tom")
        self._tabs.addTab(self._bookmarks, "Bookmarks")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs, stretch=1)

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

        # --- keep the Gallery in lockstep with the Feed's search ---------
        #   * Visual mode: CLIP-based text-to-image search (gallery does its own).
        #   * Keyword / Semantic: scope gallery to charts attached to the same
        #     messages the Feed is showing.
        #   * No query: gallery shows nothing.
        if mode == SearchMode.VISUAL:
            self._gallery.set_message_scope(None)
            self._gallery.set_query(query)
        elif query:
            self._gallery.set_query("")   # no CLIP query in these modes
            # Pull the matching message ids out of the model's row cache.
            ids: list[str] = []
            for row in range(self._model.rowCount()):
                r = self._model.data(self._model.index(row, 0), ROLE_MESSAGE)
                if r is not None and not r.doc_meta:
                    ids.append(r.id)
            # If we've only loaded the first page, top up from the raw search ids.
            raw = getattr(self._model, "_search_ids", [])
            for rid in raw:
                if rid and rid.startswith("msg:"):
                    mid = rid[4:]
                    if mid not in ids:
                        ids.append(mid)
            self._gallery.set_message_scope(ids)
        else:
            self._gallery.set_query("")
            self._gallery.set_message_scope(None)

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
                "Text embeddings not built",
                "Semantic search needs text embeddings.\n\n"
                "File → Build text embeddings… creates them. "
                "With Ollama's nomic-embed-text on a 3080-class GPU this takes ~5 min "
                "for the full Bookmap corpus.",
            )
            self._revert_mode_to("keyword")
            return
        if mode == SearchMode.VISUAL and not visual.available(self._conn):
            QMessageBox.information(
                self,
                "Image embeddings not built",
                "Visual search needs CLIP image embeddings.\n\n"
                "File → Build image (CLIP) embeddings… creates them. "
                "With ViT-B-32 on a 3080 Ti this takes ~5 min for 10K charts.",
            )
            self._revert_mode_to("keyword")
            return
        # Automatically flip to Gallery tab for visual queries — it's the natural view.
        if mode == SearchMode.VISUAL:
            self._tabs.setCurrentIndex(1)
        if self._search.text().strip():
            self._apply_search()

    def _revert_mode_to(self, mode_key: str) -> None:
        idx = self._mode_combo.findData(mode_key)
        if idx >= 0:
            self._mode_combo.blockSignals(True)
            self._mode_combo.setCurrentIndex(idx)
            self._mode_combo.blockSignals(False)

    def _on_noise_toggle(self, checked: bool) -> None:
        self._model.set_hide_noise(checked)
        self._update_noise_toggle_label()
        self._refresh_status()

    def _update_noise_toggle_label(self) -> None:
        if self._noise_toggle.isChecked():
            self._noise_toggle.setText("🔇 Hiding reactions")
        else:
            self._noise_toggle.setText("🔊 Showing everything")

    def _on_concept_clicked(self, term: str) -> None:
        """Glossary chip click routes based on the active tab.

          * Feed / Gallery: set the search bar to the term (both tabs follow).
          * Ask Tom: drop the term in the chat composer so the user can ask
            a question about it without leaving the chat.
        """
        current = self._tabs.currentIndex()
        if current == 2:   # Ask Tom
            # Don't clobber if the composer already has an in-progress draft.
            existing = self._chat._input.toPlainText().strip()
            if existing:
                self._chat._input.setPlainText(f"{existing} {term}".strip())
            else:
                self._chat._input.setPlainText(f"What is {term}?")
            self._chat._input.setFocus()
            return

        # Feed / Gallery — flip to Keyword mode and run the search. Both
        # tabs share the search bar so whichever you were on stays active.
        self._mode_combo.blockSignals(True)
        idx = self._mode_combo.findData("keyword")
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.blockSignals(False)
        self._search.setText(term)
        self._apply_search()

    def _on_tab_changed(self, idx: int) -> None:
        # When user switches to Gallery manually, make sure it reflects current query.
        if idx == 1:
            mode_str = self._mode_combo.currentData() or "keyword"
            if mode_str == "visual":
                self._gallery.set_query(self._search.text().strip())
            else:
                self._gallery.set_query("")
        elif idx == 3:   # Bookmarks tab
            self._bookmarks.reload()
        self._refresh_status()

    # ------------------------------------------------------------------
    # bookmarks
    # ------------------------------------------------------------------
    def _on_bookmark_toggled(self, message_id: str, now_on: bool) -> None:
        bmmod.toggle_message(self._conn, message_id)
        self._delegate.set_bookmarks(bmmod.all_message_ids(self._conn))
        # Repaint only the affected area.
        self._list.viewport().update()
        # If the Bookmarks tab is currently visible, refresh it.
        if self._tabs.currentIndex() == 3:
            self._bookmarks.reload()

    # ------------------------------------------------------------------
    # gallery → feed jump
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # image viewer
    # ------------------------------------------------------------------
    def _open_image_viewer(self, path: str) -> None:
        """Reuse a single ImageViewerDialog — prevents double-clicks
        from spawning multiple Windows. Debounced against identical
        rapid re-opens of the same file."""
        if not path:
            return
        if self._image_viewer is None:
            self._image_viewer = ImageViewerDialog(self)
        self._image_viewer.show_image(path)

    def _on_citation(self, kind: str, raw_id: str) -> None:
        """Ask-Tom citation click → open a popover showing the content.
        From there the user can hit 'Show in timeline' if they want the
        surrounding feed context — otherwise they stay on the Ask Tom tab.
        """
        if self._detail_dialog is None:
            self._detail_dialog = DetailDialog(self._conn, self)
            self._detail_dialog.jump_to_message.connect(self._jump_to_message)
            self._detail_dialog.open_image.connect(self._open_image_viewer)

        if kind == "msg":
            self._detail_dialog.show_message(raw_id)
        elif kind == "doc":
            try:
                page_id = int(raw_id)
            except ValueError:
                return
            self._detail_dialog.show_doc_page(page_id)

    def _jump_to_message(self, message_id: str) -> None:
        # Clear search & switch to Feed, then try to scroll to the message.
        self._search.blockSignals(True)
        self._search.setText("")
        self._search.blockSignals(False)
        self._revert_mode_to("keyword")
        self._model.set_query("", mode=SearchMode.KEYWORD)
        self._tabs.setCurrentIndex(0)

        # Scan loaded pages for the message; fetch more if needed.
        target = self._find_row(message_id, load_pages=40)
        if target is not None:
            self._list.scrollTo(
                self._model.index(target, 0),
                hint=QListView.ScrollHint.PositionAtCenter,
            )
            self._list.setCurrentIndex(self._model.index(target, 0))
            # Pulse the target gold for ~2s so the user sees where they landed.
            self._delegate.flash_message(message_id)
            self._list.viewport().update()
            # Schedule a repaint when the flash window elapses to clear it.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2300, self._clear_flash)
        else:
            # Fall back to a modal dialog with the message's content.
            self._show_message_detail(message_id)

    def _clear_flash(self) -> None:
        self._delegate.clear_flash()
        self._list.viewport().update()

    def _find_row(self, message_id: str, load_pages: int) -> int | None:
        for _ in range(max(1, load_pages)):
            for row in range(self._model.rowCount()):
                msg = self._model.data(self._model.index(row, 0), ROLE_MESSAGE)
                if msg is not None and getattr(msg, "id", None) == message_id:
                    return row
            if not self._model.canFetchMore(self._model.index(-1, -1)):
                break
            self._model.fetchMore(self._model.index(-1, -1))
        return None

    def _show_message_detail(self, message_id: str) -> None:
        row = self._conn.execute(
            """
            SELECT m.author_nickname, m.author_name, m.timestamp, m.content
              FROM messages m WHERE m.id = ?
            """,
            (message_id,),
        ).fetchone()
        if not row:
            QMessageBox.information(self, "Not found", "Couldn't locate that message.")
            return
        who = row["author_nickname"] or row["author_name"] or "?"
        ts = (row["timestamp"] or "")[:19].replace("T", " ")
        QMessageBox.information(
            self,
            f"{who} · {ts}",
            (row["content"] or "").strip() or "(no text)",
        )

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
        self._gallery.reload()
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
        pending_windows = embed_service.pending_count(self._conn)
        pending_docs = embed_service.pending_doc_pages_count(self._conn)
        total_pending = pending_windows + pending_docs
        if total_pending == 0:
            QMessageBox.information(
                self,
                "Nothing to embed",
                "All conversation windows and doc pages are already embedded.",
            )
            return

        desc_lines = []
        if pending_windows:
            desc_lines.append(f"  • {pending_windows:,} conversation windows")
        if pending_docs:
            desc_lines.append(f"  • {pending_docs:,} PDF doc pages")
        desc = "\n".join(desc_lines)

        reply = QMessageBox.question(
            self,
            "Build text embeddings",
            f"Create embeddings for:\n{desc}\n\n"
            "This enables Semantic search (Discord + Tom's reference PDFs merged). "
            "Uses the embedding provider configured in Settings → AI Providers. "
            "Runs in the background; you can keep browsing.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText("Embedding…")

        self._embed_worker = EmbedWorker(scope="both", parent=self)
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
        semantic.invalidate_doc_cache()
        self._refresh_status()
        QMessageBox.information(
            self, "Embeddings done", f"Embedded {n:,} new windows / doc pages."
        )

    def _on_embed_failed(self, err: str) -> None:
        self._progress_bar.setVisible(False)
        self._embed_worker = None
        QMessageBox.critical(self, "Embedding failed", err)
        self._refresh_status()

    # ---- image (CLIP) embeddings --------------------------------------
    def _on_build_image_embeddings(self) -> None:
        if self._image_embed_worker is not None and self._image_embed_worker.isRunning():
            QMessageBox.information(
                self, "Already running", "Image embedding already in progress."
            )
            return
        # Probe how many are pending using the currently-selected model tag.
        clip_name = dbmod.get_setting(self._conn, "clip_model", "ViT-B-32") or "ViT-B-32"
        clip_pre = dbmod.get_setting(self._conn, "clip_pretrained", "openai") or "openai"
        pending = visual.pending_count(self._conn, f"{clip_name}:{clip_pre}")
        if pending == 0:
            QMessageBox.information(
                self, "Nothing to embed", "All attachments are already CLIP-embedded."
            )
            return
        reply = QMessageBox.question(
            self,
            "Build image embeddings",
            f"Create CLIP ({clip_name}/{clip_pre}) embeddings for {pending:,} charts?\n\n"
            "Takes roughly 5 minutes on a 3080-class GPU. Runs in the background.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText("CLIP-embedding images…")

        self._image_embed_worker = ImageEmbedWorker(self)
        self._image_embed_worker.progress.connect(self._on_image_embed_progress)
        self._image_embed_worker.finished_ok.connect(self._on_image_embed_finished)
        self._image_embed_worker.failed.connect(self._on_image_embed_failed)
        self._image_embed_worker.start()

    def _on_image_embed_progress(self, done: int, total: int, status: str) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(done)
            self._status_label.setText(f"Image embeddings: {status}")
        else:
            self._status_label.setText(f"Image embeddings: {status}")

    def _on_image_embed_finished(self, n: int) -> None:
        self._progress_bar.setVisible(False)
        self._image_embed_worker = None
        visual.invalidate_cache()
        self._gallery.reload()
        self._refresh_status()
        QMessageBox.information(
            self, "Image embeddings done", f"Embedded {n:,} new charts."
        )

    def _on_image_embed_failed(self, err: str) -> None:
        self._progress_bar.setVisible(False)
        self._image_embed_worker = None
        QMessageBox.critical(self, "Image embedding failed", err)
        self._refresh_status()

    # ------------------------------------------------------------------
    # status bar
    # ------------------------------------------------------------------
    def _refresh_status(self) -> None:
        total = self._model.total_in_db()
        query = self._model.current_query()
        self._empty_hint.setVisible(total == 0)
        self._tabs.setVisible(total > 0)
        # Header stat pill: message count + nice date range.
        if total:
            row = self._conn.execute(
                "SELECT MIN(timestamp) AS a, MAX(timestamp) AS b FROM messages"
            ).fetchone()
            date_range = ""
            if row and row["a"] and row["b"]:
                date_range = f"   ·   {_fmt_ym(row['a'])} → {_fmt_ym(row['b'])}"
            self._header_counts.setText(f"{total:,} messages{date_range}")
        else:
            self._header_counts.setText("no messages")
        if total == 0:
            self._status_label.setText("Database empty. Import a DCE JSON to begin.")
            return

        # Gather embedding-pending notes
        pending_text = embed_service.pending_count(self._conn)
        pending_doc = embed_service.pending_doc_pages_count(self._conn)
        clip_name = dbmod.get_setting(self._conn, "clip_model", "ViT-B-32") or "ViT-B-32"
        clip_pre = dbmod.get_setting(self._conn, "clip_pretrained", "openai") or "openai"
        pending_img = visual.pending_count(self._conn, f"{clip_name}:{clip_pre}")
        notes: list[str] = []
        # Hide tiny residuals — a single corrupt PNG will sit as "1 chart
        # pending" forever because preprocess rejects it on every retry.
        # Only surface when the backlog is actually meaningful.
        if pending_text > 5:
            notes.append(f"{pending_text:,} windows need text embeddings")
        if pending_doc > 5:
            notes.append(f"{pending_doc:,} doc pages need text embeddings")
        if pending_img > 5:
            notes.append(f"{pending_img:,} charts need CLIP embeddings")
        embed_note = "   ·   " + "; ".join(notes) if notes else ""

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

    def _show_disclaimer(self) -> None:
        QMessageBox.information(
            self,
            "Disclaimer & Legal",
            "<h3>Experimental research tool</h3>"
            "<p><b>Tom's Lab is not a trading platform, broker, or advisor.</b></p>"
            "<p>Everything this app produces — Ask Tom answers, chart analyses, "
            "citations, similar-chart suggestions, "
            "entry/stop/target ideas — is <b>experimental output from AI models "
            "operating on publicly-shared Discord messages and reference "
            "documents</b>. It is <b>NOT financial advice</b>, NOT a trade "
            "recommendation, and NOT a substitute for your own analysis, due "
            "diligence, or the advice of a licensed professional.</p>"
            "<p><b>You alone are responsible for your trading decisions and for "
            "any gains or losses that result from them.</b></p>"
            "<ul>"
            "<li>Vet every citation against the original source before "
            "acting on it. Models can misread charts, mis-cite messages, "
            "and invent plausible-sounding detail.</li>"
            "<li>Tom B has not reviewed, endorsed, or approved this app or "
            "its outputs. His posted content is used here as educational "
            "reference material, not as personalised recommendations.</li>"
            "<li>Trading futures, equities, and other instruments carries "
            "substantial risk of loss. Past performance is not indicative "
            "of future results.</li>"
            "<li>No warranty is made as to the accuracy, completeness, or "
            "fitness for purpose of anything this app outputs. Use at your "
            "own risk.</li>"
            "</ul>"
            "<p>By continuing to use Tom's Lab you agree that you understand "
            "and accept the above.</p>"
        )

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        # Close must mean close — a lingering QThread keeps the whole
        # Python process alive invisibly. Stop each worker cleanly, then
        # fall back to terminate() if it refuses to exit in time.
        for worker in (self._worker, self._embed_worker, self._image_embed_worker):
            if worker is not None and worker.isRunning():
                worker.quit()
                worker.wait(1500)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(500)
        # ChatView has its own worker
        try:
            self._chat.shutdown()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass
        super().closeEvent(event)
        # Belt and suspenders: tell Qt to fully quit after the window is gone.
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().quit()
