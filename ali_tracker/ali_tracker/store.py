"""מסד SQLite מקומי: הזמנות, חבילות והקשר ביניהן."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .parser import Item, MailEvent
from .status import DELIVERED, PICKUP, PROBLEM, STAGE, classify

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    items       TEXT NOT NULL DEFAULT '[]',
    total       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    status_text TEXT NOT NULL DEFAULT '',
    last_update TEXT,
    first_seen  TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS packages (
    tracking    TEXT PRIMARY KEY,
    items       TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT '',
    status_text TEXT NOT NULL DEFAULT '',
    last_update TEXT,
    first_seen  TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS package_orders (
    tracking TEXT NOT NULL,
    order_id TEXT NOT NULL,
    PRIMARY KEY (tracking, order_id)
);
CREATE TABLE IF NOT EXISTS seen_messages (message_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class Row:
    """שורה בטבלה: חבילה, או הזמנה שעוד אין לה מספר מעקב."""
    kind: str                       # "package" / "order"
    key: str                        # מספר מעקב או מספר הזמנה
    tracking: str
    order_ids: list[str]
    items: list[Item]
    status: str
    status_text: str
    last_update: datetime | None
    first_seen: datetime
    done: bool
    total: str = ""
    totals: dict[str, str] = field(default_factory=dict)


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- meta
    def get_meta(self, key: str, default: str = "") -> str:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
        self.conn.commit()

    def seen(self, message_id: str) -> bool:
        return bool(message_id) and self.conn.execute(
            "SELECT 1 FROM seen_messages WHERE message_id=?", (message_id,)).fetchone() is not None

    # ---------------------------------------------------------------- עדכון
    def apply(self, ev: MailEvent) -> None:
        """מעדכן את המסד לפי מייל אחד. בטוח להריץ שוב על אותו מייל."""
        if ev.message_id:
            if self.seen(ev.message_id):
                return
            self.conn.execute("INSERT INTO seen_messages VALUES (?)", (ev.message_id,))
        when = _iso(ev.date)
        if ev.seller_message:
            if when > self.get_meta("seller_msg_at"):
                self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('seller_msg_at', ?)", (when,))
            self.conn.commit()
            return

        for o in ev.orders:
            self.conn.execute("INSERT OR IGNORE INTO orders (order_id, first_seen) VALUES (?,?)",
                              (o.order_id, when))
            self.conn.execute("UPDATE orders SET first_seen=? WHERE order_id=? AND first_seen>?",
                              (when, o.order_id, when))
            if o.items:
                self._set_items("orders", "order_id", o.order_id, o.items)
            if o.total:
                self.conn.execute("UPDATE orders SET total=? WHERE order_id=?", (o.total, o.order_id))
            if ev.status and not ev.packages:
                self._set_status("orders", "order_id", o.order_id, ev.status, ev.status_text, when)

        for p in ev.packages:
            self.conn.execute("INSERT OR IGNORE INTO packages (tracking, first_seen) VALUES (?,?)",
                              (p.tracking, when))
            self.conn.execute("UPDATE packages SET first_seen=? WHERE tracking=? AND first_seen>?",
                              (when, p.tracking, when))
            for oid in p.order_ids:
                self.conn.execute("INSERT OR IGNORE INTO orders (order_id, first_seen) VALUES (?,?)",
                                  (oid, when))
                self.conn.execute("INSERT OR IGNORE INTO package_orders VALUES (?,?)", (p.tracking, oid))
            if p.items:
                self._set_items("packages", "tracking", p.tracking, p.items)
            code = classify(p.status_text) if p.status_text else ev.status
            self._set_status("packages", "tracking", p.tracking, code,
                             p.status_text or ev.status_text, when)

        # "how did it go?" על הזמנה: חבילות שעוד בדרך כנראה נמסרו. חבילה שמחכה באיסוף לא
        # משתנה, כי בקשת חוות דעת מגיעה גם כשרק חלק מההזמנה הגיע.
        if ev.status == DELIVERED and not ev.packages:
            for o in ev.orders:
                for t, st in self.conn.execute(
                        """SELECT p.tracking, p.status FROM package_orders po
                           JOIN packages p ON p.tracking = po.tracking WHERE po.order_id=?""",
                        (o.order_id,)).fetchall():
                    if st not in (PICKUP, PROBLEM):
                        self._set_status("packages", "tracking", t, DELIVERED, ev.status_text, when)
        self.conn.commit()

    def _set_items(self, table: str, col: str, key: str, items: list[Item]) -> None:
        cur = json.loads(self.conn.execute(
            f"SELECT items FROM {table} WHERE {col}=?", (key,)).fetchone()[0])
        if len(items) >= len(cur):
            self.conn.execute(f"UPDATE {table} SET items=? WHERE {col}=?",
                              (json.dumps([asdict(i) for i in items], ensure_ascii=False), key))

    def _set_status(self, table: str, col: str, key: str, code: str, text: str, when: str) -> None:
        r = self.conn.execute(f"SELECT status, last_update FROM {table} WHERE {col}=?", (key,)).fetchone()
        old, last = r["status"], r["last_update"]
        if last is None or when >= last:
            self.conn.execute(f"UPDATE {table} SET last_update=? WHERE {col}=?", (when, key))
        if not code:
            return
        newer = last is None or when >= last
        forward = STAGE.get(code, 0) >= STAGE.get(old, 0) or code == PROBLEM or old == PROBLEM
        if (newer and forward) or (not old):
            self.conn.execute(f"UPDATE {table} SET status=?, status_text=? WHERE {col}=?",
                              (code, text, key))

    def set_done(self, row: Row, done: bool) -> None:
        table, col = ("packages", "tracking") if row.kind == "package" else ("orders", "order_id")
        self.conn.execute(f"UPDATE {table} SET done=? WHERE {col}=?", (int(done), row.key))
        self.conn.commit()

    # ---------------------------------------------------------------- קריאה
    def rows(self) -> list[Row]:
        orders = {r["order_id"]: r for r in self.conn.execute("SELECT * FROM orders")}
        links: dict[str, list[str]] = {}
        for t, o in self.conn.execute("SELECT tracking, order_id FROM package_orders ORDER BY order_id"):
            links.setdefault(t, []).append(o)
        linked = {o for os_ in links.values() for o in os_}
        out: list[Row] = []

        for p in self.conn.execute("SELECT * FROM packages"):
            oids = links.get(p["tracking"], [])
            items = [Item(**i) for i in json.loads(p["items"])]
            if not items:
                for oid in oids:
                    if oid in orders:
                        items += [Item(**i) for i in json.loads(orders[oid]["items"])]
            status, text = p["status"], p["status_text"]
            if not status:                       # עוד אין סטטוס לחבילה: לוקחים מההזמנה
                o = next((orders[x] for x in oids if x in orders and orders[x]["status"]), None)
                if o is not None:
                    status, text = o["status"], o["status_text"]
            totals = {oid: orders[oid]["total"] for oid in oids if oid in orders and orders[oid]["total"]}
            out.append(Row("package", p["tracking"], p["tracking"], oids, items, status, text,
                           _dt(p["last_update"]), _dt(p["first_seen"]), bool(p["done"]),
                           " + ".join(totals.values()), totals))

        for oid, o in orders.items():
            if oid in linked:
                continue
            out.append(Row("order", oid, "", [oid], [Item(**i) for i in json.loads(o["items"])],
                           o["status"], o["status_text"], _dt(o["last_update"]),
                           _dt(o["first_seen"]), bool(o["done"]), o["total"],
                           {oid: o["total"]} if o["total"] else {}))
        out.sort(key=lambda r: r.last_update or r.first_seen, reverse=True)
        return out
