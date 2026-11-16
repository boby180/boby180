"""מסך החזרה: כרטיס, השמעה, הצגת תשובה וכפתורי Again/Hard/Good/Easy."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..config import Config
from ..session import AGAIN, NEW, REVIEW, ReviewSession
from ..storage import Storage
from .audio import AudioService
from .edit import EditDialog

GRADES = {1: "שוב", 2: "קשה", 3: "טוב", 4: "קל"}


class ReviewPage(QWidget):
    cards_changed = Signal()

    def __init__(self, cfg: Config, db: Storage, audio: AudioService, parent=None):
        super().__init__(parent)
        self.cfg, self.db, self.audio = cfg, db, audio
        self.session: ReviewSession | None = None
        self.card = None
        self.revealed = False

        self.counts = QLabel(objectName="counts")
        self.counts.setTextFormat(Qt.RichText)

        # ---- הכרטיס
        self.front = QLabel(objectName="front")
        self.back1 = QLabel(objectName="back1")
        self.back2 = QLabel(objectName="back2")
        self.line = QFrame()
        self.line.setFrameShape(QFrame.HLine)
        self.line.setStyleSheet("color: #d5dbe1;")
        for lab in (self.front, self.back1, self.back2):
            lab.setAlignment(Qt.AlignCenter)
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_box = QFrame(objectName="card")
        cl = QVBoxLayout(card_box)
        cl.setContentsMargins(30, 30, 30, 30)
        cl.addStretch(2)
        cl.addWidget(self.front)
        cl.addSpacing(10)
        cl.addWidget(self.line)
        cl.addSpacing(10)
        cl.addWidget(self.back1)
        cl.addWidget(self.back2)
        cl.addStretch(3)

        self.listen_btn = self._button("🔊 השמע שוב (R)", self.listen)
        self.edit_btn = self._button("✎ ערוך כרטיס (E)", self.edit)
        tools = QHBoxLayout()
        tools.addWidget(self.listen_btn)
        tools.addWidget(self.edit_btn)
        tools.addStretch()

        # ---- שורת התשובה: "הצג תשובה" או ארבעת הציונים
        self.show_btn = self._button("הצג תשובה (רווח)", self.reveal)
        self.show_btn.setObjectName("show")
        self.grade_btns: dict[int, QPushButton] = {}
        grades = QWidget()
        gl = QHBoxLayout(grades)
        gl.setContentsMargins(0, 0, 0, 0)
        for g in GRADES:
            b = self._button("", lambda _=False, g=g: self.grade(g))
            b.setProperty("grade", str(g))
            self.grade_btns[g] = b
            gl.addWidget(b)
        self.answer_row = QStackedWidget()
        self.answer_row.addWidget(self.show_btn)
        self.answer_row.addWidget(grades)
        self.answer_row.setFixedHeight(70)

        study = QWidget()
        sl = QVBoxLayout(study)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(card_box, 1)
        sl.addLayout(tools)
        sl.addWidget(self.answer_row)

        # ---- מסך "סיימת"
        self.done_msg = QLabel(objectName="doneMsg")
        self.done_msg.setAlignment(Qt.AlignCenter)
        self.done_msg.setWordWrap(True)
        self.more_btn = self._button("ללמוד עוד כרטיסים חדשים", self.more_new)
        done = QWidget()
        dl = QVBoxLayout(done)
        dl.addStretch()
        dl.addWidget(self.done_msg)
        dl.addSpacing(16)
        dl.addWidget(self.more_btn, 0, Qt.AlignCenter)
        dl.addStretch()

        self.pages = QStackedWidget()
        self.pages.addWidget(study)
        self.pages.addWidget(done)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 20)
        root.addWidget(self.counts)
        root.addWidget(self.pages, 1)

        for key, fn in (("Space", self.advance), ("Return", self.advance),
                        ("Enter", self.advance), ("R", self.listen), ("E", self.edit),
                        ("1", lambda: self.grade(1)), ("2", lambda: self.grade(2)),
                        ("3", lambda: self.grade(3)), ("4", lambda: self.grade(4))):
            # קיצור ברמת החלון, כדי שיעבוד גם כשהפוקוס על שורת הלשוניות.
            # בלשוניות האחרות המסך מוסתר והקיצור לא עושה כלום.
            QShortcut(QKeySequence(key), self,
                      lambda fn=fn: self.isVisible() and fn(), context=Qt.WindowShortcut)
        self.restart()

    @staticmethod
    def _button(text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setFocusPolicy(Qt.NoFocus)        # המקלדת שייכת לקיצורי הדרך של המסך
        b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------------- סבב
    def restart(self, extra_new: int = 0) -> None:
        self.session = ReviewSession(self.db, self.cfg.new_cards_per_day, extra_new=extra_new)
        self.next_card()

    def is_idle(self) -> bool:
        """אין כרטיס פתוח, ולכן אפשר לרענן את הסבב בלי להפריע."""
        return self.card is None

    def more_new(self) -> None:
        self.restart(extra_new=self.cfg.new_cards_per_day)

    def next_card(self) -> None:
        self.card = self.session.current()
        self.revealed = False
        if self.card is None:
            self._show_done()
            return
        self.pages.setCurrentIndex(0)
        self.answer_row.setCurrentIndex(0)
        self._render()
        if self.cfg.autoplay and self.cfg.direction == "ar2he":
            self.listen()

    def _render(self) -> None:
        c, shown = self.card, self.revealed
        if self.cfg.direction == "he2ar":
            front, b1, b2 = c.hebrew or c.translit, c.arabic, c.translit
        else:
            front, b1, b2 = c.arabic, c.translit, c.hebrew
        self.front.setText(front)
        self.back1.setText(b1 if shown else "")
        self.back2.setText(b2 if shown else "")
        self.line.setVisible(shown)
        self.listen_btn.setEnabled(shown or self.cfg.direction == "ar2he")
        n = self.session.counts()
        self.counts.setText(
            f"<span style='color:#3498db'>חדשים: <b>{n[NEW]}</b></span> &nbsp;·&nbsp; "
            f"<span style='color:#e74c3c'>לחזור שוב: <b>{n[AGAIN]}</b></span> &nbsp;·&nbsp; "
            f"<span style='color:#27ae60'>חזרות: <b>{n[REVIEW]}</b></span>")

    def _show_done(self) -> None:
        self.audio.stop()
        self.counts.setText("")
        _due, new_left = self.db.counts()
        if self.db.count() == 0:
            msg = "עדיין אין כרטיסים.\nבלשונית «כרטיסים» אפשר לייבא חפיסת Anki ‏(‎.apkg) או להוסיף כרטיס."
        elif self.session.answered:
            msg = f"כל הכבוד! סיימת את הסבב ({self.session.answered} כרטיסים)."
        else:
            msg = "אין כרטיסים לחזרה כרגע."
        self.done_msg.setText(msg)
        self.more_btn.setVisible(new_left > 0)
        self.more_btn.setText(f"ללמוד עוד כרטיסים חדשים (נותרו {new_left})")
        self.pages.setCurrentIndex(1)

    # ---------------------------------------------------------------- פעולות
    def advance(self) -> None:
        """רווח / Enter: מציג את התשובה, ואם היא כבר מוצגת מסמן "טוב"."""
        if self.card is None:
            return
        if self.revealed:
            self.grade(3)
        else:
            self.reveal()

    def reveal(self) -> None:
        if self.card is None or self.revealed:
            return
        self.revealed = True
        for g, when in self.session.preview().items():
            self.grade_btns[g].setText(f"{GRADES[g]} ({g})\n{when}")
        self.answer_row.setCurrentIndex(1)
        self._render()
        if self.cfg.autoplay and self.cfg.direction == "he2ar":
            self.listen()

    def grade(self, g: int) -> None:
        if self.card is None or not self.revealed:
            return
        self.session.answer(g)
        self.next_card()

    def listen(self) -> None:
        if self.card is not None and self.listen_btn.isEnabled():
            self.audio.speak_card(self.card)

    def edit(self) -> None:
        if self.card is None:
            return
        dlg = EditDialog(self.card, self.audio, self)
        if dlg.exec() == QDialog.Accepted:
            self.card = dlg.card()
            self.db.update_card(self.card)
            self._render()
            self.cards_changed.emit()
