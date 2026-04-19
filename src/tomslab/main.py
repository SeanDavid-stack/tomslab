"""Tom's Lab — entry point."""
from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtCore import Qt, QSharedMemory
from PyQt6.QtWidgets import QApplication, QMessageBox

from tomslab import __app_name__
from tomslab.paths import log_path
from tomslab.ui.main_window import MainWindow


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_path(), encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


# Unique key for the single-instance lock. The shared-memory segment
# records the PID of the holding process so a second launch can
# verify liveness and steal a stale lock instead of silently bailing.
_SINGLE_INSTANCE_KEY = "tomslab.single-instance.v2"
_PID_SLOT_SIZE = 32   # bytes — enough for any 64-bit PID as ASCII


def _pid_alive(pid: int) -> bool:
    """True if the given PID corresponds to a running process."""
    if pid <= 0:
        return False
    try:
        # On Windows, os.kill(pid, 0) raises OSError if the pid is gone.
        # On POSIX, same — signal 0 doesn't send a signal, just checks.
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def _read_pid(segment: QSharedMemory) -> int:
    try:
        if not segment.lock():
            return 0
        try:
            raw = bytes(segment.data()[:_PID_SLOT_SIZE])
        finally:
            segment.unlock()
        return int(raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore") or 0)
    except Exception:
        return 0


def _write_pid(segment: QSharedMemory, pid: int) -> None:
    try:
        if not segment.lock():
            return
        try:
            buf = str(pid).encode("ascii").ljust(_PID_SLOT_SIZE, b"\x00")
            segment.data()[: _PID_SLOT_SIZE] = bytes(buf)
        finally:
            segment.unlock()
    except Exception:
        pass


def _acquire_single_instance_lock() -> tuple[QSharedMemory, int]:
    """Grab the single-instance lock, auto-stealing stale segments left
    behind by crashed / force-killed prior runs. Returns ``(segment,
    other_live_pid)``. If ``other_live_pid`` is nonzero, a real Tom's
    Lab is already running and the caller should exit."""
    seg = QSharedMemory(_SINGLE_INSTANCE_KEY)

    # Fast path: create a fresh segment. If that works, we're alone.
    if seg.create(_PID_SLOT_SIZE):
        _write_pid(seg, os.getpid())
        return seg, 0

    # A segment already exists. Attach and see who owns it.
    if not seg.attach():
        # Can't even attach — Qt refused. Log and fall through as if
        # we're the only instance so the app isn't perma-bricked.
        logging.warning("single-instance: could not attach existing segment")
        return seg, 0

    holder_pid = _read_pid(seg)
    if holder_pid and _pid_alive(holder_pid) and holder_pid != os.getpid():
        # Real live other instance. Caller will show the dialog + exit.
        return seg, holder_pid

    # The segment exists but nobody living owns it — stale lock from a
    # crash / force-kill / power loss. Release it and re-create.
    seg.detach()
    # On Windows a detach from the last attacher frees the segment;
    # a fresh create() should now succeed. If it still refuses, we
    # log and continue rather than block the user.
    if seg.create(_PID_SLOT_SIZE):
        _write_pid(seg, os.getpid())
        logging.info("single-instance: stole stale lock (prior holder PID %s gone)",
                     holder_pid or "?")
        return seg, 0
    logging.warning("single-instance: segment stuck even after detach; proceeding")
    return seg, 0


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)

    # Single-instance guard. Holding `lock` on a module-level name keeps
    # the segment alive for the lifetime of the process; dropping the
    # reference would let another launcher grab it.
    lock, other_pid = _acquire_single_instance_lock()
    if other_pid:
        # Pop the 'already running' notice in front of other apps so
        # the user actually sees it (previously it was hiding behind
        # Firefox / the download batch window).
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Tom's Lab is already running")
        box.setText(
            f"Another Tom's Lab window is open on this computer "
            f"(process id {other_pid}). Switch to that window "
            f"instead — running two copies at once can corrupt the "
            f"in-progress ingest state.<br><br>"
            f"<i>If you can't find that window, open Task Manager, end "
            f"the python.exe process with id {other_pid}, then launch "
            f"again.</i>"
        )
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setWindowFlags(
            box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        box.raise_()
        box.activateWindow()
        box.exec()
        return 0

    window = MainWindow()
    window.show()
    try:
        return app.exec()
    finally:
        lock.detach()


if __name__ == "__main__":
    sys.exit(main())
