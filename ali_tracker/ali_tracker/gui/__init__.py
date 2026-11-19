"""הממשק הגרפי (PySide6)."""
from __future__ import annotations

import sys


def run() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ..config import Config
    from .window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Ali Tracker")
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)
    win = MainWindow(Config.load())
    win.show()
    return app.exec()
