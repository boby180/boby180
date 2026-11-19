"""עבודות רקע: משיכת מיילים והורדת תמונות, כדי שהחלון לא ייתקע."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import mail
from ..parser import parse_mail


class SyncSignals(QObject):
    progress = Signal(int, int)
    done = Signal(object, int, str)          # events, max uid, uidvalidity
    failed = Signal(str)


class SyncJob(QRunnable):
    def __init__(self, address: str, password: str, days: int, after_uid: int, validity: str,
                 signals: SyncSignals):
        super().__init__()
        self.args = (address, password, days, after_uid, validity)
        # האות שייך לחלון ולא לעבודה: QThreadPool מוחק את העבודה מיד כשהיא מסתיימת,
        # ואז הודעה שעוד ממתינה בתור (למשל "ההתחברות נכשלה") הייתה הולכת לאיבוד.
        self.signals = signals

    def run(self) -> None:
        address, password, days, after_uid, validity = self.args
        try:
            raw, max_uid, new_validity = mail.fetch(address, password, days, after_uid, validity,
                                                    progress=self.signals.progress.emit)
            events = []
            for subject, html, date, mid in raw:
                try:
                    events.append(parse_mail(subject, html, date, mid))
                except Exception:
                    continue                 # מייל אחד משובש לא יעצור את כל הסנכרון
            events.sort(key=lambda e: e.date)
            self.signals.done.emit(events, max_uid, new_validity)
        except mail.MailError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:
            self.signals.failed.emit(f"הסנכרון נכשל: {type(e).__name__}: {e}")
