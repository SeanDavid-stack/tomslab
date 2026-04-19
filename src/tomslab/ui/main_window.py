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
    QApplication,
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
from tomslab.ui.docs_view import DocsView
from tomslab.ui.embed_worker import EmbedWorker
from tomslab.ui.gallery_view import GalleryView, ROLE_PATH as GALLERY_ROLE_PATH
from tomslab.ui.image_embed_worker import ImageEmbedWorker
from tomslab.ui.image_viewer import ImageViewerDialog
from tomslab.ui.import_worker import ImportWorker
from tomslab.ui.message_delegate import MessageDelegate
from tomslab.ui.message_model import MAX_BROWSE_ROWS, MessageListModel, ROLE_MESSAGE
from tomslab.ui.settings_dialog import SettingsDialog
from tomslab.ui.video_view import TomTubeView
from tomslab.ui.video_worker import VideoIngestWorker


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
        self._video_worker: VideoIngestWorker | None = None

        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self._apply_search)

        self._build_menu()
        self._build_ui()
        self._refresh_status()

        # First-run: show the Getting Started & Policy dialog once so
        # new users see the expectation-setting + ToS-responsibility
        # text before they start using the ingest features. Gated by a
        # settings key so it doesn't nag on subsequent launches.
        if dbmod.get_setting(self._conn, "first_run_policy_shown", "") != "yes":
            QTimer.singleShot(300, self._show_first_run_policy)

    def _show_first_run_policy(self) -> None:
        self._show_getting_started()
        dbmod.set_setting(self._conn, "first_run_policy_shown", "yes")

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

        self._import_folder_action = QAction(
            "Import videos from &folder… (recommended)", self
        )
        self._import_folder_action.triggered.connect(self._on_import_video_folder)
        file_menu.addAction(self._import_folder_action)

        self._signin_youtube_action = QAction("Check TomTube &direct-download setup…", self)
        self._signin_youtube_action.triggered.connect(self._on_signin_youtube)
        file_menu.addAction(self._signin_youtube_action)

        self._ingest_youtube_action = QAction(
            "Import &YouTube directly (experimental)…", self
        )
        self._ingest_youtube_action.triggered.connect(self._on_ingest_youtube)
        file_menu.addAction(self._ingest_youtube_action)

        self._check_youtube_action = QAction("Check for &new Tom videos", self)
        self._check_youtube_action.triggered.connect(self._on_check_new_youtube)
        file_menu.addAction(self._check_youtube_action)

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
        getting_started_action = QAction("&Getting Started && Policy", self)
        getting_started_action.triggered.connect(self._show_getting_started)
        help_menu.addAction(getting_started_action)

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
        self._concept_bar.evolution_requested.connect(self._on_evolution_requested)
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

        # --- docs tab --------------------------------------------------
        self._docs = DocsView(self._conn, self)
        self._docs.page_opened.connect(self._open_image_viewer)

        # --- tomtube tab ----------------------------------------------
        self._tomtube = TomTubeView(self._conn, self)

        # Tab order — most-used surfaces first. Ask Tom is where people
        # actually go; Gallery is the fast visual scan; Feed is raw-read
        # when you need a specific conversation; Docs is reference; Bookmarks
        # is where saved stuff lives.
        self._tabs.addTab(self._chat, "Ask Tom")
        self._tabs.addTab(self._gallery, "Gallery")
        self._tabs.addTab(self._list, "Feed")
        self._tabs.addTab(self._docs, "Docs")
        self._tabs.addTab(self._tomtube, "TomTube")
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
            self._tabs.setCurrentWidget(self._gallery)
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

    def _on_evolution_requested(self, term: str) -> None:
        """Right-click on a concept chip → dedicated timeline dialog
        showing how Tom's framing of that term has shifted across
        quarters of Discord + TomTube transcripts."""
        from tomslab.ui.evolution_dialog import EvolutionDialog
        dlg = EvolutionDialog(
            self._conn,
            term,
            on_citation_clicked=self._jump_to_message_citation,
            parent=self,
        )
        dlg.exec()

    def _jump_to_message_citation(self, kind: str, raw: str) -> None:
        """Shared handler used by both the chat view's citation pills and
        the evolution dialog — opens Tom's Lab's detail dialog for
        msg/doc citations. Video citations are handled inline in the
        evolution dialog itself."""
        if kind not in ("msg", "doc"):
            return
        try:
            from tomslab.ui.detail_dialog import DetailDialog
        except ImportError:
            return
        if self._detail_dialog is None:
            self._detail_dialog = DetailDialog(self._conn, self)
        if kind == "msg":
            self._detail_dialog.show_message(raw)
        else:
            try:
                self._detail_dialog.show_doc_page(int(raw))
            except (ValueError, AttributeError):
                return

    def _on_concept_clicked(self, term: str) -> None:
        """Glossary chip click routes based on the active tab.

          * Feed / Gallery: set the search bar to the term (both tabs follow).
          * Ask Tom: drop the term in the chat composer so the user can ask
            a question about it without leaving the chat.
        """
        if self._tabs.currentWidget() is self._chat:
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

    def _on_tab_changed(self, _idx: int) -> None:
        cur = self._tabs.currentWidget()
        if cur is self._gallery:
            mode_str = self._mode_combo.currentData() or "keyword"
            if mode_str == "visual":
                self._gallery.set_query(self._search.text().strip())
            else:
                self._gallery.set_query("")
        elif cur is self._docs:
            self._docs.reload()
        elif cur is self._tomtube:
            self._tomtube.reload()
        elif cur is self._bookmarks:
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
        if self._tabs.currentWidget() is self._bookmarks:
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
        """Switch to Feed and scroll to a specific Discord message.

        Uses an O(1) SQL rank lookup rather than iterating loaded pages,
        so jumping to a message from 2021 is as fast as jumping to
        yesterday's. Loads pages up to that rank lazily and scrolls.
        """
        from PyQt6.QtCore import QModelIndex, QTimer

        self._search.blockSignals(True)
        self._search.setText("")
        self._search.blockSignals(False)
        self._revert_mode_to("keyword")
        self._model.set_query("", mode=SearchMode.KEYWORD)
        self._tabs.setCurrentWidget(self._list)

        try:
            rank = self._rank_of_message(message_id)
        except Exception as exc:
            log.warning("rank lookup failed for %s: %s", message_id, exc)
            rank = None

        if rank is None:
            # Message isn't in the DB, or the noise filter hid it, or
            # rank is past the 10K browse cap. Fall back to the popover.
            self._show_message_detail(message_id)
            return

        # Lazily load model pages until we've covered the target rank or
        # we run out of data. Bounded to prevent runaway fetches.
        root = QModelIndex()
        max_iterations = 60    # at 200 rows/page that's 12K rows = the full cap
        while (
            self._model.rowCount() <= rank
            and self._model.canFetchMore(root)
            and max_iterations > 0
        ):
            self._model.fetchMore(root)
            max_iterations -= 1

        if rank >= self._model.rowCount():
            self._show_message_detail(message_id)
            return

        idx = self._model.index(rank, 0)
        if not idx.isValid():
            self._show_message_detail(message_id)
            return

        self._list.scrollTo(idx, hint=QListView.ScrollHint.PositionAtCenter)
        self._list.setCurrentIndex(idx)
        self._delegate.flash_message(message_id)
        self._list.viewport().update()
        QTimer.singleShot(2300, self._clear_flash)

    def _rank_of_message(self, message_id: str) -> int | None:
        """Return the 0-indexed row position of ``message_id`` under the
        current browse ORDER BY (timestamp DESC, id DESC). Honours the
        noise filter setting so the rank matches what's actually on screen.
        """
        row = self._conn.execute(
            "SELECT timestamp FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return None
        ts = row["timestamp"] or ""
        # Count messages that come BEFORE this one in the ordering.
        if self._model.hide_noise():
            from tomslab.ui.message_model import NOISE_FILTER_SQL
            where = (
                "(timestamp > ? OR (timestamp = ? AND id > ?))"
                f" AND {NOISE_FILTER_SQL}"
            )
        else:
            where = "(timestamp > ? OR (timestamp = ? AND id > ?))"
        count = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM messages m WHERE {where}",
            (ts, ts, message_id),
        ).fetchone()
        return int(count["n"] or 0)

    def _clear_flash(self) -> None:
        self._delegate.clear_flash()
        self._list.viewport().update()

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
        pending_videos = embed_service.pending_video_chunks_count(self._conn)
        total_pending = pending_windows + pending_docs + pending_videos
        if total_pending == 0:
            QMessageBox.information(
                self,
                "Nothing to embed",
                "All conversation windows, doc pages, and video chunks are "
                "already embedded.",
            )
            return

        desc_lines = []
        if pending_windows:
            desc_lines.append(f"  • {pending_windows:,} conversation windows")
        if pending_docs:
            desc_lines.append(f"  • {pending_docs:,} PDF doc pages")
        if pending_videos:
            desc_lines.append(f"  • {pending_videos:,} video transcript chunks")
        desc = "\n".join(desc_lines)

        reply = QMessageBox.question(
            self,
            "Build text embeddings",
            f"Create embeddings for:\n{desc}\n\n"
            "This enables Semantic search (Discord + Tom's reference PDFs + "
            "TomTube transcripts merged). Uses the embedding provider "
            "configured in Settings → AI Providers. Runs in the background; "
            "you can keep browsing.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText("Embedding…")

        self._embed_worker = EmbedWorker(scope="all", parent=self)
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
        semantic.invalidate_video_cache()
        self._refresh_status()
        QMessageBox.information(
            self, "Embeddings done",
            f"Embedded {n:,} new windows / doc pages / video chunks.",
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

    # ---- YouTube / TomTube ingest -------------------------------------
    def _on_check_new_youtube(self) -> None:
        """Fast 'what's new?' check — just enumerate the channel, tell the
        user how many new Tom videos exist, and offer to ingest only
        those. No download / no transcribe until the user says OK."""
        if self._video_worker is not None and self._video_worker.isRunning():
            QMessageBox.information(self, "Already running",
                                    "A YouTube ingest is already in progress.")
            return
        self._status_label.setText("Checking YouTube for new Tom videos…")
        QApplication.processEvents()
        try:
            from tomslab.ingest.youtube import (
                find_new_videos, upsert_video_rows,
            )
            title_filter = dbmod.get_setting(
                self._conn, "youtube_title_filter", "tom b"
            ) or "tom b"
            new, existing = find_new_videos(self._conn, title_filter=title_filter)
        except Exception as exc:
            QMessageBox.critical(self, "Check failed", f"{type(exc).__name__}: {exc}")
            self._refresh_status()
            return

        if not new:
            QMessageBox.information(
                self,
                "You're up to date",
                f"Checked the channel — {len(existing)} Tom video(s) already "
                f"indexed, no new matches.",
            )
            self._refresh_status()
            return

        titles_preview = "\n".join(
            f"  · {e.title[:80]}" + ("…" if len(e.title) > 80 else "")
            for e in new[:8]
        )
        if len(new) > 8:
            titles_preview += f"\n  … and {len(new) - 8} more"
        reply = QMessageBox.question(
            self,
            "New Tom videos found",
            f"<b>{len(new)} new Tom B video(s)</b> not yet indexed:"
            f"<pre style='font-size:11px;'>{titles_preview}</pre>"
            f"<p>Download + transcribe now? Each video runs ~10 min on your GPU "
            f"and the pipeline is resumable.</p>",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            self._refresh_status()
            return

        if not self._ensure_youtube_signed_in():
            self._refresh_status()
            return
        # Commit the new rows as 'pending' and kick off the worker — it will
        # only process pending/failed rows, so the full re-enumeration that
        # `_on_ingest_youtube` does isn't needed.
        upsert_video_rows(self._conn, new)
        self._on_ingest_youtube_start(f"{len(new)} new video(s)")

    def _on_signin_youtube(self) -> None:
        """Run the TomTube direct-download setup check + show a verbose
        dialog explaining the fragility."""
        import shutil
        from pathlib import Path
        from tomslab.paths import data_dir

        node_ok = shutil.which("node") is not None
        bgutil_locations = [
            Path.home() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
            Path(r"C:\Users\seane\bgutil-ytdlp-pot-provider\server\build\generate_once.js"),
            data_dir() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "generate_once.js",
        ]
        bgutil_ok = any(p.is_file() for p in bgutil_locations)

        def _row(name: str, ok: bool, detail: str = "") -> str:
            icon = "✅" if ok else "❌"
            tail = f" — <i>{detail}</i>" if detail else ""
            return f"<li>{icon} <b>{name}</b>{tail}</li>"

        body = (
            "<h3>TomTube direct-download setup</h3>"
            "<ul>"
            f"{_row('Node.js on PATH', node_ok, 'solves YouTube JS challenge')}"
            f"{_row('bgutil PO-token script', bgutil_ok, 'mints proof-of-origin tokens')}"
            "<li>❓ <b>Firefox signed into YouTube</b> — "
            "<i>we can't check this without a network round-trip. "
            "Open Firefox and confirm your avatar shows on youtube.com.</i></li>"
            "</ul>"
            "<p style='color:#b00020;'><b>⚠ This feature is fragile.</b> "
            "YouTube actively fights third-party downloaders. The pipeline "
            "relies on external tools (yt-dlp, Node.js, bgutil) that may "
            "stop working any day when YouTube changes their anti-bot "
            "system. I may or may not fix it promptly. If you need new "
            "videos indexed and the in-app downloader is broken, download "
            "them with whatever tool works at that moment (JDownloader, "
            "4K Video Downloader, browser extensions) into a folder, then "
            "use <b>File → Import videos from folder…</b> — that path is "
            "library-agnostic and doesn't break.</p>"
        )
        QMessageBox.information(self, "TomTube setup", body)

    def _ensure_youtube_signed_in(self) -> bool:
        """Gate direct-YouTube ingest on the setup pre-flight. Returns True
        if the caller should proceed."""
        from tomslab.ingest.youtube import is_signed_in
        if is_signed_in():
            return True
        QMessageBox.warning(
            self,
            "TomTube setup incomplete",
            "The direct-YouTube downloader isn't ready. Use "
            "<b>File → Check TomTube direct-download setup…</b> to see "
            "what's missing, or use <b>File → Import videos from folder…</b> "
            "to side-step the YouTube auth problem entirely.",
        )
        return False

    def _on_ingest_youtube(self) -> None:
        if self._video_worker is not None and self._video_worker.isRunning():
            QMessageBox.information(self, "Already running",
                                    "A YouTube ingest is already in progress.")
            return
        if not self._ensure_youtube_signed_in():
            return
        reply = QMessageBox.question(
            self,
            "Import YouTube directly (experimental)",
            "<b>This will:</b>"
            "<ul>"
            "<li>Scrape the Bookmap YouTube channel for videos tagged 'Tom B'</li>"
            "<li>Download each video's audio (.webm / Opus, ~70 MB each)</li>"
            "<li>Transcribe locally with faster-whisper on your GPU (~10× realtime)</li>"
            "<li>Chunk the transcripts and embed them for Ask Tom</li>"
            "</ul>"
            "<p style='color:#b00020;'><b>⚠ Fragile feature — read this:</b> "
            "This downloader fights YouTube's anti-bot system using a chain "
            "of external tools (yt-dlp + Node.js + bgutil + Firefox cookies). "
            "YouTube changes their defenses constantly and the pipeline can "
            "break any day. When it breaks, it may stay broken until the "
            "tooling catches up — which may never happen. "
            "<b>If you need new videos indexed and this feature is down, "
            "download them manually with any working tool (JDownloader, "
            "4K Video Downloader, browser extensions) into a folder, then "
            "use File → Import videos from folder — that path is "
            "library-agnostic and doesn't break.</b></p>"
            "<p><b>Proceed with the direct downloader anyway?</b></p>",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        self._on_ingest_youtube_start("all pending videos")

    def _on_ingest_youtube_start(self, label: str) -> None:
        """Actually start the video worker. Shared by 'Import' (full
        re-enumerate) and 'Check for new' (skip the enumerate, only
        process already-queued pending rows)."""
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText(f"TomTube: starting — {label}…")

        self._video_worker = VideoIngestWorker(
            model_name=dbmod.get_setting(self._conn, "whisper_model", "large-v3"),
            bitrate_kbps=int(dbmod.get_setting(self._conn, "youtube_audio_bitrate", "96") or "96"),
            parent=self,
        )
        self._video_worker.progress.connect(self._on_video_progress)
        self._video_worker.finished_ok.connect(self._on_video_finished)
        self._video_worker.failed.connect(self._on_video_failed)
        self._video_worker.start()

    def _on_video_progress(self, stage: str, current: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._status_label.setText(f"TomTube: {stage}  ({current:,}/{total:,})")
        else:
            self._status_label.setText(f"TomTube: {stage}")

    def _on_video_finished(self, report: object) -> None:
        self._progress_bar.setVisible(False)
        self._video_worker = None
        self._tomtube.reload()
        d = report if isinstance(report, dict) else {}
        QMessageBox.information(
            self,
            "TomTube ingest complete",
            f"Enumerated: {d.get('enumerated', 0)}\n"
            f"Newly added rows: {d.get('newly_added_rows', 0)}\n"
            f"Processed OK: {d.get('processed', 0)}\n"
            f"Failed: {d.get('failed', 0)}",
        )
        self._refresh_status()

    def _on_video_failed(self, err: str) -> None:
        self._progress_bar.setVisible(False)
        self._video_worker = None
        QMessageBox.critical(self, "TomTube ingest failed", err)
        self._refresh_status()

    # ---- Folder-based video import (reliable path) --------------------
    def _on_import_video_folder(self) -> None:
        """User picks a folder of pre-downloaded audio/video files. We
        scan, upsert rows, then transcribe + chunk each one."""
        if self._video_worker is not None and self._video_worker.isRunning():
            QMessageBox.information(self, "Already running",
                                    "A video ingest is already in progress.")
            return

        from PyQt6.QtWidgets import QFileDialog
        last = dbmod.get_setting(self._conn, "video_import_folder", "") or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Select folder with Tom's video files", last,
        )
        if not folder:
            return

        from pathlib import Path
        from tomslab.ingest.youtube import scan_folder_for_videos
        try:
            candidates = scan_folder_for_videos(Path(folder))
        except Exception as exc:
            QMessageBox.critical(self, "Scan failed",
                                 f"{type(exc).__name__}: {exc}")
            return
        if not candidates:
            QMessageBox.warning(
                self, "No video files found",
                f"Didn't find any .mp3/.mp4/.m4a/.webm/etc files in:\n\n{folder}"
                "\n\nTip: download Tom's videos with 4K Video Downloader "
                "(free) and point here at its output folder.",
            )
            return

        with_id = sum(1 for c in candidates if c["has_yt_id"])
        reply = QMessageBox.question(
            self,
            "Import videos from folder",
            f"<b>Found {len(candidates)} media file(s)</b> in:<br>"
            f"<code>{folder}</code><br><br>"
            f"{with_id} have a YouTube id in the filename (citations will "
            f"deep-link to youtube.com with timestamps). "
            f"{len(candidates) - with_id} don't — they'll still be "
            f"searchable but without the YouTube link.<br><br>"
            f"<b>Each file will be transcribed on your GPU (~10× realtime).</b>"
            f" Resumable — close the app any time and it picks up where "
            f"it left off.<br><br>Proceed?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        dbmod.set_setting(self._conn, "video_import_folder", folder)

        from tomslab.ui.video_worker import FolderIngestWorker
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)
        self._status_label.setText("Folder import: starting…")
        self._video_worker = FolderIngestWorker(
            folder=Path(folder),
            model_name=dbmod.get_setting(self._conn, "whisper_model", "large-v3"),
            parent=self,
        )
        self._video_worker.progress.connect(self._on_video_progress)
        self._video_worker.finished_ok.connect(self._on_folder_finished)
        self._video_worker.failed.connect(self._on_video_failed)
        self._video_worker.start()

    def _on_folder_finished(self, report: object) -> None:
        """Folder-specific completion dialog (different keys than channel
        ingest)."""
        self._progress_bar.setVisible(False)
        self._video_worker = None
        self._tomtube.reload()
        d = report if isinstance(report, dict) else {}
        QMessageBox.information(
            self,
            "Folder import complete",
            f"Scanned: {d.get('scanned', 0)}\n"
            f"Newly added rows: {d.get('newly_added_rows', 0)}\n"
            f"Transcribed OK: {d.get('processed', 0)}\n"
            f"Failed: {d.get('failed', 0)}",
        )
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
            f"Database: <code>{database_path()}</code><br><br>"
            "Publisher: <b>SDE-Software (SDES.DEV)</b><br>"
            "<a href='https://sdes.dev'>sdes.dev</a><br>"
            "© 2026 SDE-Software. All rights reserved.",
        )

    def _show_getting_started(self) -> None:
        """Frank expectations-setting dialog: what this is, what it isn't,
        whose responsibility the upkeep + third-party-ToS risk is. Shown
        via Help → Getting Started & Policy, and also auto-presented the
        very first time the app launches (see first_run_check below)."""
        QMessageBox.information(
            self,
            "Getting Started & Policy",
            "<h3>Welcome to Tom's Lab</h3>"
            "<p>A quick read before you dig in — this sets expectations "
            "so nobody's surprised later.</p>"

            "<h4>What this is</h4>"
            "<p>A free, volunteer-built desktop study tool that makes "
            "Tom B's publicly-shared teaching searchable. Built on an "
            "as-needed basis by one person.</p>"

            "<h4>What this isn't</h4>"
            "<p>It is <b>not a commercial product</b>. There is no "
            "customer support queue, no service-level agreement, no "
            "guaranteed roadmap, and no release schedule. Bug fixes and "
            "updates happen at the maintainer's discretion — they may "
            "or may not ever happen. If something stops working, you "
            "may need to wait or fix it yourself.</p>"

            "<h4>Your responsibilities</h4>"
            "<ul>"
            "<li><b>Keep your own corpus current.</b> New Discord "
            "exports, new YouTube videos, new PDFs — you import them "
            "yourself when you want them indexed. The app does not "
            "auto-fetch anything. The ingest workflows are documented "
            "in Help and in the Getting Started guide.</li>"
            "<li><b>Respect third-party Terms of Service.</b> "
            "Bulk-exporting Discord messages and bulk-downloading "
            "YouTube videos may violate those platforms' ToS. This app "
            "provides the ingest mechanisms; <b>whether, how, and "
            "how much you use them is your responsibility</b>, not "
            "the maintainer's.</li>"
            "<li><b>Verify every answer.</b> AI-generated output can "
            "be wrong. This is an experimental research tool, not "
            "financial advice. You alone are responsible for your "
            "trading decisions.</li>"
            "</ul>"

            "<h4>Why this policy</h4>"
            "<p>This is a free utility shared so that people interested "
            "in Tom's framework have a useful tool. The choice is "
            "between sharing it under these constraints or not sharing "
            "it at all. The maintainer is happy to share under the "
            "constraints above.</p>"

            "<h4>About the publisher</h4>"
            "<p>Tom's Lab is developed by "
            "<b>SDE-Software (SDES.DEV)</b> — "
            "<a href='https://sdes.dev'>sdes.dev</a>. "
            "It is a free side project, separate from SDE-Software's "
            "commercial products. No support, walkthroughs, troubleshooting, "
            "or individual assistance is provided by SDE-Software, Bookmap, "
            "Tom B, or the Bookmap Discord channels for this program. "
            "Bugs and glitches will be addressed as they are identified; "
            "no service-level agreement is offered.</p>"

            "<p><i>Thanks for reading — enjoy the app.</i></p>"
        )

    def _show_disclaimer(self) -> None:
        QMessageBox.information(
            self,
            "Disclaimer & Legal",
            "<h3>Independent third-party software</h3>"
            "<p>Tom's Lab is an independent, third-party software application "
            "developed by <b>SDE-Software (SDES.DEV)</b>. It is not "
            "affiliated with, endorsed by, sponsored by, or in any way "
            "connected to Bookmap Ltd., Tom B, the Bookmap Discord channels, "
            "Google, Ollama, or Hugging Face, or any of their parent "
            "companies, subsidiaries, licensors, or affiliated entities. "
            "Bookmap™ is a trademark of Bookmap Ltd. and is referenced here "
            "solely to describe subject-matter context.</p>"

            "<h3>Experimental research tool</h3>"
            "<p><b>Tom's Lab is not a trading platform, broker, or advisor.</b> "
            "Everything this app produces — Ask Tom answers, chart analyses, "
            "citations, similar-chart suggestions, entry / stop / target "
            "ideas — is experimental output from AI models operating on "
            "publicly-shared Discord messages, reference documents, and "
            "YouTube transcripts. It is NOT financial advice, NOT a trade "
            "recommendation, and NOT a substitute for your own analysis, "
            "due diligence, or the advice of a licensed professional.</p>"
            "<p><b>You alone are responsible for your trading decisions and "
            "for any gains or losses that result from them.</b></p>"
            "<ul>"
            "<li>Vet every citation against the original source before "
            "acting on it. Models can misread charts, mis-cite messages, "
            "and invent plausible-sounding detail.</li>"
            "<li>Tom B has not reviewed, endorsed, or approved this app "
            "or its outputs. His posted content is used here as "
            "educational reference material, not as personalised "
            "recommendations.</li>"
            "<li>Trading futures, equities, and other instruments carries "
            "substantial risk of loss. Past performance is not indicative "
            "of future results.</li>"
            "</ul>"

            "<h3>Provided as-is — no support</h3>"
            "<p>Tom's Lab is provided <b>as-is</b> and <b>used entirely at "
            "the user's own risk</b>. No warranty is made as to accuracy, "
            "completeness, or fitness for purpose.</p>"
            "<p><b>No support, walkthroughs, troubleshooting, or individual "
            "assistance is provided by SDE-Software, Bookmap, Tom B, or "
            "the Bookmap Discord channels for this program.</b> Users are "
            "responsible for reading the in-app Getting Started & Policy "
            "dialog, for managing their own corpus (Discord exports, "
            "YouTube videos, PDFs), and for respecting all third-party "
            "Terms of Service when using the ingest features. Bugs and "
            "glitches will be addressed as they are identified; no "
            "service-level agreement is offered.</p>"

            "<h3>Limitation of liability</h3>"
            "<p>THE SOFTWARE IS PROVIDED \"AS IS\" WITHOUT WARRANTY OF ANY "
            "KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO "
            "WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR "
            "PURPOSE, OR NON-INFRINGEMENT.</p>"
            "<p>IN NO EVENT SHALL SDE-SOFTWARE BE LIABLE FOR ANY INDIRECT, "
            "INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR "
            "ANY LOSS OF PROFITS, DATA, OR TRADING LOSSES, ARISING FROM "
            "YOUR USE OF THE SOFTWARE, EVEN IF SDE-SOFTWARE HAS BEEN "
            "ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.</p>"

            "<h3>Third-party services</h3>"
            "<p>Certain features connect to third-party services "
            "(Google Gemini, Ollama, Hugging Face, YouTube, Discord). These "
            "services require your own API keys, accounts, or session "
            "credentials and are governed by their own terms and conditions. "
            "SDE-Software has no control over third-party service "
            "availability, pricing, rate limits, data accuracy, or changes "
            "to their Terms of Service. SDE-Software is not responsible "
            "for any disruption, cost, account suspension, or loss "
            "resulting from third-party service use.</p>"

            "<h3>Attribution</h3>"
            "<p>Publisher: <b>SDE-Software (SDES.DEV)</b> — "
            "<a href='https://sdes.dev'>sdes.dev</a><br>"
            "© 2026 SDE-Software. All rights reserved.</p>"

            "<p>By continuing to use Tom's Lab you agree that you "
            "understand and accept the above.</p>"
        )

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        # Close must mean close — a lingering QThread keeps the whole
        # Python process alive invisibly. Stop each worker cleanly, then
        # fall back to terminate() if it refuses to exit in time.
        for worker in (self._worker, self._embed_worker, self._image_embed_worker,
                       self._video_worker):
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
        QApplication.instance().quit()
