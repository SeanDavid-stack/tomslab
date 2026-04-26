"""Help → Check for Updates… dialog + background worker.

The dialog is intentionally plain: current/latest versions, release
date, notes, a Download button that opens the release URL in the user's
browser, and a checkbox to disable auto-checks. Everything honours the
no-auto-install policy — we only ever point the user at the release
page.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from tomslab.ui.browser_open import open_browser

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from tomslab import __version__, updates


class UpdateCheckThread(QThread):
    """Runs ``updates.check_for_update`` on a worker thread so the UI
    stays responsive during the 5-second GitHub timeout."""

    finished_with_info = pyqtSignal(object)  # emits UpdateInfo | None

    def __init__(self, conn: sqlite3.Connection, parent=None) -> None:
        super().__init__(parent)
        self._conn = conn

    def run(self) -> None:  # type: ignore[override]
        info = None
        try:
            info = updates.check_for_update(self._conn)
        except Exception:
            # Network module is already silent; belt-and-braces in case
            # a downstream exception slips through.
            info = None
        self.finished_with_info.emit(info)


class UpdateDialog(QDialog):
    """Show the result of an update check.

    * ``info`` may be ``None`` — that means the check hit an error or
      we've never been able to reach the manifest. We just say "could
      not check" instead of pretending we're up to date.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        info: Optional[updates.UpdateInfo],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._info = info
        self.setWindowTitle("Check for Updates")
        self.resize(520, 380)

        layout = QVBoxLayout(self)

        header = QLabel(self._header_html())
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setHtml(self._notes_html())
        layout.addWidget(notes, stretch=1)

        # Download row — only meaningful when there's a newer version.
        row = QHBoxLayout()
        self._download_btn = QPushButton("Download update")
        self._download_btn.setDefault(True)
        self._download_btn.clicked.connect(self._on_download)
        if not (info and info.is_newer and info.url):
            self._download_btn.setEnabled(False)
        row.addWidget(self._download_btn)
        row.addStretch(1)
        layout.addLayout(row)

        # Auto-check toggle.
        self._auto = QCheckBox("Check for updates automatically (weekly)")
        self._auto.setChecked(updates.get_auto_check_enabled(conn))
        self._auto.toggled.connect(
            lambda v: updates.set_auto_check_enabled(self._conn, v)
        )
        layout.addWidget(self._auto)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _header_html(self) -> str:
        info = self._info
        if info is None:
            return (
                f"<b>Tom's Lab v{__version__}</b><br>"
                "<span style='color:#949BA4;'>"
                "Couldn't reach the update server. Try again later — "
                "this is a free utility, so there's no support line."
                "</span>"
            )
        if info.is_newer:
            return (
                f"<b>Update available: v{info.latest_version}</b><br>"
                f"<span style='color:#949BA4;'>You're on v{info.current_version}"
                f"{'  ·  released ' + info.released if info.released else ''}."
                "</span>"
            )
        return (
            f"<b>You're on the latest version (v{info.current_version}).</b>"
            "<br><span style='color:#949BA4;'>Nothing to do.</span>"
        )

    def _notes_html(self) -> str:
        info = self._info
        if info is None or not info.notes:
            return (
                "<p style='color:#949BA4;'>No release notes available.</p>"
            )
        # Escape by letting QTextBrowser render as plain-ish HTML; the
        # manifest is authored by us, not the user, so we don't need
        # hardening here, but we still wrap in <pre> so line breaks from
        # "notes" survive.
        safe = (info.notes
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
        return f"<pre style='white-space:pre-wrap; font-family:inherit;'>{safe}</pre>"

    def _on_download(self) -> None:
        info = self._info
        if not (info and info.url):
            return
        try:
            open_browser(info.url)
        except Exception as e:
            QMessageBox.warning(
                self, "Couldn't open browser",
                f"Open this URL manually:\n\n{info.url}\n\n({e})",
            )
