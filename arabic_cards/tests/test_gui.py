"""בדיקת עשן לממשק הגרפי, בלי מסך. מדולגת אם PySide6 לא מותקן."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from arabic_cards.config import Config
from arabic_cards.gui.window import MainWindow
from arabic_cards.models import Card
from arabic_cards.storage import Storage


def test_review_flow(tmp_path):
    cfg = Config(data_dir=str(tmp_path), autoplay=False)
    db = Storage(cfg.db_path)
    db.add_card(Card(arabic="هون", translit="הוֹן", hebrew="כאן"))
    db.add_card(Card(arabic="بدّي", translit="בִּדִּי", hebrew="אני רוצה"))
    db.close()

    app = QApplication.instance() or QApplication([])
    win = MainWindow(cfg)
    win.show()
    win.activateWindow()
    app.processEvents()
    page = win.review

    assert page.front.text() == "هون" and page.back2.text() == ""
    QTest.keyClick(win, Qt.Key_3)                 # ציון לפני הצגת התשובה לא נספר
    assert page.card.id == 1
    QTest.keyClick(win, Qt.Key_Space)
    assert page.back1.text() == "הוֹן" and page.back2.text() == "כאן"
    assert "יום" in page.grade_btns[3].text()
    QTest.keyClick(win, Qt.Key_1)                 # שוב -> הכרטיס חוזר בסוף הסבב
    assert page.front.text() == "بدّي"
    QTest.keyClick(win, Qt.Key_Space); QTest.keyClick(win, Qt.Key_4)
    QTest.keyClick(win, Qt.Key_Space); QTest.keyClick(win, Qt.Key_3)
    assert page.card is None and "סיימת" in page.done_msg.text()

    win.tabs.setCurrentWidget(win.browse)
    assert win.browse.table.rowCount() == 2
    win.browse.search.setText("רוצה")
    assert win.browse.table.rowCount() == 1
    win.close()
