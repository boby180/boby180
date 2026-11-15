"""הגדרות. אפשר לדרוס דרך קובץ config.json בתיקיית הפרויקט."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

if getattr(sys, "frozen", False):
    # קובץ EXE: הנתונים וההגדרות נשמרים ליד הקובץ, לא בתיקייה הזמנית שממנה הוא רץ
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"


@dataclass
class Config:
    data_dir: str = str(ROOT / "data")
    voice: str = "ar-JO-TaimNeural"
    tts_rate: str = "-10%"
    new_cards_per_day: int = 20
    direction: str = "ar2he"      # ar2he = ערבית בחזית, he2ar = עברית בחזית
    autoplay: bool = True         # השמעה אוטומטית כשהכרטיס מוצג

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "cards.db"

    @property
    def audio_cache(self) -> Path:
        return Path(self.data_dir) / "audio_cache"

    @property
    def media_dir(self) -> Path:
        return Path(self.data_dir) / "media"

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.exists():
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in raw.items() if k in known})
        return cls()

    def save(self) -> None:
        CONFIG_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                               encoding="utf-8")
