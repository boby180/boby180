"""חלון ההגדרות: כתובת Gmail וסיסמה לאפליקציה."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QSpinBox)

from ..config import Config
from ..secret import protect, unprotect

HELP = (
    "כדי שהתוכנה תוכל לקרוא את המיילים של אלי אקספרס צריך «סיסמה לאפליקציה» של Google. "
    "זו סיסמה נפרדת של 16 תווים, לא הסיסמה הרגילה שלך:<br>"
    "1. מפעילים «אימות דו-שלבי» בחשבון Google (אם עוד לא מופעל).<br>"
    "2. נכנסים ל-<a href='https://myaccount.google.com/apppasswords'>myaccount.google.com/apppasswords</a>, "
    "נותנים שם (למשל Ali Tracker) ולוחצים «יצירה».<br>"
    "3. מעתיקים לכאן את 16 התווים שמופיעים.<br><br>"
    "התוכנה רק קוראת מיילים מ-aliexpress.com, לא מוחקת ולא מסמנת אותם כנקראו. "
    "הסיסמה נשמרת במחשב הזה בלבד, מוצפנת למשתמש שלך ב-Windows."
)


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("הגדרות חיבור ל-Gmail")
        self.setMinimumWidth(560)

        self.address = QLineEdit(cfg.gmail)
        self.address.setPlaceholderText("name@gmail.com")
        self.address.setLayoutDirection(Qt.LeftToRight)
        self.password = QLineEdit(unprotect(cfg.app_password))
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("xxxx xxxx xxxx xxxx")
        self.password.setLayoutDirection(Qt.LeftToRight)
        self.days = QSpinBox()
        self.days.setRange(14, 730)
        self.days.setValue(cfg.days_back)
        self.days.setSuffix(" ימים")
        self.every = QSpinBox()
        self.every.setRange(0, 720)
        self.every.setValue(cfg.auto_sync_minutes)
        self.every.setSuffix(" דקות")
        self.every.setSpecialValueText("כבוי")
        self.late = QSpinBox()
        self.late.setRange(0, 120)
        self.late.setValue(cfg.late_days)
        self.late.setSuffix(" ימים מההזמנה")
        self.late.setSpecialValueText("לא להזכיר")

        help_ = QLabel(HELP)
        help_.setWordWrap(True)
        help_.setOpenExternalLinks(True)
        help_.setTextFormat(Qt.RichText)

        form = QFormLayout(self)
        form.addRow(help_)
        form.addRow("כתובת Gmail", self.address)
        form.addRow("סיסמה לאפליקציה", self.password)
        form.addRow("לחפש מיילים מהתקופה של", self.days)
        form.addRow("סנכרון אוטומטי כל", self.every)
        form.addRow("זמן משלוח צפוי (אחריו: הודעה למוכר)", self.late)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("שמור")
        buttons.button(QDialogButtonBox.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self) -> None:
        self.cfg.gmail = self.address.text().strip()
        self.cfg.app_password = protect(self.password.text().strip())
        self.cfg.days_back = self.days.value()
        self.cfg.auto_sync_minutes = self.every.value()
        self.cfg.late_days = self.late.value()
        self.cfg.save()
        super().accept()
