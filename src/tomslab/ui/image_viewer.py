"""Full-size chart viewer.

A plain, non-modal dialog with a scroll area holding the image. Opens
fit-to-window by default; the user can resize the dialog (image rescales)
or press Ctrl+= / Ctrl+- / Ctrl+0 to zoom / reset. Escape closes. The
same dialog is reused across thumbnail clicks so double-click noise can't
spawn multiple windows.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent, QPixmap, QShortcut, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)


class ImageViewerDialog(QDialog):
    """Reusable viewer — call :meth:`show_image(path)` to (re)use it.

    It's a single instance kept on the parent; repeated clicks swap the
    pixmap instead of spawning a new window.
    """

    _ZOOM_STEP = 1.25
    _MIN_ZOOM = 0.1
    _MAX_ZOOM = 8.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Non-modal: user can keep scrolling the feed while the viewer is up.
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1000, 720)

        self._pixmap: QPixmap | None = None
        self._path: Path | None = None
        self._zoom: float = 1.0
        self._fit_to_window: bool = True

        self._build_ui()
        self._install_shortcuts()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # toolbar
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 6, 10, 6)
        self._caption = QLabel("")
        self._caption.setStyleSheet("color: #DBDEE1;")
        bar.addWidget(self._caption, stretch=1)

        zoom_out = QPushButton("−")
        zoom_out.setFixedWidth(30)
        zoom_out.clicked.connect(lambda: self._zoom_by(1 / self._ZOOM_STEP))
        self._zoom_label = QLabel("Fit")
        self._zoom_label.setStyleSheet("color: #949BA4; min-width: 52px;")
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(30)
        zoom_in.clicked.connect(lambda: self._zoom_by(self._ZOOM_STEP))
        reset = QPushButton("Fit")
        reset.setFixedWidth(46)
        reset.clicked.connect(self._fit)
        open_ext = QPushButton("Open in system viewer")
        open_ext.clicked.connect(self._open_external)
        for b in (zoom_out, self._zoom_label, zoom_in, reset, open_ext):
            bar.addWidget(b)

        toolbar_wrap = QLabel()
        toolbar_wrap.setLayout(bar)
        toolbar_wrap.setStyleSheet(
            "QLabel { background: #2B2D31; border-bottom: 1px solid #3F4147; }"
            "QPushButton { background: transparent; color: #DBDEE1;"
            "  padding: 4px 10px; border: 1px solid #3F4147; border-radius: 6px; }"
            "QPushButton:hover { background: #1E1F22; color: white; }"
        )
        outer.addWidget(toolbar_wrap)

        # image area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setStyleSheet("QScrollArea { background: #111214; border: none; }")

        self._image = QLabel()
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._scroll.setWidget(self._image)
        outer.addWidget(self._scroll, stretch=1)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+="), self, self._zoom_in_shortcut)
        QShortcut(QKeySequence("Ctrl++"), self, self._zoom_in_shortcut)
        QShortcut(QKeySequence("Ctrl+-"), self, self._zoom_out_shortcut)
        QShortcut(QKeySequence("Ctrl+0"), self, self._fit)

    def _zoom_in_shortcut(self) -> None:
        self._zoom_by(self._ZOOM_STEP)

    def _zoom_out_shortcut(self) -> None:
        self._zoom_by(1 / self._ZOOM_STEP)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def show_image(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        pix = QPixmap(str(p))
        if pix.isNull():
            return False

        self._pixmap = pix
        self._path = p
        self.setWindowTitle(p.name)
        self._caption.setText(f"{p.name}   ·   {pix.width()}×{pix.height()}")
        self._fit_to_window = True
        self._zoom = 1.0
        self._apply_pixmap()

        if not self.isVisible():
            self.show()
        else:
            self.raise_()
            self.activateWindow()
        return True

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _apply_pixmap(self) -> None:
        if self._pixmap is None:
            return
        if self._fit_to_window:
            avail = self._scroll.viewport().size()
            scaled = self._pixmap.scaled(
                avail,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image.setPixmap(scaled)
            self._image.resize(scaled.size())
            self._zoom_label.setText("Fit")
        else:
            w = int(self._pixmap.width() * self._zoom)
            h = int(self._pixmap.height() * self._zoom)
            scaled = self._pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._image.setPixmap(scaled)
            self._image.resize(scaled.size())
            self._zoom_label.setText(f"{int(self._zoom * 100)}%")

    def _zoom_by(self, factor: float) -> None:
        if self._pixmap is None:
            return
        if self._fit_to_window:
            # treat "Fit" as baseline, then zoom from there
            avail = self._scroll.viewport().size()
            if self._pixmap.width() > 0:
                base = min(
                    avail.width() / self._pixmap.width(),
                    avail.height() / self._pixmap.height(),
                    1.0,
                )
            else:
                base = 1.0
            self._zoom = max(base, 0.1)
            self._fit_to_window = False
        self._zoom = max(self._MIN_ZOOM, min(self._MAX_ZOOM, self._zoom * factor))
        self._apply_pixmap()

    def _fit(self) -> None:
        self._fit_to_window = True
        self._zoom = 1.0
        self._apply_pixmap()

    def _open_external(self) -> None:
        if self._path is None:
            return
        try:
            if sys.platform == "win32":
                import os
                os.startfile(str(self._path))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self._path)])
            else:
                subprocess.Popen(["xdg-open", str(self._path)])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # qt overrides
    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_to_window:
            self._apply_pixmap()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        """Mouse-wheel zoom. Wheel up zooms in, wheel down zooms out.
        Shift+wheel leaves scrolling alone (pan horizontally in the
        scroll area's native behavior). No modifier required — this is
        an image viewer, not a doc reader, so zoom is the natural
        default."""
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            super().wheelEvent(event)
            return
        self._zoom_by(self._ZOOM_STEP if delta > 0 else 1 / self._ZOOM_STEP)
        event.accept()
