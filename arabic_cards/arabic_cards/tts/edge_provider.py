"""ספק הקראה חינמי דרך edge-tts (הקולות של Microsoft Edge, אותם קולות כמו Azure).

הערה: זה ממשק לא רשמי. אם יפסיק לעבוד - נעבור ל-Azure Speech הרשמי
(יש מכסה חינמית חודשית) בלי לשנות את שאר הקוד.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .base import TTSProvider

# קולות לבנטיניים - הכי קרובים לערבית פלסטינית
LEVANTINE_VOICES = {
    "jordan_f": "ar-JO-SanaNeural",
    "jordan_m": "ar-JO-TaimNeural",
    "lebanon_f": "ar-LB-LaylaNeural",
    "lebanon_m": "ar-LB-RamiNeural",
    "syria_f": "ar-SY-AmanyNeural",
    "syria_m": "ar-SY-LaithNeural",
}


class EdgeTTS(TTSProvider):
    name = "edge"

    def __init__(self, rate: str = "-10%"):
        self.rate = rate   # קצב איטי מעט - נוח ללמידה
        self.name = f"edge|{rate}"   # הקצב חלק ממפתח המטמון

    def synthesize(self, text: str, out_path: Path, voice: str) -> Path:
        import edge_tts

        async def _run():
            await edge_tts.Communicate(text, voice, rate=self.rate).save(str(out_path))

        asyncio.run(_run())
        return out_path
