"""ניגון שמע פנימי + יצירת הקראה ברקע, כדי שהחלון לא ייתקע בזמן ההמתנה לרשת."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from ..config import Config
from ..models import Card
from ..tts import CachedTTS, EdgeTTS


class _Signals(QObject):
    done = Signal(int, str, str)        # token, path, error


class _Job(QRunnable):
    def __init__(self, tts: CachedTTS, text: str, token: int, signals: _Signals):
        super().__init__()
        self.tts, self.text, self.token, self.signals = tts, text, token, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(self.token, str(self.tts.get(self.text)), "")
        except Exception as e:                      # אין רשת, קול לא קיים וכו'
            self.signals.done.emit(self.token, "", f"{type(e).__name__}: {e}")


class AudioService(QObject):
    failed = Signal(str)                # הודעת שגיאה למשתמש

    def __init__(self, cfg: Config, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self._token = 0
        self._pool = QThreadPool(self)
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._out = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._out)
        self._player.errorOccurred.connect(
            lambda _err, msg: self.failed.emit(f"שגיאת ניגון: {msg}"))

    def speak_card(self, card: Card) -> None:
        """הקלטה אמיתית אם יש, אחרת הקראה של tts_text / הכתיב הערבי."""
        if card.audio_path and Path(card.audio_path).exists():
            self._token += 1
            self.play_file(card.audio_path)
        else:
            self.speak_text(card.speak_text())

    def speak_text(self, text: str, voice: str | None = None) -> None:
        text = text.strip()
        if not text:
            return
        self._token += 1                # תוצאה של בקשה ישנה לא תנוגן
        tts = CachedTTS(EdgeTTS(rate=self.cfg.tts_rate), self.cfg.audio_cache,
                        voice or self.cfg.voice)
        self._pool.start(_Job(tts, text, self._token, self._signals))

    def play_file(self, path: str | Path) -> None:
        self._player.stop()
        self._player.setSource(QUrl())  # מאפשר לנגן שוב את אותו קובץ
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def stop(self) -> None:
        self._token += 1
        self._player.stop()

    def _on_done(self, token: int, path: str, error: str) -> None:
        if token != self._token:
            return
        if error:
            self.failed.emit(f"ההקראה נכשלה (נדרש חיבור לאינטרנט). {error}")
        else:
            self.play_file(path)
