"""Robust external-browser open that actually brings the browser to
the foreground on Windows.

Python's ``webbrowser.open`` and Qt's ``QDesktopServices.openUrl`` both
hit the same problem on Windows: when Tom's Lab is the active window,
Windows' focus-stealing protection refuses to let the browser take the
foreground, so the user's click seems to do nothing while a new tab
quietly opens behind the app.

The fix is a two-step Win32 dance:

  1. ``AllowSetForegroundWindow(ASFW_ANY)`` — explicitly authorise the
     next process that asks for focus to get it.
  2. Launch the URL via ``QDesktopServices.openUrl``, which hands off
     to the registered default-browser handler via ``ShellExecuteEx``.

On non-Windows platforms the ``ctypes`` call is a no-op (we guard it
behind ``sys.platform``) and ``QDesktopServices.openUrl`` behaves
correctly by itself, so the same code path works everywhere.
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices


log = logging.getLogger(__name__)


def _allow_foreground_transfer() -> None:
    """Tell Windows: the next process that asks for foreground is
    allowed to take it. Silent no-op on non-Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # ASFW_ANY = -1; the cast is what Windows expects for 'any PID'.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    except Exception as exc:
        log.debug("AllowSetForegroundWindow failed (non-fatal): %s", exc)


def open_browser(url: str) -> bool:
    """Open ``url`` in the user's default browser, bringing the browser
    window to the foreground. Returns True if the call was successfully
    handed off to the OS, False otherwise (caller may fall back)."""
    if not url:
        return False
    _allow_foreground_transfer()
    try:
        return bool(QDesktopServices.openUrl(QUrl(url)))
    except Exception as exc:
        log.warning("QDesktopServices.openUrl failed for %s: %s", url, exc)
        return False
