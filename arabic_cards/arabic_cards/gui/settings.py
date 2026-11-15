"""מסך ההגדרות: בחירת קול (עם השוואה באוזן), קצב, מכסה יומית וכיוון הכרטיס."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..config import Config
from ..tts import LEVANTINE_VOICES
from .audio import AudioService

VOICE_LABELS = {
    "jordan_f": "ירדן, אישה (Sana)",
    "jordan_m": "ירדן, גבר (Taim)",
    "lebanon_f": "לבנון, אישה (Layla)",
    "lebanon_m": "לבנון, גבר (Rami)",
    "syria_f": "סוריה, אישה (Amany)",
    "syria_m": "סוריה, גבר (Laith)",
}
SAMPLE = "شو اسمك؟ كيف حالك اليوم؟"


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, cfg: Config, audio: AudioService, parent=None):
        super().__init__(parent)
        self.cfg, self.audio = cfg, audio

        self.voice = QComboBox()
        for key, voice in LEVANTINE_VOICES.items():
            self.voice.addItem(VOICE_LABELS.get(key, voice), voice)
        if self.voice.findData(cfg.voice) < 0:          # קול שהוגדר ידנית ב-config.json
            self.voice.addItem(cfg.voice, cfg.voice)
        self.voice.setCurrentIndex(self.voice.findData(cfg.voice))

        self.sample = QLineEdit(SAMPLE)
        self.sample.setStyleSheet("font-size: 16pt;")
        try_btn = QPushButton("🔊 השמע בקול הנבחר")
        try_btn.clicked.connect(self.try_voice)
        sample_row = QHBoxLayout()
        sample_row.addWidget(self.sample, 1)
        sample_row.addWidget(try_btn)

        self.rate = QSpinBox()
        self.rate.setRange(-50, 50)
        self.rate.setSingleStep(5)
        self.rate.setSuffix("%")
        self.rate.setLayoutDirection(Qt.LeftToRight)     # כדי שהמינוס יופיע משמאל למספר
        self.rate.setValue(self._rate_value(cfg.tts_rate))

        self.new_per_day = QSpinBox()
        self.new_per_day.setRange(0, 500)
        self.new_per_day.setValue(cfg.new_cards_per_day)

        self.direction = QComboBox()
        self.direction.addItem("ערבית בחזית, עברית בתשובה", "ar2he")
        self.direction.addItem("עברית בחזית, ערבית בתשובה", "he2ar")
        self.direction.setCurrentIndex(max(0, self.direction.findData(cfg.direction)))

        self.autoplay = QCheckBox("להשמיע את הכרטיס אוטומטית")
        self.autoplay.setChecked(cfg.autoplay)

        form = QFormLayout()
        form.addRow("קול", self.voice)
        form.addRow("משפט לדוגמה", sample_row)
        form.addRow("קצב דיבור", self.rate)
        form.addRow("כרטיסים חדשים ביום", self.new_per_day)
        form.addRow("כיוון הכרטיס", self.direction)
        form.addRow("", self.autoplay)

        note = QLabel("ההקראה נוצרת דרך האינטרנט (קולות Microsoft Edge) ונשמרת במחשב, "
                      "כך שכל מילה יורדת פעם אחת בלבד. כדאי להשוות בין הקולות על כמה "
                      "משפטים ולבחור את הקול שנשמע הכי קרוב לערבית פלסטינית.")
        note.setWordWrap(True)

        save = QPushButton("שמור הגדרות")
        save.clicked.connect(self.save)
        self.status = QLabel()
        save_row = QHBoxLayout()
        save_row.addWidget(save)
        save_row.addWidget(self.status)
        save_row.addStretch()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addLayout(form)
        root.addSpacing(8)
        root.addWidget(note)
        root.addSpacing(8)
        root.addLayout(save_row)
        root.addStretch()

    @staticmethod
    def _rate_value(rate: str) -> int:
        try:
            return int(rate.strip().rstrip("%"))
        except ValueError:
            return -10

    def _rate_text(self) -> str:
        return f"{self.rate.value():+d}%"

    def try_voice(self) -> None:
        """משמיע את המשפט בקול ובקצב שנבחרו עכשיו, גם לפני השמירה."""
        saved_rate, self.cfg.tts_rate = self.cfg.tts_rate, self._rate_text()
        try:
            self.audio.speak_text(self.sample.text(), voice=self.voice.currentData())
        finally:
            self.cfg.tts_rate = saved_rate

    def save(self) -> None:
        self.cfg.voice = self.voice.currentData()
        self.cfg.tts_rate = self._rate_text()
        self.cfg.new_cards_per_day = self.new_per_day.value()
        self.cfg.direction = self.direction.currentData()
        self.cfg.autoplay = self.autoplay.isChecked()
        self.cfg.save()
        self.status.setText("נשמר ✓")
        self.saved.emit()
