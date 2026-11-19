"""הורדת תמונות המוצרים דרך רכיב הרשת של Qt (משתמש באישורי האבטחה של Windows)."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

HEADERS = {
    b"User-Agent": b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   b"(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    b"Accept": b"image/jpeg,image/png,image/gif,image/*;q=0.8",
    b"Referer": b"https://www.aliexpress.com/",
}


def image_path(cache: Path, url: str) -> Path:
    return cache / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".img")


def candidates(url: str) -> list[str]:
    """הכתובת מהמייל, ואם היא לא עובדת גרסה מוקטנת שלה."""
    out = [url]
    if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        out.append(url + "_220x220.jpg")
    if url.startswith("http://"):
        out.insert(0, "https://" + url[7:])
    return out


class ImageLoader(QObject):
    def __init__(self, cache: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.cache = cache
        self.nam = QNetworkAccessManager(self)
        self.waiting: dict[str, list[Callable[[str], None]]] = {}

    def get(self, url: str, callback: Callable[[str], None]) -> None:
        """callback(path) כשהתמונה מוכנה, או callback("") אם לא הצליח."""
        path = image_path(self.cache, url)
        if path.exists():
            callback(str(path))
            return
        if url in self.waiting:
            self.waiting[url].append(callback)
            return
        self.waiting[url] = [callback]
        self._try(url, candidates(url), [])

    def _try(self, url: str, todo: list[str], errors: list[str]) -> None:
        if not todo:
            self._log(url, errors)
            self._finish(url, "")
            return
        req = QNetworkRequest(QUrl(todo[0]))
        for k, v in HEADERS.items():
            req.setRawHeader(k, v)
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                         QNetworkRequest.NoLessSafeRedirectPolicy)
        req.setTransferTimeout(20_000)
        reply = self.nam.get(req)
        reply.finished.connect(lambda: self._done(reply, url, todo, errors))

    def _done(self, reply: QNetworkReply, url: str, todo: list[str], errors: list[str]) -> None:
        reply.deleteLater()
        data = bytes(reply.readAll())
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() != QNetworkReply.NoError:
            errors.append(f"{todo[0]} -> {reply.errorString()} (HTTP {status})")
        elif QImage.fromData(data).isNull():
            kind = reply.header(QNetworkRequest.ContentTypeHeader)
            errors.append(f"{todo[0]} -> not an image Qt can read ({kind}, {len(data)} bytes)")
        else:
            path = image_path(self.cache, url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self._finish(url, str(path))
            return
        self._try(url, todo[1:], errors)

    def _finish(self, url: str, path: str) -> None:
        for cb in self.waiting.pop(url, []):
            try:
                cb(path)
            except RuntimeError:            # התווית כבר נמחקה (עברת לחבילה אחרת)
                pass

    def _log(self, url: str, errors: list[str]) -> None:
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            with open(self.cache / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M} {url}\n" +
                        "".join(f"    {e}\n" for e in errors))
        except OSError:
            pass
