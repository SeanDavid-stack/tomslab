"""Tom's Lab — entry point. Phase 0: shows a Hello window."""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

from tomslab import __app_name__, __version__


class HelloWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.resize(720, 480)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(f"Hello, {__app_name__}")
        title_font = QFont()
        title_font.setPointSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Phase 0 scaffold — ingestion and search arrive in Phase 1.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #888;")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.setCentralWidget(central)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    window = HelloWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
