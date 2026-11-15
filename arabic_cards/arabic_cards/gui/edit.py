"""חלון עריכת כרטיס (וגם יצירת כרטיס חדש)."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton)

from ..models import Card
from .audio import AudioService


class EditDialog(QDialog):
    def __init__(self, card: Card, audio: AudioService, parent=None):
        super().__init__(parent)
        self.setWindowTitle("עריכת כרטיס" if card.id else "כרטיס חדש")
        self.setMinimumWidth(520)
        self._card, self._audio = card, audio

        self.arabic = self._arabic_edit(card.arabic)
        self.translit = QLineEdit(card.translit)
        self.hebrew = QLineEdit(card.hebrew)
        self.tts_text = self._arabic_edit(card.tts_text)
        self.tts_text.setPlaceholderText("ריק = לפי השדה «ערבית»")

        hint = QLabel("כאן כותבים את המילה כפי שהיא נהגית במדוברת, "
                      "למשל «شو» במקום «ماذا». זה הטקסט שיוקרא.")
        hint.setWordWrap(True)
        listen = QPushButton("🔊 השמע")
        listen.clicked.connect(self._listen)

        form = QFormLayout(self)
        form.addRow("ערבית", self.arabic)
        form.addRow("תעתיק", self.translit)
        form.addRow("עברית", self.hebrew)
        form.addRow("טקסט להקראה", self.tts_text)
        form.addRow("", hint)
        if card.audio_path:
            form.addRow("", QLabel("לכרטיס יש הקלטה מקורית מהחפיסה. היא מנוגנת בזמן החזרה."))
        form.addRow("", listen)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("שמור")
        buttons.button(QDialogButtonBox.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _arabic_edit(text: str) -> QLineEdit:
        e = QLineEdit(text)
        e.setLayoutDirection(Qt.RightToLeft)
        e.setStyleSheet("font-size: 18pt;")
        return e

    def _listen(self) -> None:
        """משמיע את מה שכתוב עכשיו בשדות, עוד לפני השמירה."""
        self._audio.speak_text(self.tts_text.text().strip() or self.arabic.text())

    def accept(self) -> None:
        if not self.arabic.text().strip():
            QMessageBox.warning(self, "חסר טקסט", "השדה «ערבית» לא יכול להיות ריק.")
            return
        super().accept()

    def card(self) -> Card:
        return replace(self._card,
                       arabic=self.arabic.text().strip(),
                       translit=self.translit.text().strip(),
                       hebrew=self.hebrew.text().strip(),
                       tts_text=self.tts_text.text().strip())
