"""מודל הנתונים: כרטיס ומצב החזרה שלו."""
from __future__ import annotations

import re
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
        return self.tts_text.strip() or spoken_form(self.arabic)


@dataclass
class ReviewState:
    """מצב חזרה מרווחת (SM-2) לכרטיס אחד."""
    card_id: int
    due: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    interval_days: float = 0.0
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0


_BRACKETS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_NOT_ARABIC = re.compile(r"[^\u0600-\u06FF\s]+")


def spoken_form(arabic: str) -> str:
    """מה שמקריאים מתוך שדה הערבית כשאין tts_text.

    בחפיסות יש בשדה גם הערות: "هُنَا / هَهُنَا", "شُو (מד'; =...)", "سَاكِن [سُكَّان / ...]".
    מקריאים רק את הצורה הראשונה, בלי סוגריים, בלי חלופות ובלי אותיות שאינן ערביות.
    """
    text = arabic
    while True:                                   # סוגריים מקוננים, מבפנים החוצה
        cleaned = _BRACKETS.sub(" ", text)
        if cleaned == text:
            break
        text = cleaned
    text = re.split(r"[(\[]", text)[0]            # סוגר שנפתח ולא נסגר
    text = text.split("/")[0]
    text = _NOT_ARABIC.sub(" ", text)
    return " ".join(text.split()) or arabic.strip()
