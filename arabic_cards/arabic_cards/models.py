"""מודל הנתונים: כרטיס ומצב החזרה שלו."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Card:
    """כרטיס לימוד.

    arabic       - המילה בכתב ערבי (מהחפיסה, בדרך כלל בכתיב ספרותי)
    translit     - תעתיק עברי שמשקף את ההגייה המדוברת
    hebrew       - התרגום לעברית
    tts_text     - טקסט מותאם שיישלח למנוע ההקראה (אם ריק - משתמשים ב-arabic).
                   כאן אפשר לכתוב את המילה כפי שהיא נהגית במדוברת.
    audio_path   - קובץ שמע קיים (הקלטה אמיתית / קובץ מטמון). עדיף על TTS.
    """
    arabic: str
    translit: str = ""
    hebrew: str = ""
    tts_text: str = ""
    audio_path: str = ""
    tags: str = ""
    source_deck: str = ""
    source_guid: str = ""      # מזהה הפתק המקורי ב-Anki, למניעת כפילויות
    id: int | None = None

    def speak_text(self) -> str:
        return self.tts_text.strip() or self.arabic.strip()


@dataclass
class ReviewState:
    """מצב חזרה מרווחת (SM-2) לכרטיס אחד."""
    card_id: int
    due: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    interval_days: float = 0.0
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0
