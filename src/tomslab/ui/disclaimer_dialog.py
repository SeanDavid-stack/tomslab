"""First-launch click-to-agree gate.

Used once on first launch to present the full Disclaimer & Legal text
with a hard requirement that the user scroll to the bottom before the
'I agree' button enables. Click-wrap enforceability improves when the
user can be shown to have had the full text in front of them, not just
a preview.

After acceptance, the main window records the flag and the same text is
still viewable via Help → Disclaimer / Legal (the non-gated review path)
and Help → Privacy Policy.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)


_ACCEPT_TEXT = "I have read and accept these terms"
_DECLINE_TEXT = "Decline and exit"
_SCROLL_HINT = "↓ Please scroll to the bottom to enable the accept button."
_SCROLL_DONE = "You have reviewed the full terms."


class DisclaimerGateDialog(QDialog):
    """Scrollable disclaimer review with a scroll-to-enable accept button.

    - Fixed reasonable size (fits a 1200×820 default window comfortably)
    - QTextBrowser for HTML rendering + native scroll bar
    - 'I agree' starts disabled; enables only after verticalScrollBar
      reaches its max (user genuinely scrolled through)
    - If the text is short enough to fit without scrolling, the accept
      button is enabled immediately (no fake gate)
    - 'Decline and exit' is always enabled
    """

    def __init__(self, html: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tom's Lab — Required: review & accept terms")
        self.setModal(True)
        # Come up above other apps the first time the app launches.
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        # Fixed footprint so the dialog doesn't overflow small monitors.
        # Tall enough to show ~1 full section at a time.
        self.setFixedSize(720, 620)
        self._accepted = False
        self._build_ui(html)

    # ------------------------------------------------------------------
    def _build_ui(self, html: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        heading = QLabel(
            "<h3>Before you use Tom's Lab</h3>"
            "<p style='color:#b00020;'><b>Required reading.</b> "
            "Scroll to the bottom to review the full terms, then click "
            "<b>I have read and accept these terms</b> to continue. "
            "If you decline, the app will close.</p>"
        )
        heading.setWordWrap(True)
        heading.setTextFormat(Qt.TextFormat.RichText)
        heading.setStyleSheet("color: #DBDEE1; font-size: 12px;")
        outer.addWidget(heading)

        self._text = QTextBrowser()
        self._text.setOpenExternalLinks(True)
        self._text.setHtml(html)
        self._text.setStyleSheet(
            "QTextBrowser {"
            " background: #1E1F22; color: #DBDEE1;"
            " border: 1px solid #3F4147; border-radius: 6px;"
            " padding: 10px 14px; font-size: 12px; }"
        )
        outer.addWidget(self._text, stretch=1)

        self._scroll_hint = QLabel(_SCROLL_HINT)
        self._scroll_hint.setStyleSheet(
            "color: #FFC857; font-size: 11px; padding: 2px 4px;"
        )
        outer.addWidget(self._scroll_hint)

        btn_row = QHBoxLayout()
        self._decline_btn = QPushButton(_DECLINE_TEXT)
        self._decline_btn.clicked.connect(self.reject)
        self._decline_btn.setStyleSheet(self._btn_style())
        btn_row.addWidget(self._decline_btn)
        btn_row.addStretch(1)
        self._accept_btn = QPushButton(_ACCEPT_TEXT)
        self._accept_btn.setEnabled(False)
        self._accept_btn.setStyleSheet(self._btn_style(primary=True))
        self._accept_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._accept_btn)
        outer.addLayout(btn_row)

        # Wire the scroll-to-bottom detector. Also check on show in case
        # the rendered text already fits without scrolling (e.g. on a
        # huge monitor) — we shouldn't block the user behind a fake gate
        # when the full text is visible at first paint.
        self._text.verticalScrollBar().valueChanged.connect(
            self._check_scroll_position
        )
        self._text.document().contentsChanged.connect(
            self._check_scroll_position
        )

    def _btn_style(self, *, primary: bool = False) -> str:
        if primary:
            return (
                "QPushButton { background: #FFC857; color: #1E1F22;"
                " padding: 10px 20px; border: none; border-radius: 6px;"
                " font-weight: 600; font-size: 12px; }"
                "QPushButton:hover:enabled { background: #FFD87A; }"
                "QPushButton:disabled { background: #3F4147; color: #6b6e74; }"
            )
        return (
            "QPushButton { background: transparent; color: #949BA4;"
            " padding: 10px 16px; border: 1px solid #3F4147;"
            " border-radius: 6px; font-size: 12px; }"
            "QPushButton:hover { color: #DBDEE1; border-color: #DBDEE1; }"
        )

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:   # type: ignore[override]
        super().showEvent(event)
        # Defer the fit check until after layout settles — the
        # scrollbar maximum is 0 before the QTextBrowser paints.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self._check_scroll_position)
        self.raise_()
        self.activateWindow()

    def _check_scroll_position(self, *_ignored) -> None:
        """If the user has reached (or is within a few pixels of) the
        end of the scroll range — or the text fits without scrolling
        at all — enable the accept button."""
        bar = self._text.verticalScrollBar()
        at_bottom = (bar.maximum() == 0) or (bar.value() >= bar.maximum() - 4)
        self._accept_btn.setEnabled(at_bottom)
        self._scroll_hint.setText(_SCROLL_DONE if at_bottom else _SCROLL_HINT)

    def _on_accept(self) -> None:
        self._accepted = True
        self.accept()

    # ------------------------------------------------------------------
    def accepted(self) -> bool:
        """True if the user clicked the 'I agree' button specifically.
        Distinguishes from dialog.accept() via keyboard Esc etc."""
        return self._accepted
