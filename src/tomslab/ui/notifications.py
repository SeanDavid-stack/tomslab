"""Desktop notifications (Windows toast / macOS notification / Linux libnotify).

Qt's ``QSystemTrayIcon.showMessage`` produces a real OS toast on all
three platforms, with zero extra dependencies. We guard the tray
creation behind a singleton so each notify() call doesn't spam a new
tray icon.

Use ``notify(title, body)`` from anywhere in the UI thread. Call
``prime_tray(parent)`` once from MainWindow after the window is shown
so the tray icon has a valid parent and is visible in the system tray.
"""
from __future__ import annotations

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

_tray: QSystemTrayIcon | None = None


def prime_tray(parent) -> QSystemTrayIcon | None:
    """Create a tray icon we can use for toasts. Returns None if the
    current OS / session doesn't support it (remote desktop, headless)."""
    global _tray
    if _tray is not None:
        return _tray
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    # Reuse the app's window icon if any; otherwise synthesise a simple
    # theme-matching icon so Windows doesn't show its default "?" icon.
    icon = QIcon()
    app = QApplication.instance()
    if app is not None:
        win_icon = getattr(app, "windowIcon", None)
        if win_icon is not None:
            icon = app.windowIcon()
    _tray = QSystemTrayIcon(icon, parent)
    _tray.setToolTip("Tom's Lab")
    _tray.setVisible(True)
    return _tray


def notify(title: str, body: str, *, urgent: bool = False) -> None:
    """Show a desktop toast. No-op if the tray isn't available."""
    tray = _tray
    if tray is None:
        return
    icon = (QSystemTrayIcon.MessageIcon.Critical if urgent
            else QSystemTrayIcon.MessageIcon.Information)
    # 5-second default on Windows; OS may cap / extend this.
    tray.showMessage(title, body, icon, 5000)
