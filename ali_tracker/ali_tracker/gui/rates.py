"""בדיקת שער הדולר והיורו מבנק ישראל, ברקע, דרך רכיב הרשת של Qt (אישורי האבטחה של Windows)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import rates


class RateFetcher(QObject):
    done = Signal(object)        # {'USD': (rate, 'YYYY-MM-DD'), 'EUR': ...}
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.nam = QNetworkAccessManager(self)
        self.busy = False
        self.json_url = rates.JSON_URL
        self.csv_url = rates.CSV_URL

    def fetch(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._get(self.json_url, self._json_done)

    def _get(self, url: str, then) -> None:
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"Accept", b"application/json, text/csv;q=0.9, */*;q=0.5")
        req.setRawHeader(b"User-Agent", b"AliTracker")
        req.setTransferTimeout(20_000)
        reply = self.nam.get(req)
        reply.finished.connect(lambda: then(reply))

    @staticmethod
    def _read(reply: QNetworkReply) -> tuple[str, str]:
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            return "", reply.errorString()
        return bytes(reply.readAll()).decode("utf-8", "replace"), ""

    def _json_done(self, reply: QNetworkReply) -> None:
        text, err = self._read(reply)
        got: dict = {}
        if text:
            try:
                got = {k: v for k, v in rates.parse_json(text).items() if k in rates.CURRENCIES}
            except ValueError:
                err = "תשובה לא צפויה מבנק ישראל"
        if "USD" in got:
            self._finish(got, "")
        else:                                   # גיבוי: סדרות SDMX, מטבע אחרי מטבע
            self._csv(list(rates.CURRENCIES), got, err)

    def _csv(self, todo: list[str], got: dict, err: str) -> None:
        todo = [c for c in todo if c not in got]
        if not todo:
            self._finish(got, err)
            return
        cur = todo[0]

        def then(reply: QNetworkReply) -> None:
            text, e = self._read(reply)
            r = rates.parse_csv(text) if text else None
            if r:
                got[cur] = r
            self._csv(todo[1:], got, err or e)

        self._get(self.csv_url.format(cur=cur), then)

    def _finish(self, got: dict, err: str) -> None:
        self.busy = False
        if "USD" in got:
            self.done.emit(got)
        else:
            self.failed.emit(err or "בנק ישראל לא החזיר שער")
