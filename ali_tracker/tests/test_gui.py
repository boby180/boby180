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

from PySide6.QtCore import Qt
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
    key = lambda i: w.table.item(i, 0).data(Qt.UserRole)  # noqa: E731
    assert w.filter.currentData() == "open"
    shown = {key(i): w.table.item(i, 6).text() for i in range(w.table.rowCount())}
    assert set(shown) == {"RS0000000001Y", "1000000000000004"}
    assert shown["RS0000000001Y"] == "30" and shown["1000000000000004"] == "10"
    assert w.table.horizontalHeaderItem(6).text() == "ימים מההזמנה"
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
    assert "לשלוח הודעה למוכר" in w.table.item(w.table.currentRow(), 4).text()
    btn = next(b for b in w.details.findChildren(QPushButton) if "הודעה למוכר" in b.text())
    btn.click()
    text = QGuiApplication.clipboard().text()
    assert "1000000000000001" in text and "RS0000000001Y" in text and "30 days" in text
    assert opened and "orderId=1000000000000001" in opened[0]
    assert "לחכות" in w.table.item(w.table.currentRow(), 4).text()
    assert next(r for r in w.rows if r.key == "RS0000000001Y").messaged_at is not None
    w.close()


def _serve(routes: dict[str, tuple[int, str]]):
    """שרת מקומי קטן שמחזיר תשובה קבועה לכל נתיב."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code, body = routes.get(self.path.split("?")[0], (404, ""))
            self.send_response(code)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _wait(app, cond, ms=5000):
    from PySide6.QtCore import QTimer
    t = QTimer()
    t.timeout.connect(lambda: cond() and app.quit())
    t.start(30)
    QTimer.singleShot(ms, app.quit)
    app.exec()
    t.stop()


@pytest.mark.real_rates
@pytest.mark.parametrize("json_ok", [True, False])
def test_rate_from_bank_of_israel(tmp_path, monkeypatch, json_ok):
    """השער מתעדכן מבנק ישראל; אם הממשק הראשי לא עונה, מהסדרות (CSV); ומחירים בדולרים מתעדכנים."""
    from ali_tracker.gui import rates as gui_rates

    head = "TIME_PERIOD,OBS_VALUE\n"
    srv, base = _serve({
        "/json": (200 if json_ok else 500,
                  '{"exchangeRates":[{"key":"USD","currentExchangeRate":3.293,"unit":1,'
                  '"lastUpdate":"2026-11-27T10:21:05Z"},'
                  '{"key":"EUR","currentExchangeRate":3.6092,"unit":1,"lastUpdate":"2026-11-27T10:21:05Z"}]}'),
        "/USD": (200, head + "2026-11-26,3.334\n2026-11-27,3.3\n"),
        "/EUR": (200, head + "2026-11-27,3.61\n"),
    })
    orig = gui_rates.RateFetcher.__init__

    def init(self, parent=None):
        orig(self, parent)
        self.json_url, self.csv_url = base + "/json", base + "/{cur}"
    monkeypatch.setattr(gui_rates.RateFetcher, "__init__", init)
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_image", lambda self, url, label: label.setText("img"))
    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0, usd_rate=3.7)
    st = Store(cfg.db_path)
    for subject, html, date, mid in samples.demo_mails():
        st.apply(parse_mail(subject, html, date, mid))
    st.close()
    app = QApplication.instance() or QApplication([])
    try:
        w = MainWindow(cfg)
        _wait(app, lambda: cfg.rate_date)
    finally:
        srv.shutdown()
    want = 3.293 if json_ok else 3.3
    assert cfg.usd_rate == want and cfg.rate_date == "2026-11-27" and cfg.rate_checked
    assert cfg.eur_rate == (3.6092 if json_ok else 3.61)
    assert "בנק ישראל, 27.11.2026" in w.rate_label.text()
    w.filter.setCurrentIndex(2)
    price = {w.table.item(i, 0).data(Qt.UserRole): w.table.item(i, 2).text()
             for i in range(w.table.rowCount())}
    assert price["RS0000000001Y"] == f"≈ ₪{30.40 * want:,.2f}"
    # בדיקה נוספת מיד אחר כך לא פונה שוב לבנק
    calls = []
    monkeypatch.setattr(w.rates, "fetch", lambda: calls.append(1))
    w.check_rate()
    assert not calls
    w.close()


@pytest.mark.real_rates
def test_rate_failure_keeps_saved_rate(tmp_path, monkeypatch):
    from ali_tracker.gui import rates as gui_rates

    srv, base = _serve({})
    orig = gui_rates.RateFetcher.__init__

    def init(self, parent=None):
        orig(self, parent)
        self.json_url, self.csv_url = base + "/json", base + "/{cur}"
    monkeypatch.setattr(gui_rates.RateFetcher, "__init__", init)
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0, usd_rate=3.5, rate_date="2026-11-20")
    app = QApplication.instance() or QApplication([])
    try:
        w = MainWindow(cfg)
        _wait(app, lambda: "⚠" in w.rate_label.text())
    finally:
        srv.shutdown()
    assert cfg.usd_rate == 3.5 and "⚠" in w.rate_label.text()
    assert "לא הצלחתי" in w.rate_label.toolTip()
    w.close()


def test_manual_rate(tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0, usd_rate=3.65, auto_rate=False)
    app = QApplication.instance() or QApplication([])  # noqa: F841
    w = MainWindow(cfg)
    assert w.rate_label.text() == "$1 = ₪3.65 (ידני)"
    w.close()


def test_prices_and_month_total(tmp_path, monkeypatch):
    from datetime import timedelta

    now = datetime(2026, 11, 29, 12, tzinfo=timezone.utc)   # הזמנה 3: 1.11, הזמנה 1: 30.10
    cfg = Config(data_dir=str(tmp_path), auto_sync_minutes=0, usd_rate=3.7)
    st = Store(cfg.db_path)
    for subject, html, date, mid in samples.demo_mails(now):
        st.apply(parse_mail(subject, html, date, mid))
    st.apply(parse_mail("Order 1000000000000005: order confirmed",
                        samples.order_mail("1000000000000005", [("Cable...", "1m", "x1")], "₪9.00"),
                        now - timedelta(days=3), "<m10@x>"))
    st.apply(parse_mail("Order 1000000000000005: order cancelled", "", now - timedelta(days=2), "<m11@x>"))
    st.close()
    monkeypatch.setattr(MainWindow, "_first_run", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_image", lambda self, url, label: label.setText("img"))
    app = QApplication.instance() or QApplication([])
    w = MainWindow(cfg)
    w.filter.setCurrentIndex(2)
    price = {w.table.item(i, 0).data(Qt.UserRole): w.table.item(i, 2).text()
             for i in range(w.table.rowCount())}
    assert price["RS0000000001Y"] == "≈ ₪112.48"            # US $30.40 × 3.7
    assert price["LP00000000000002"] == "₪47.80"
    # נובמבר: הזמנות 2, 3, 4. הזמנה 1 מאוקטובר, והזמנה 5 בוטלה
    assert w.month_total(now) == (pytest.approx(47.80 + 18.90 + 28.65), 3, False)
    assert "הזמנות ב" in w.month_label.text()
    keys = [w.table.item(i, 0).data(Qt.UserRole) for i in range(w.table.rowCount())]
    w.select_row(keys.index("AP00000000000003"))
    texts = " ".join(lbl.text() for lbl in w.details.findChildren(type(w.banner)))
    assert "₪18.90" in texts
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
