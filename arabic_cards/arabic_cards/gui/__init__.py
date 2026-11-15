"""הממשק הגרפי (PySide6). הפעלה: python main.py"""
from __future__ import annotations

import sys


def run() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ..config import Config
    from .style import STYLE
    from .window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Arabic Cards")
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyleSheet(STYLE)
    win = MainWindow(Config.load())
    win.show()
    return app.exec()
