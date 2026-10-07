"""SQLite cache of scan results, keyed by path + size + modification time."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ScanCache:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS files (
                   path TEXT PRIMARY KEY,
                   size INTEGER NOT NULL,
                   mtime REAL NOT NULL,
                   version TEXT NOT NULL,
                   data TEXT NOT NULL
               )"""
        )

    def get(self, path: str, size: int, mtime: float, version: str) -> dict | None:
        row = self._conn.execute(
            "SELECT size, mtime, version, data FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is None or row[0] != size or abs(row[1] - mtime) > 1e-3 or row[2] != version:
            return None
        return json.loads(row[3])

    def put(self, path: str, size: int, mtime: float, version: str, data: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO files (path, size, mtime, version, data) VALUES (?, ?, ?, ?, ?)",
            (path, size, mtime, version, json.dumps(data, ensure_ascii=False)),
        )

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> "ScanCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
