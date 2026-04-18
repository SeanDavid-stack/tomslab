"""Tom's Lab — entry point."""
from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QSharedMemory
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


# Unique key for the single-instance lock. The shared-memory segment is
# created at app start and released when the process exits; a second
# launch sees the key already exists and bails out with a friendly
# message rather than fighting the DB.
_SINGLE_INSTANCE_KEY = "tomslab.single-instance.v1"


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)

    # Single-instance guard. Holding `lock` on a module-level name keeps
    # the segment alive for the lifetime of the process; dropping the
    # reference would let another launcher grab it.
    lock = QSharedMemory(_SINGLE_INSTANCE_KEY)
    # On an unclean previous exit (crash, kill, power loss), a stale
    # segment can linger — attach+detach once to clear it before we try
    # to create, otherwise a legitimate re-launch would be rejected.
    if lock.attach():
        lock.detach()
    if not lock.create(1):
        QMessageBox.information(
            None,
            "Tom's Lab is already running",
            "Another instance of Tom's Lab is already open on this "
            "computer. Switch to that window instead — running two "
            "copies at once can corrupt the in-progress ingest state.",
        )
        return 0

    window = MainWindow()
    window.show()
    try:
        return app.exec()
    finally:
        lock.detach()


if __name__ == "__main__":
    sys.exit(main())
