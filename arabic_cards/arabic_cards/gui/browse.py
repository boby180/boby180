"""מסך הכרטיסים: חיפוש, ייבוא חפיסה, הוספה, עריכה, מחיקה והשמעה."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..anki_import import notes_to_cards, read_apkg
from ..config import Config
from ..models import Card, ReviewState
from ..storage import Storage
from .audio import AudioService
from .edit import EditDialog

COLUMNS = ["#", "ערבית", "תעתיק", "עברית", "טקסט להקראה", "חזרה הבאה"]


def _due_text(st: ReviewState) -> str:
    if st.reps == 0:
        return "חדש"
    days = (st.due - datetime.now(timezone.utc)).total_seconds() / 86400
    if days <= 0:
        return "עכשיו"
    if days < 0.5:
        return "היום"
    return "מחר" if round(days) <= 1 else f"בעוד {round(days)} ימים"


class BrowsePage(QWidget):
    cards_changed = Signal()

    def __init__(self, cfg: Config, db: Storage, audio: AudioService, parent=None):
        super().__init__(parent)
        self.cfg, self.db, self.audio = cfg, db, audio

        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש בערבית, בתעתיק או בעברית…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.total = QLabel()

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.total)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Stretch)
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda _i: self.edit())
        self.table.itemSelectionChanged.connect(self._sync_buttons)

        self.import_btn = QPushButton("ייבוא חפיסת Anki…")
        self.import_btn.clicked.connect(self.import_deck)
        self.add_btn = QPushButton("כרטיס חדש")
        self.add_btn.clicked.connect(self.add)
        self.listen_btn = QPushButton("🔊 השמע")
        self.listen_btn.clicked.connect(self.listen)
        self.edit_btn = QPushButton("✎ ערוך")
        self.edit_btn.clicked.connect(self.edit)
        self.delete_btn = QPushButton("מחק")
        self.delete_btn.clicked.connect(self.delete)

        bottom = QHBoxLayout()
        for b in (self.import_btn, self.add_btn):
            bottom.addWidget(b)
        bottom.addStretch()
        for b in (self.listen_btn, self.edit_btn, self.delete_btn):
            bottom.addWidget(b)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 20)
        root.addLayout(top)
        root.addWidget(self.table, 1)
        root.addLayout(bottom)
        self.refresh()

    # ---------------------------------------------------------------- טבלה
    def refresh(self) -> None:
        keep = self._selected_id()
        rows = self.db.search(self.search.text())
        self.table.setRowCount(len(rows))
        for r, (c, st) in enumerate(rows):
            values = [str(c.id), c.arabic, c.translit, c.hebrew, c.tts_text, _due_text(st)]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, c.id)
                self.table.setItem(r, col, item)
            if c.id == keep:
                self.table.selectRow(r)
        all_cards = self.db.count()
        self.total.setText(f"{len(rows)} מתוך {all_cards} כרטיסים" if len(rows) != all_cards
                           else f"{all_cards} כרטיסים")
        self._sync_buttons()

    def _selected_id(self) -> int | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _selected(self) -> Card | None:
        cid = self._selected_id()
        return self.db.get_card(cid) if cid is not None else None

    def _sync_buttons(self) -> None:
        on = self._selected_id() is not None
        for b in (self.listen_btn, self.edit_btn, self.delete_btn):
            b.setEnabled(on)

    # ---------------------------------------------------------------- פעולות
    def listen(self) -> None:
        card = self._selected()
        if card:
            self.audio.speak_card(card)

    def add(self) -> None:
        dlg = EditDialog(Card(arabic="", source_deck="שלי"), self.audio, self)
        if dlg.exec() == QDialog.Accepted:
            self.db.add_card(dlg.card())
            self._changed()

    def edit(self) -> None:
        card = self._selected()
        if not card:
            return
        dlg = EditDialog(card, self.audio, self)
        if dlg.exec() == QDialog.Accepted:
            self.db.update_card(dlg.card())
            self._changed()

    def delete(self) -> None:
        card = self._selected()
        if not card:
            return
        ok = QMessageBox.question(
            self, "מחיקת כרטיס",
            f"למחוק את הכרטיס «{card.arabic}» ({card.hebrew})?\nגם היסטוריית החזרות שלו תימחק.")
        if ok == QMessageBox.Yes:
            self.db.delete_card(card.id)
            self._changed()

    def import_deck(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "בחירת חפיסת Anki", "",
                                              "חפיסות Anki (*.apkg)")
        if path:
            self.import_file(Path(path))

    def import_file(self, apkg: Path) -> None:
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            notes = read_apkg(apkg, media_out=self.cfg.media_dir)
            cards = notes_to_cards(notes, deck_name=apkg.stem, media_dir=self.cfg.media_dir)
            added = sum(1 for c in cards if self.db.add_card(c) is not None)
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "הייבוא נכשל", f"לא הצלחתי לקרוא את הקובץ.\n{e}")
            return
        QGuiApplication.restoreOverrideCursor()
        self._changed()
        skipped = len(notes) - len(cards)
        msg = f"נמצאו {len(notes)} פתקים. נוספו {added} כרטיסים חדשים."
        if len(cards) - added:
            msg += f"\n{len(cards) - added} כבר היו קיימים ולא יובאו שוב."
        if skipped:
            msg += f"\n{skipped} פתקים דולגו כי אין בהם טקסט בערבית."
        QMessageBox.information(self, "הייבוא הסתיים", msg)

    def _changed(self) -> None:
        self.refresh()
        self.cards_changed.emit()
