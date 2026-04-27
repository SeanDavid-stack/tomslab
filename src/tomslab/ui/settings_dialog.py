"""Settings dialog — AI Providers tab.

Phase 3 only wires the AI Providers tab (pick which provider does what,
paste API keys, test connection).  Other tabs (Ingestion / Featured
Speaker / Advanced / About) arrive in later phases.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tomslab import db as dbmod, secret_store, updates as updatesmod
from tomslab.ai import registry
from tomslab.ai.base import ProviderError, ProviderUnavailable


class SettingsDialog(QDialog):
    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle("Settings")
        self.resize(620, 480)

        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_ai_tab(), "AI Providers")
        tabs.addTab(self._build_transcription_tab(), "Transcription")
        tabs.addTab(self._build_updates_tab(), "Updates")
        tabs.addTab(self._build_about_tab(), "About")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # AI tab
    # ------------------------------------------------------------------
    def _build_ai_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        # --- Role → provider mapping ------------------------------------
        mapping = QGroupBox("Which provider does which task")
        mapform = QFormLayout(mapping)

        self._combo_embed = self._make_provider_combo(
            dbmod.get_setting(self._conn, "ai_provider_embed", "ollama") or "ollama"
        )
        self._combo_chat = self._make_provider_combo(
            dbmod.get_setting(self._conn, "ai_provider_chat", "gemini") or "gemini"
        )
        self._combo_vision = self._make_provider_combo(
            dbmod.get_setting(self._conn, "ai_provider_vision", "ollama") or "ollama"
        )
        mapform.addRow("Embeddings:", self._combo_embed)
        mapform.addRow("Chat (Ask Tom):", self._combo_chat)
        mapform.addRow("Vision (chart descriptions):", self._combo_vision)
        outer.addWidget(mapping)

        # --- Gemini ------------------------------------------------------
        gem = QGroupBox("Gemini (cloud)")
        g = QFormLayout(gem)

        self._gem_key = QLineEdit(secret_store.load_api_key(self._conn, "gemini"))
        self._gem_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._gem_key.setPlaceholderText("Paste key from https://aistudio.google.com/app/apikey")
        g.addRow("API key:", self._gem_key)

        self._gem_chat_model = QLineEdit(
            dbmod.get_setting(self._conn, "chat_model_gemini", "gemini-2.5-flash") or ""
        )
        g.addRow("Chat model:", self._gem_chat_model)

        self._gem_embed_model = QLineEdit(
            dbmod.get_setting(self._conn, "embed_model_gemini", "gemini-embedding-001") or ""
        )
        g.addRow("Embed model:", self._gem_embed_model)

        gem_buttons = QHBoxLayout()
        test_gem_chat = QPushButton("Test chat")
        test_gem_chat.clicked.connect(lambda: self._test_provider("gemini", "chat"))
        test_gem_embed = QPushButton("Test embed")
        test_gem_embed.clicked.connect(lambda: self._test_provider("gemini", "embed"))
        gem_buttons.addWidget(test_gem_chat)
        gem_buttons.addWidget(test_gem_embed)
        gem_buttons.addStretch(1)
        g.addRow("", self._wrap(gem_buttons))
        outer.addWidget(gem)

        # --- Ollama ------------------------------------------------------
        oll = QGroupBox("Ollama (local)")
        o = QFormLayout(oll)

        self._oll_embed_model = QLineEdit(
            dbmod.get_setting(self._conn, "embed_model_ollama", "nomic-embed-text") or ""
        )
        o.addRow("Embed model:", self._oll_embed_model)

        self._oll_chat_model = QLineEdit(
            dbmod.get_setting(self._conn, "chat_model_ollama", "llama3.1:8b") or ""
        )
        o.addRow("Chat model:", self._oll_chat_model)

        self._oll_vision_model = QLineEdit(
            dbmod.get_setting(self._conn, "vision_model_ollama", "llava:13b") or ""
        )
        o.addRow("Vision model:", self._oll_vision_model)

        oll_buttons = QHBoxLayout()
        test_oll = QPushButton("Test connection")
        test_oll.clicked.connect(lambda: self._test_provider("ollama", "embed"))
        oll_buttons.addWidget(test_oll)
        oll_buttons.addStretch(1)
        o.addRow("", self._wrap(oll_buttons))
        outer.addWidget(oll)

        # --- Groq -------------------------------------------------------
        # Chat-only. Free tier is generous (~14,400 req/day) and inference
        # is much faster than Gemini Flash, but the open models Groq hosts
        # follow citation instructions less reliably than Gemini 2.5 Flash.
        # Position as alternative for power users, not as the default.
        groq = QGroupBox("Groq (cloud, chat-only — fast, generous free tier)")
        gq = QFormLayout(groq)

        self._groq_key = QLineEdit(secret_store.load_api_key(self._conn, "groq"))
        self._groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._groq_key.setPlaceholderText("Paste key from https://console.groq.com/keys")
        gq.addRow("API key:", self._groq_key)

        from tomslab.ai.groq import DEFAULT_CHAT_MODEL as _GROQ_DEFAULT
        self._groq_chat_model = QLineEdit(
            dbmod.get_setting(self._conn, "chat_model_groq", _GROQ_DEFAULT) or ""
        )
        self._groq_chat_model.setPlaceholderText(_GROQ_DEFAULT)
        gq.addRow("Chat model:", self._groq_chat_model)

        groq_buttons = QHBoxLayout()
        test_groq_chat = QPushButton("Test chat")
        test_groq_chat.clicked.connect(lambda: self._test_provider("groq", "chat"))
        groq_buttons.addWidget(test_groq_chat)
        groq_buttons.addStretch(1)
        gq.addRow("", self._wrap(groq_buttons))

        groq_hint = QLabel(
            "Groq does not offer embeddings or vision — use it for the "
            "Chat role only. Open-model citations are less reliable than "
            "Gemini's, so verify Ask Tom's links if you switch."
        )
        groq_hint.setWordWrap(True)
        groq_hint.setStyleSheet("color: #949BA4; font-size: 11px;")
        gq.addRow("", groq_hint)

        outer.addWidget(groq)

        outer.addStretch(1)
        return w

    def _build_transcription_tab(self) -> QWidget:
        """Whisper model picker — speed vs accuracy trade-off.

        Model change takes effect on the next 'Import from folder' or
        'Import YouTube' run; the in-flight transcription keeps using
        whatever was loaded when it started.
        """
        w = QWidget()
        outer = QVBoxLayout(w)

        box = QGroupBox("Whisper model (speed vs accuracy)")
        form = QFormLayout(box)

        current = dbmod.get_setting(
            self._conn, "whisper_model", "distil-large-v3"
        ) or "distil-large-v3"

        self._whisper_combo = QComboBox()
        # (display name, setting value, description)
        choices = [
            ("distil-large-v3  ~2× faster, near-identical quality (recommended)",
             "distil-large-v3"),
            ("large-v3  slowest, highest accuracy on jargon",
             "large-v3"),
            ("medium.en  ~3× faster than large-v3, slight jargon loss",
             "medium.en"),
            ("small.en  ~5× faster, noticeable accuracy dip",
             "small.en"),
            ("base.en  ~15× faster, rough — drafts only",
             "base.en"),
        ]
        for label, value in choices:
            self._whisper_combo.addItem(label, userData=value)
        idx = self._whisper_combo.findData(current)
        if idx >= 0:
            self._whisper_combo.setCurrentIndex(idx)
        form.addRow("Model:", self._whisper_combo)

        hint = QLabel(
            "Changes apply to the next transcription run. "
            "The currently-running job keeps its model. Relaunching "
            "Tom's Lab picks up the new setting immediately."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #949BA4; font-size: 11px; padding-top: 4px;")
        form.addRow("", hint)

        outer.addWidget(box)
        outer.addStretch(1)
        return w

    def _build_updates_tab(self) -> QWidget:
        """Updates tab — auto-check toggle, manifest URL, manual check.

        The manifest URL is user-editable so the PM can repoint at a
        different repo later without a code change.
        """
        w = QWidget()
        outer = QVBoxLayout(w)

        box = QGroupBox("Update checks")
        form = QFormLayout(box)

        self._updates_auto = QCheckBox("Check for updates automatically (weekly)")
        self._updates_auto.setChecked(updatesmod.get_auto_check_enabled(self._conn))
        form.addRow("", self._updates_auto)

        self._updates_url = QLineEdit(updatesmod.get_manifest_url(self._conn))
        self._updates_url.setPlaceholderText(updatesmod.DEFAULT_MANIFEST_URL)
        form.addRow("Manifest URL:", self._updates_url)

        row = QHBoxLayout()
        self._updates_check_now = QPushButton("Check now")
        self._updates_check_now.clicked.connect(self._on_check_updates_now)
        row.addWidget(self._updates_check_now)
        row.addStretch(1)
        form.addRow("", self._wrap(row))

        hint = QLabel(
            "Free utility — no auto-install, no support line. "
            "If an update is available you'll see a toast and the Help "
            "menu will badge it; the download is a manual install."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #949BA4; font-size: 11px;")
        form.addRow("", hint)

        outer.addWidget(box)
        outer.addStretch(1)
        return w

    def _on_check_updates_now(self) -> None:
        # Persist any in-flight edits to the URL first so "Check now"
        # uses what the user just typed, not the last-saved value.
        updatesmod.set_manifest_url(self._conn, self._updates_url.text().strip())
        updatesmod.set_auto_check_enabled(self._conn, self._updates_auto.isChecked())
        from tomslab.ui.update_dialog import UpdateDialog
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = updatesmod.check_for_update(self._conn)
        finally:
            QApplication.restoreOverrideCursor()
        if info is not None and info.is_newer:
            updatesmod.mark_version_notified(self._conn, info.latest_version)
        UpdateDialog(self._conn, info, parent=self).exec()

    def _build_about_tab(self) -> QWidget:
        from tomslab import __app_name__, __version__
        from tomslab.paths import database_path

        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(
            QLabel(
                f"<b>{__app_name__}</b> v{__version__}<br><br>"
                "Desktop study tool for the Bookmap Discord.<br>"
                f"Database: <code>{database_path()}</code>"
            )
        )
        v.addStretch(1)
        return w

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _make_provider_combo(self, current: str) -> QComboBox:
        c = QComboBox()
        for name in ("ollama", "gemini", "groq"):
            c.addItem(name, userData=name)
        idx = c.findData(current)
        if idx >= 0:
            c.setCurrentIndex(idx)
        return c

    def _wrap(self, layout) -> QWidget:
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _save_and_close(self) -> None:
        # provider mapping
        dbmod.set_setting(self._conn, "ai_provider_embed", self._combo_embed.currentData())
        dbmod.set_setting(self._conn, "ai_provider_chat", self._combo_chat.currentData())
        dbmod.set_setting(self._conn, "ai_provider_vision", self._combo_vision.currentData())
        # models
        dbmod.set_setting(self._conn, "embed_model_ollama", self._oll_embed_model.text().strip())
        dbmod.set_setting(self._conn, "chat_model_ollama", self._oll_chat_model.text().strip())
        dbmod.set_setting(self._conn, "vision_model_ollama", self._oll_vision_model.text().strip())
        dbmod.set_setting(self._conn, "embed_model_gemini", self._gem_embed_model.text().strip())
        dbmod.set_setting(self._conn, "chat_model_gemini", self._gem_chat_model.text().strip())
        dbmod.set_setting(self._conn, "chat_model_groq", self._groq_chat_model.text().strip())
        # api keys
        secret_store.store_api_key(self._conn, "gemini", self._gem_key.text().strip())
        secret_store.store_api_key(self._conn, "groq", self._groq_key.text().strip())
        # whisper model
        dbmod.set_setting(
            self._conn, "whisper_model", self._whisper_combo.currentData()
        )
        # updates
        updatesmod.set_manifest_url(self._conn, self._updates_url.text().strip())
        updatesmod.set_auto_check_enabled(self._conn, self._updates_auto.isChecked())
        registry.reset_cache()
        self.accept()

    def _test_provider(self, name: str, role: str) -> None:
        # persist whatever's currently in the fields so the test uses fresh values
        if name == "gemini":
            secret_store.store_api_key(self._conn, "gemini", self._gem_key.text().strip())
            dbmod.set_setting(self._conn, "chat_model_gemini", self._gem_chat_model.text().strip())
            dbmod.set_setting(self._conn, "embed_model_gemini", self._gem_embed_model.text().strip())
        elif name == "ollama":
            dbmod.set_setting(self._conn, "embed_model_ollama", self._oll_embed_model.text().strip())
            dbmod.set_setting(self._conn, "chat_model_ollama", self._oll_chat_model.text().strip())
            dbmod.set_setting(self._conn, "vision_model_ollama", self._oll_vision_model.text().strip())
        elif name == "groq":
            secret_store.store_api_key(self._conn, "groq", self._groq_key.text().strip())
            dbmod.set_setting(self._conn, "chat_model_groq", self._groq_chat_model.text().strip())
        registry.reset_cache()

        try:
            prov = registry.build_provider(self._conn, name, role)
            status = prov.ping()
        except ProviderUnavailable as e:
            QMessageBox.warning(self, f"{name} unavailable", str(e))
            return
        except ProviderError as e:
            QMessageBox.warning(self, f"{name} error", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, f"{name} failed", f"{type(e).__name__}: {e}")
            return
        QMessageBox.information(self, f"{name} OK", status)
