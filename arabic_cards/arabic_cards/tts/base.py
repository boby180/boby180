"""ממשק אחיד למנועי הקראה + מטמון קבצים, כדי שכל מילה תיווצר פעם אחת בלבד."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def synthesize(self, text: str, out_path: Path, voice: str) -> Path:
        """יוצר קובץ שמע (mp3) מהטקסט ומחזיר את הנתיב."""


class CachedTTS:
    """עוטף ספק TTS ושומר כל תוצאה בתיקיית מטמון."""

    def __init__(self, provider: TTSProvider, cache_dir: str | Path, voice: str):
        self.provider = provider
        self.voice = voice
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, text: str) -> str:
        h = hashlib.sha1(f"{self.provider.name}|{self.voice}|{text}".encode()).hexdigest()
        return h[:16]

    def get(self, text: str) -> Path:
        path = self.cache_dir / f"{self._key(text)}.mp3"
        if not path.exists() or path.stat().st_size == 0:
            part = path.with_suffix(".part")
            try:
                self.provider.synthesize(text, part, self.voice)
                if part.stat().st_size == 0:
                    raise RuntimeError("מנוע ההקראה החזיר קובץ ריק")
                part.replace(path)
            finally:
                part.unlink(missing_ok=True)
        return path
