"""בדיקת עשן לממשק, בלי מסך ובלי רשת."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtWidgets import QApplication

from ali_tracker import samples
from ali_tracker.config import Config
from ali_tracker.gui.window import MainWindow
from ali_tracker.parser import parse_mail
from ali_tracker.store import Store


def test_window(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0)
    st = Store(cfg.db_path)
    for subject, html, date, mid in samples.demo_mails():
        st.apply(parse_mail(subject, html, date, mid))
    st.close()
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_image", lambda self, url, label: label.setText("img"))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(cfg)
    w.show()
    app.processEvents()
    assert w.table.rowCount() == 4
    assert "יש הודעות חדשות" in w.banner.text()
    w.filter.setCurrentIndex(1)                       # דורשות פעולה
    keys = {w.table.item(i, 0).text().strip("\u202a\u202c") for i in range(w.table.rowCount())}
    assert "AP00000000000003" in keys
    w.search.setText("insoles")
    w.filter.setCurrentIndex(2)
    assert w.table.rowCount() == 1
    w.select_row(0)
    assert w._selected_key() == "AP00000000000003"
    texts = " ".join(lbl.text() for lbl in w.details.findChildren(type(w.banner)))
    assert "נקודת האיסוף" in texts and "Sport Insoles" in texts
    w.close()


def test_received_hidden_by_default_and_days_column(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0)
    st = Store(cfg.db_path)
    for subject, html, date, mid in samples.demo_mails():
        st.apply(parse_mail(subject, html, date, mid))
    st.set_done(next(r for r in st.rows() if r.key == "AP00000000000003"), True)
    st.apply(parse_mail("Order 1000000000000002: how did it go?", "", datetime.now(timezone.utc),
                        "<rev@x>"))                    # נמסר, עוד לא סומן «קיבלתי»
    st.close()
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_image", lambda self, url, label: label.setText("img"))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(cfg)
    key = lambda i: w.table.item(i, 0).text().strip("\u202a\u202c")  # noqa: E731
    assert w.filter.currentData() == "open"
    shown = {key(i): w.table.item(i, 5).text() for i in range(w.table.rowCount())}
    assert set(shown) == {"RS0000000001Y", "1000000000000004"}
    assert shown["RS0000000001Y"] == "30" and shown["1000000000000004"] == "10"
    assert w.table.horizontalHeaderItem(5).text() == "ימים מההזמנה"
    w.filter.setCurrentIndex(2)                       # הכול
    assert w.table.rowCount() == 4
    w.close()


def test_message_seller(tmp_path, monkeypatch):
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import QMessageBox, QPushButton

    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0, late_days=30)
    st = Store(cfg.db_path)
    for subject, html, date, mid in samples.demo_mails():
        st.apply(parse_mail(subject, html, date, mid))
    st.close()
    opened = []
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_image", lambda self, url, label: label.setText("img"))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text() == "שלחתי"))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(cfg)
    rows = [w.table.item(i, 0).text().strip("\u202a\u202c") for i in range(w.table.rowCount())]
    w.select_row(rows.index("RS0000000001Y"))
    assert "לשלוח הודעה למוכר" in w.table.item(w.table.currentRow(), 3).text()
    btn = next(b for b in w.details.findChildren(QPushButton) if "הודעה למוכר" in b.text())
    btn.click()
    text = QGuiApplication.clipboard().text()
    assert "1000000000000001" in text and "RS0000000001Y" in text and "30 days" in text
    assert opened and "orderId=1000000000000001" in opened[0]
    assert "לחכות" in w.table.item(w.table.currentRow(), 3).text()
    assert next(r for r in w.rows if r.key == "RS0000000001Y").messaged_at is not None
    w.close()


@pytest.mark.parametrize("fail", [False, True])
def test_sync_result_reaches_window(tmp_path, monkeypatch, fail):
    """התוצאה של הסנכרון ברקע חייבת להגיע לחלון, גם כשההתחברות נכשלת."""
    import gc
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    from ali_tracker import mail
    from ali_tracker.secret import protect

    def fake_fetch(*args, progress=None, **kw):
        time.sleep(0.3)
        gc.collect()
        if fail:
            raise mail.MailError("Gmail סירב להתחבר")
        return samples.demo_mails(), 9, "1"

    shown = []
    monkeypatch.setattr(mail, "fetch", fake_fetch)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(MainWindow, "_load_image", lambda self, url, label: label.setText("img"))
    cfg = Config(data_dir=str(tmp_path), gmail="me@gmail.com", app_password=protect("x"),
                 auto_sync_minutes=0)
    app = QApplication.instance() or QApplication([])
    w = MainWindow(cfg)
    QTimer.singleShot(50, lambda: w.sync(quiet=False))
    QTimer.singleShot(2500, app.quit)
    app.exec()                       # (QTest.qWait לא משחרר את ה-GIL, ואז הרקע לא רץ)
    assert not w.syncing and w.sync_btn.isEnabled()
    if fail:
        assert shown and "סירב" in shown[0]
    else:
        assert w.table.rowCount() == 4
    w.close()


def test_images_download_with_fallback(tmp_path):
    """תמונה נטענת; כתובת שלא עובדת עוברת לגרסה המוקטנת; כשלון נרשם ביומן."""
    import http.server
    import threading

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QTimer
    from PySide6.QtGui import QColor, QImage

    from ali_tracker.gui.images import ImageLoader

    app = QApplication.instance() or QApplication([])
    img = QImage(10, 10, QImage.Format_RGB32)
    img.fill(QColor("red"))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG")
    jpg = bytes(ba)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("good.jpg") or self.path.endswith("_220x220.jpg"):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(jpg)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/kf/"
    import ali_tracker.gui.images as images
    orig = images.candidates
    images.candidates = lambda u: [u] + ([u + "_220x220.jpg"] if u.endswith(".jpg") else [])
    try:
        loader = ImageLoader(tmp_path / "img")
        got = {}
        for name in ("good.jpg", "small.jpg", "none.gif"):
            loader.get(base + name, lambda p, n=name: got.__setitem__(n, p))
        QTimer.singleShot(3000, app.quit)
        t = QTimer()
        t.timeout.connect(lambda: len(got) == 3 and app.quit())
        t.start(50)
        app.exec()
    finally:
        images.candidates = orig
        srv.shutdown()
    assert got["good.jpg"] and got["small.jpg"] and got["none.gif"] == ""
    assert "none.gif" in (tmp_path / "img" / "errors.log").read_text(encoding="utf-8")
