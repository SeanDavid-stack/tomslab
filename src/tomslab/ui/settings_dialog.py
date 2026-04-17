"""Settings dialog — AI Providers tab.

Phase 3 only wires the AI Providers tab (pick which provider does what,
paste API keys, test connection).  Other tabs (Ingestion / Featured
Speaker / Advanced / About) arrive in later phases.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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

from tomslab import db as dbmod, secret_store
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

        outer.addStretch(1)
        return w

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
        for name in ("ollama", "gemini"):
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
        # api key
        secret_store.store_api_key(self._conn, "gemini", self._gem_key.text().strip())
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
