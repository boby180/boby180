"""בדיקת עשן לממשק, בלי מסך ובלי רשת."""
from __future__ import annotations

import os
import sys
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
