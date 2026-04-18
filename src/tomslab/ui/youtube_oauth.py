"""One-time Google OAuth sign-in flow for the TomTube downloader.

pytubefix's underlying OAuth is Google's "device" flow:

  1. App asks Google for a device code. Google returns a short user code
     + a verification URL.
  2. App shows the URL + code to the user. User visits the URL in a
     browser, enters the code, and approves access.
  3. App polls Google's token endpoint; once the user approves, Google
     returns a long-lived refresh token that pytubefix caches.
  4. All future TomTube downloads load the cached token silently.

This module wraps steps (1)-(3) in a QThread so the polling doesn't
freeze the UI, and shows a Qt dialog with the URL + code during step (2).
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from tomslab.ingest.youtube import youtube_token_path


class YouTubeOAuthWorker(QThread):
    """Runs pytubefix's OAuth device flow in a background thread.

    Emits ``verify_needed(url, code)`` when the user must go visit the
    verification URL. The main thread shows a dialog and calls
    :meth:`confirm_signed_in` once the user clicks "I've signed in" — that
    releases the internal event so the worker can proceed with polling.
    """

    verify_needed = pyqtSignal(str, str)   # (verification_url, user_code)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proceed_event = threading.Event()

    def confirm_signed_in(self) -> None:
        """Called from the main thread when the user clicks "I've signed in"
        so that pytubefix begins polling Google for the token."""
        self._proceed_event.set()

    def _verifier(self, verification_url: str, user_code: str) -> None:
        # Called by pytubefix on the background thread. We signal the main
        # thread to surface the dialog, then block until the user confirms.
        self.verify_needed.emit(verification_url, user_code)
        self._proceed_event.wait()

    def run(self) -> None:
        try:
            from pytubefix import YouTube
            # Any valid video URL triggers the token fetch. Rick-roll is the
            # internet's canonical always-available public video.
            url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            token_file = str(youtube_token_path())
            youtube_token_path().parent.mkdir(parents=True, exist_ok=True)
            yt = YouTube(
                url,
                use_oauth=True,
                allow_oauth_cache=True,
                token_file=token_file,
                oauth_verifier=self._verifier,
            )
            # Force a metadata hit so the OAuth flow actually runs.
            _ = yt.title
            self.finished_ok.emit()
        except Exception as exc:   # pragma: no cover - network / user cancel
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class YouTubeOAuthDialog(QDialog):
    """Modal dialog: shows the Google device URL + code, walks the user
    through sign-in, and waits for them to confirm before the worker
    begins polling."""

    def __init__(self, worker: YouTubeOAuthWorker, parent=None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._verification_url: str | None = None
        self.setWindowTitle("Sign in to YouTube (one-time)")
        self.setModal(True)
        self.resize(540, 300)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        intro = QLabel(
            "TomTube needs to sign in to YouTube once so it can download "
            "Tom's videos without being blocked by YouTube's bot protection. "
            "Your Google account is used only to fetch videos — nothing is "
            "uploaded or posted."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)

        step1 = QLabel("<b>Step 1.</b> Open this page in your browser:")
        lay.addWidget(step1)

        self._url_field = QLineEdit()
        self._url_field.setReadOnly(True)
        self._url_field.setText("(fetching…)")
        lay.addWidget(self._url_field)

        open_row = QHBoxLayout()
        self._open_btn = QPushButton("Open in browser")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_browser)
        open_row.addWidget(self._open_btn)
        open_row.addStretch(1)
        lay.addLayout(open_row)

        step2 = QLabel("<b>Step 2.</b> Enter this code:")
        lay.addWidget(step2)

        self._code_field = QLineEdit()
        self._code_field.setReadOnly(True)
        self._code_field.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_field.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 20px; "
            "letter-spacing: 3px; padding: 8px;"
        )
        lay.addWidget(self._code_field)

        copy_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy code")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_code)
        copy_row.addWidget(self._copy_btn)
        copy_row.addStretch(1)
        lay.addLayout(copy_row)

        step3 = QLabel(
            "<b>Step 3.</b> Approve access. Then come back and click "
            "below — the app finishes sign-in automatically."
        )
        step3.setWordWrap(True)
        lay.addWidget(step3)

        btn_row = QHBoxLayout()
        self._signed_in_btn = QPushButton("I've signed in — finish")
        self._signed_in_btn.setEnabled(False)
        self._signed_in_btn.setDefault(True)
        self._signed_in_btn.clicked.connect(self._on_signed_in)
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addWidget(self._signed_in_btn)
        lay.addLayout(btn_row)

        self._worker.verify_needed.connect(self._on_verify_needed)

    def _on_verify_needed(self, url: str, code: str) -> None:
        self._verification_url = url
        self._url_field.setText(url)
        self._code_field.setText(code)
        self._open_btn.setEnabled(True)
        self._copy_btn.setEnabled(True)
        self._signed_in_btn.setEnabled(True)

    def _on_open_browser(self) -> None:
        if self._verification_url:
            QDesktopServices.openUrl(QUrl(self._verification_url))

    def _on_copy_code(self) -> None:
        QApplication.clipboard().setText(self._code_field.text())

    def _on_signed_in(self) -> None:
        self._signed_in_btn.setEnabled(False)
        self._signed_in_btn.setText("Finishing…")
        self._worker.confirm_signed_in()


def run_oauth_flow(parent=None) -> bool:
    """Kick off the whole one-time sign-in flow. Blocks until the user has
    either finished sign-in or cancelled. Returns True on success."""
    worker = YouTubeOAuthWorker(parent)
    dlg = YouTubeOAuthDialog(worker, parent)

    result = {"ok": False, "error": ""}

    def _ok():
        result["ok"] = True
        dlg.accept()

    def _fail(msg: str):
        result["error"] = msg
        dlg.reject()

    worker.finished_ok.connect(_ok)
    worker.failed.connect(_fail)
    worker.start()

    dlg.exec()
    # If the dialog was cancelled before sign-in, release the worker so it
    # can clean up quickly.
    worker.confirm_signed_in()
    worker.wait(2000)

    if not result["ok"] and result["error"]:
        QMessageBox.warning(
            parent,
            "Sign-in failed",
            f"YouTube sign-in didn't complete:\n\n{result['error']}",
        )
    return result["ok"]
