"""Tom's Lab — entry point."""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QSplashScreen

from tomslab import __app_name__, __version__
from tomslab.paths import log_path
from tomslab.ui.main_window import MainWindow


# Minimum time the splash stays on screen even if the main window is
# ready sooner. Tom's "get in the Barcalounger" tagline is the whole
# point of the splash, so it needs a beat to be readable.
SPLASH_MIN_MS = 3_000
# Shrink the loaded splash image to this fraction of its native size.
# 0.70 feels right on 1080p and 1440p — fills the eye without looming.
SPLASH_SCALE = 0.70


def _splash_image_path() -> Path | None:
    """Locate the bundled splash image. In a PyInstaller build the image
    lives under ``sys._MEIPASS``; in a dev run it's alongside the source
    under ``packaging/splash.png``."""
    meipass = getattr(sys, "_MEIPASS", None)
    candidates = []
    if meipass:
        candidates.append(Path(meipass) / "packaging" / "splash.png")
        candidates.append(Path(meipass) / "splash.png")
    # Repo-root relative (dev mode).
    here = Path(__file__).resolve()
    for up in (here.parent, here.parent.parent, here.parent.parent.parent):
        candidates.append(up / "packaging" / "splash.png")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _make_fallback_pixmap(width: int = 900, height: int = 520) -> QPixmap:
    """Drawn placeholder for when no splash.png is bundled. Dark card,
    gold wordmark, Barcalounger tagline — looks intentional, gets out of
    the way the moment a real image is added to ``packaging/splash.png``."""
    pm = QPixmap(width, height)
    pm.fill(QColor("#0b0d12"))
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Subtle border stripe along the bottom.
        p.fillRect(0, height - 4, width, 4, QColor("#c7a447"))

        title = QFont("Segoe UI", 44)
        title.setWeight(QFont.Weight.Bold)
        p.setFont(title)
        p.setPen(QColor("#f1f5f9"))
        p.drawText(0, 0, width, height // 2,
                   Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom,
                   "Tom's Lab")

        tagline = QFont("Segoe UI", 15)
        tagline.setItalic(True)
        p.setFont(tagline)
        p.setPen(QColor("#c7a447"))
        p.drawText(0, height // 2 + 14, width, 40,
                   Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignTop,
                   "Get in the Barcalounger")

        sub = QFont("Segoe UI", 10)
        p.setFont(sub)
        p.setPen(QColor("#64748b"))
        p.drawText(0, height - 52, width, 24,
                   Qt.AlignmentFlag.AlignCenter,
                   f"v{__version__}  ·  SDE Software")
    finally:
        p.end()
    return pm


