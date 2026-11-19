"""Ali Tracker: מעקב חבילות AliExpress מתוך Gmail.

  python main.py                 # הממשק הגרפי
  python main.py selftest FILE   # בדיקה עצמית (משמשת בבנייה האוטומטית)
"""
from __future__ import annotations

import sys
from pathlib import Path


def selftest(report: str) -> int:
    import tempfile
    import traceback
    lines, ok = [], True
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from ali_tracker import samples
        from ali_tracker.config import Config
        from ali_tracker.gui.window import MainWindow
        from ali_tracker.parser import parse_mail
        from ali_tracker.store import Store

        cfg = Config(data_dir=tempfile.mkdtemp(), auto_sync_minutes=0)
        st = Store(cfg.db_path)
        for subject, html, date, mid in samples.demo_mails():
            st.apply(parse_mail(subject, html, date, mid))
        st.close()
        MainWindow._first_run = lambda self: None
        app = QApplication(sys.argv[:1])
        win = MainWindow(cfg)
        win.show()
        QTimer.singleShot(1500, app.quit)
        app.exec()
        n = win.table.rowCount()
        lines.append(f"gui: ok, rows={n}")
        ok = n == 4
        win.close()
    except Exception:
        ok = False
        lines.append("gui: FAILED\n" + traceback.format_exc())
    try:
        import imaplib
        import ssl
        imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context(),
                          timeout=20).logout()
        lines.append("imap: ok, reached imap.gmail.com")
    except Exception as e:
        lines.append(f"imap: could not reach imap.gmail.com ({e})")
    try:                                      # תמונת מוצר אמיתית מהשרת של אלי אקספרס
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from ali_tracker.gui.images import ImageLoader
        app = QApplication.instance() or QApplication(sys.argv[:1])
        url = "https://ae-pic-a1.aliexpress-media.com/kf/S804c312f617c4d0386bd2795c228a27cS.jpg"
        cache = Path(tempfile.mkdtemp())
        got = []
        loader = ImageLoader(cache)
        loader.get(url, got.append)
        timer = QTimer()
        timer.timeout.connect(lambda: got and app.quit())
        timer.start(100)
        QTimer.singleShot(30000, app.quit)
        app.exec()
        log = (cache / "errors.log").read_text(encoding="utf-8") if (cache / "errors.log").exists() else ""
        lines.append(f"image: {'ok ' + str(Path(got[0]).stat().st_size) + ' bytes' if got and got[0] else 'FAILED'}"
                     + (f"\n{log}" if log else ""))
    except Exception:
        lines.append("image: FAILED\n" + traceback.format_exc())
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "selftest":
        sys.exit(selftest(sys.argv[2]))
    from ali_tracker.gui import run
    sys.exit(run())


if __name__ == "__main__":
    main()
