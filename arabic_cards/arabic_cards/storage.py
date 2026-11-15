"""שמירת כרטיסים ומצב חזרה ב-SQLite מקומי."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Card, ReviewState

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    arabic      TEXT NOT NULL,
    translit    TEXT DEFAULT '',
    hebrew      TEXT DEFAULT '',
    tts_text    TEXT DEFAULT '',
    audio_path  TEXT DEFAULT '',
    tags        TEXT DEFAULT '',
    source_deck TEXT DEFAULT '',
    source_guid TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cards_guid
    ON cards(source_deck, source_guid) WHERE source_guid <> '';

CREATE TABLE IF NOT EXISTS reviews (
    card_id       INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    due           TEXT NOT NULL,
    interval_days REAL NOT NULL DEFAULT 0,
    ease          REAL NOT NULL DEFAULT 2.5,
    reps          INTEGER NOT NULL DEFAULT 0,
    lapses        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id   INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    grade     INTEGER NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------- cards ----------
    def add_card(self, card: Card) -> int | None:
        """מוסיף כרטיס. מחזיר id, או None אם כבר קיים (לפי guid)."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO cards
               (arabic, translit, hebrew, tts_text, audio_path, tags,
                source_deck, source_guid, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (card.arabic, card.translit, card.hebrew, card.tts_text,
             card.audio_path, card.tags, card.source_deck,
             card.source_guid, _now()),
        )
        if cur.rowcount == 0:
            return None
        card_id = cur.lastrowid
        self.conn.execute(
            "INSERT INTO reviews (card_id, due) VALUES (?, ?)", (card_id, _now())
        )
        self.conn.commit()
        return card_id

    def update_card(self, card: Card) -> None:
        self.conn.execute(
            """UPDATE cards SET arabic=?, translit=?, hebrew=?, tts_text=?,
               audio_path=?, tags=? WHERE id=?""",
            (card.arabic, card.translit, card.hebrew, card.tts_text,
             card.audio_path, card.tags, card.id),
        )
        self.conn.commit()

    def get_card(self, card_id: int) -> Card | None:
        row = self.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return _row_to_card(row) if row else None

    def all_cards(self) -> list[Card]:
        return [_row_to_card(r) for r in self.conn.execute("SELECT * FROM cards ORDER BY id")]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    def delete_card(self, card_id: int) -> None:
        self.conn.execute("DELETE FROM review_log WHERE card_id=?", (card_id,))
        self.conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
        self.conn.commit()

    def search(self, text: str = "") -> list[tuple[Card, ReviewState]]:
        """כרטיסים (עם מצב החזרה שלהם) שהטקסט מופיע באחד השדות שלהם."""
        like = f"%{text.strip()}%"
        rows = self.conn.execute(
            """SELECT c.*, r.due, r.interval_days, r.ease, r.reps, r.lapses
               FROM cards c JOIN reviews r ON r.card_id = c.id
               WHERE c.arabic LIKE ?1 OR c.translit LIKE ?1 OR c.hebrew LIKE ?1
                  OR c.tts_text LIKE ?1
               ORDER BY c.id""", (like,))
        return [(_row_to_card(r), _row_to_state(r["id"], r)) for r in rows]

    # ---------- reviews ----------
    def get_state(self, card_id: int) -> ReviewState:
        r = self.conn.execute("SELECT * FROM reviews WHERE card_id=?", (card_id,)).fetchone()
        return _row_to_state(card_id, r)

    def save_state(self, st: ReviewState, grade: int) -> None:
        self.conn.execute(
            """UPDATE reviews SET due=?, interval_days=?, ease=?, reps=?, lapses=?
               WHERE card_id=?""",
            (st.due.isoformat(), st.interval_days, st.ease, st.reps, st.lapses, st.card_id),
        )
        self.conn.execute(
            "INSERT INTO review_log (card_id, ts, grade) VALUES (?,?,?)",
            (st.card_id, _now(), grade),
        )
        self.conn.commit()

    def due_card_ids(self, limit: int = 50, new_limit: int = 20) -> list[int]:
        """כרטיסים שהגיע זמנם: קודם חזרות, אחר כך עד new_limit כרטיסים חדשים."""
        now = _now()
        review = [r[0] for r in self.conn.execute(
            "SELECT card_id FROM reviews WHERE reps>0 AND due<=? ORDER BY due LIMIT ?",
            (now, limit))]
        new = [r[0] for r in self.conn.execute(
            "SELECT card_id FROM reviews WHERE reps=0 ORDER BY card_id LIMIT ?",
            (new_limit,))]
        return review + new

    def counts(self) -> tuple[int, int]:
        """(חזרות שהגיע זמנן, כרטיסים חדשים שעוד לא נלמדו)."""
        due = self.conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE reps>0 AND due<=?", (_now(),)).fetchone()[0]
        new = self.conn.execute("SELECT COUNT(*) FROM reviews WHERE reps=0").fetchone()[0]
        return due, new

    def new_done_today(self, now: datetime | None = None) -> int:
        """כמה כרטיסים חדשים נלמדו לראשונה היום (לפי השעון המקומי)."""
        local = (now or datetime.now(timezone.utc)).astimezone()
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.conn.execute(
            """SELECT COUNT(*) FROM
               (SELECT MIN(ts) AS first FROM review_log GROUP BY card_id)
               WHERE first >= ?""",
            (start.astimezone(timezone.utc).isoformat(),)).fetchone()[0]


def _row_to_state(card_id: int, r: sqlite3.Row) -> ReviewState:
    return ReviewState(
        card_id=card_id,
        due=datetime.fromisoformat(r["due"]),
        interval_days=r["interval_days"],
        ease=r["ease"],
        reps=r["reps"],
        lapses=r["lapses"],
    )


def _row_to_card(r: sqlite3.Row) -> Card:
    return Card(
        id=r["id"], arabic=r["arabic"], translit=r["translit"], hebrew=r["hebrew"],
        tts_text=r["tts_text"], audio_path=r["audio_path"], tags=r["tags"],
        source_deck=r["source_deck"], source_guid=r["source_guid"],
    )