def _build_splash() -> QSplashScreen:
    """Return a QSplashScreen showing ``packaging/splash.png`` if present,
    otherwise a drawn fallback with the Barcalounger tagline. The
    returned splash is not yet shown — caller decides the timing."""
    img_path = _splash_image_path()
    if img_path is not None:
        pm = QPixmap(str(img_path))
        if pm.isNull():
            pm = _make_fallback_pixmap()
    else:
        pm = _make_fallback_pixmap()

    # Scale down so the splash doesn't dominate the monitor. KeepAspectRatio
    # preserves the artwork's composition; SmoothTransformation gives us a
    # bilinear resample so the downscale doesn't look crunchy.
    if SPLASH_SCALE and SPLASH_SCALE != 1.0:
        new_w = max(1, int(pm.width() * SPLASH_SCALE))
        new_h = max(1, int(pm.height() * SPLASH_SCALE))
        pm = pm.scaled(
            new_w, new_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    splash = QSplashScreen(pm, Qt.WindowType.WindowStaysOnTopHint)
    splash.setMask(pm.mask())
    return splash


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_path(), encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


_LOCK_FILE_NAME = "tomslab.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def _pid_file_path():
    from tomslab.paths import data_dir
    return data_dir() / _LOCK_FILE_NAME


def _check_single_instance() -> int:
    """File-based single-instance check. Returns the PID of a live
    other instance if one exists, otherwise 0 and writes our PID.

    File-based is deliberately simple: no QSharedMemory, no OS
    semaphore — those can orphan on Windows after a crashed process
    and deadlock subsequent launches. A stale PID file is harmless;
    we check if the PID is actually alive before treating it as a
    conflict."""
    try:
        p = _pid_file_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            try:
                prev = int(p.read_text(encoding="utf-8").strip() or 0)
            except Exception:
                prev = 0
            if prev and prev != os.getpid() and _pid_alive(prev):
                return prev
        p.write_text(str(os.getpid()), encoding="utf-8")
        return 0
    except Exception as exc:
        logging.warning("single-instance check failed: %s", exc)
        return 0


def _release_single_instance() -> None:
    """Best-effort: remove our PID file so the next launch doesn't see
    a stale entry. Swallows errors — an orphan PID file is harmless."""
    try:
        p = _pid_file_path()
        if p.exists():
            # Only delete if it's actually ours.
            try:
                if int(p.read_text(encoding="utf-8").strip() or 0) == os.getpid():
                    p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def main() -> int:
    _setup_logging()
    logging.info("Tom's Lab starting (pid=%d)", os.getpid())

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)

    # Show the splash as early as possible so the user sees something
    # the moment they launch, rather than staring at a cold screen while
    # Qt styling, DB init, and MainWindow widget construction run.
    splash = _build_splash()
    splash.show()
    splash.raise_()
    # Pump the event loop once so the splash actually paints before we
    # enter the slow path below.
    app.processEvents()
    _splash_timer = QElapsedTimer()
    _splash_timer.start()

    # Global stylesheet — tightens typography, scrollbars, tooltips,
    # selection colors, and every QWidget base across the app without
    # touching each view individually. Per-widget stylesheets still
    # override these — this is the floor, not the ceiling.
    from tomslab.ui import theme as _theme
    app.setStyleSheet(f"""
        * {{
            font-family: "Segoe UI", "Inter", "SF Pro Text", sans-serif;
            font-size: {_theme.FONT_SIZE_BODY}px;
        }}
        QWidget {{
            background: {_theme.BG};
            color: {_theme.TEXT};
        }}
        QMenuBar {{
            background: {_theme.BG};
            color: {_theme.TEXT};
            border-bottom: 1px solid {_theme.BORDER_SOFT};
        }}
        QMenuBar::item:selected {{
            background: {_theme.BG_ALT};
        }}
        QMenu {{
            background: {_theme.BG_ALT};
            color: {_theme.TEXT};
            border: 1px solid {_theme.BORDER};
            padding: 4px;
        }}
        QMenu::item {{ padding: 6px 18px; border-radius: {_theme.RADIUS_SM}px; }}
        QMenu::item:selected {{ background: {_theme.BORDER}; }}
        QMenu::separator {{ background: {_theme.BORDER_SOFT}; height: 1px; margin: 4px 8px; }}
        QStatusBar {{
            background: {_theme.BG_DEEP};
            color: {_theme.TEXT_DIM};
            border-top: 1px solid {_theme.BORDER_SOFT};
        }}
        QStatusBar::item {{ border: none; }}
        QToolTip {{
            background: {_theme.BG_DEEP};
            color: {_theme.TEXT};
            border: 1px solid {_theme.BORDER};
            padding: 6px 10px;
            border-radius: {_theme.RADIUS_SM}px;
        }}
        QTabWidget::pane {{
            border: none;
            background: {_theme.BG};
        }}
        QTabBar::tab {{
            background: transparent;
            color: {_theme.TEXT_DIM};
            padding: 8px 18px;
            border: none;
            border-bottom: 2px solid transparent;
            font-size: {_theme.FONT_SIZE_BODY_LG}px;
        }}
        QTabBar::tab:hover {{ color: {_theme.TEXT}; }}
        QTabBar::tab:selected {{
            color: {_theme.TEXT};
            border-bottom: 2px solid {_theme.TOM_GOLD};
            font-weight: 600;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {_theme.BORDER};
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {_theme.TEXT_DIM}; }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
        }}
        QScrollBar::handle:horizontal {{
            background: {_theme.BORDER};
            border-radius: 5px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {_theme.TEXT_DIM}; }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{ width: 0; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser {{
            background: {_theme.BG_ALT};
            color: {_theme.TEXT};
            border: 1px solid {_theme.BORDER};
            border-radius: {_theme.RADIUS_MD}px;
            padding: 6px 10px;
            selection-background-color: {_theme.PRIMARY};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: {_theme.TOM_GOLD};
        }}
        QListView, QListWidget, QTreeView {{
            background: {_theme.BG};
            color: {_theme.TEXT};
            border: none;
            outline: none;
        }}
        QListView::item:selected, QListWidget::item:selected {{
            background: {_theme.BG_ALT};
            color: {_theme.TEXT};
        }}
        QComboBox {{
            background: {_theme.BG_ALT};
            color: {_theme.TEXT};
            border: 1px solid {_theme.BORDER};
            border-radius: {_theme.RADIUS_MD}px;
            padding: 4px 10px;
        }}
        QComboBox:focus {{ border-color: {_theme.TOM_GOLD}; }}
        QSpinBox, QDoubleSpinBox {{
            background: {_theme.BG_ALT};
            color: {_theme.TEXT};
            border: 1px solid {_theme.BORDER};
            border-radius: {_theme.RADIUS_SM}px;
            padding: 2px 6px;
        }}
        QGroupBox {{
            border: 1px solid {_theme.BORDER};
            border-radius: {_theme.RADIUS_MD}px;
            margin-top: 10px;
            padding-top: 8px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {_theme.TEXT};
        }}
    """)

    other_pid = _check_single_instance()
    if other_pid:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Tom's Lab is already running")
        box.setText(
            f"Another Tom's Lab window appears to be open (pid {other_pid}). "
            f"Switch to that window instead.<br><br>"
            f"<i>If you can't find it, open Task Manager → Details, end the "
            f"python.exe with that PID, then launch again.</i>"
        )
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setWindowFlags(box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        box.raise_()
        box.activateWindow()
        box.exec()
        return 0

    logging.info("creating main window")
    window = MainWindow()
    logging.info("showing main window")

    # Keep the splash up for at least SPLASH_MIN_MS even if MainWindow
    # finished constructing faster. Without this, on a warm cache the
    # splash can blink away in under a second and the user misses it.
    remaining_ms = max(0, SPLASH_MIN_MS - int(_splash_timer.elapsed()))
    if remaining_ms > 0:
        QTimer.singleShot(remaining_ms, lambda: (splash.finish(window), window.show()))
    else:
        splash.finish(window)
        window.show()

    try:
        return app.exec()
    finally:
        _release_single_instance()


if __name__ == "__main__":
    sys.exit(main())
