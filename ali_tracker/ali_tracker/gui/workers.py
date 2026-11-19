"""עבודות רקע: משיכת מיילים והורדת תמונות, כדי שהחלון לא ייתקע."""
from __future__ import annotations

import hashlib
import ssl
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import mail
from ..parser import parse_mail


class SyncSignals(QObject):
    progress = Signal(int, int)
    done = Signal(object, int, str)          # events, max uid, uidvalidity
    failed = Signal(str)


class SyncJob(QRunnable):
    def __init__(self, address: str, password: str, days: int, after_uid: int, validity: str):
        super().__init__()
        self.args = (address, password, days, after_uid, validity)
        self.signals = SyncSignals()

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


class ImageSignals(QObject):
    ready = Signal(str, str)                 # url, local path


class ImageJob(QRunnable):
    def __init__(self, url: str, cache: Path, signals: ImageSignals):
        super().__init__()
        self.url, self.cache, self.signals = url, cache, signals

    def run(self) -> None:
        path = image_path(self.cache, self.url)
        try:
            if not path.exists():
                req = urllib.request.Request(_thumb(self.url), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
                    data = r.read()
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(path)
            self.signals.ready.emit(self.url, str(path))
        except Exception:
            pass


def image_path(cache: Path, url: str) -> Path:
    return cache / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".jpg")


def _thumb(url: str) -> str:
    """גרסה קטנה של תמונת המוצר (שרת התמונות של אלי אקספרס תומך בסיומת _200x200.jpg)."""
    return url + "_200x200.jpg" if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) else url
