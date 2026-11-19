"""הממשק הגרפי (PySide6)."""
from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _install_crash_log(log: Path):
    """רושם כל קריסה ל-data/crash.log, כדי שאפשר יהיה לדעת מה קרה גם בלי חלון שחור."""
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "a", encoding="utf-8", buffering=1)
    f.write(f"\n=== started {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    faulthandler.enable(file=f, all_threads=True)

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        f.write(f"--- error {datetime.now():%H:%M:%S}\n{text}")
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "שגיאה בתוכנה",
                                 f"קרתה שגיאה. הפרטים נשמרו בקובץ:\n{log}\n\n{exc}")
        except Exception:
            pass

    sys.excepthook = hook
    return f


def run() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ..config import Config
    from .window import MainWindow

    cfg = Config.load()
    _keep = _install_crash_log(Path(cfg.data_dir) / "crash.log")  # noqa: F841
    app = QApplication(sys.argv)
    app.setApplicationName("Ali Tracker")
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)
    win = MainWindow(cfg)
    win.show()
    return app.exec()
