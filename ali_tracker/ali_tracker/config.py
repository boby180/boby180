"""הגדרות. נשמרות ב-config.json ליד התוכנה."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"


@dataclass
class Config:
    data_dir: str = str(ROOT / "data")
    gmail: str = ""
    app_password: str = ""          # מוצפן (secret.protect)
    days_back: int = 120
    auto_sync_minutes: int = 30

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "packages.db"

    @property
    def image_dir(self) -> Path:
        return Path(self.data_dir) / "images"

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.exists():
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                return cls(**{k: v for k, v in raw.items() if k in known})
            except (ValueError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        CONFIG_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
