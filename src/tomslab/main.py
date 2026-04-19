"""Tom's Lab — entry point."""
from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtWidgets import QApplication

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
    window.show()
    try:
        return app.exec()
    finally:
        _release_single_instance()


if __name__ == "__main__":
    sys.exit(main())
