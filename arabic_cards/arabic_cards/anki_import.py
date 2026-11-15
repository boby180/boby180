"""ייבוא חפיסות Anki (.apkg).

תומך בשלושת הפורמטים:
  collection.anki2   - ישן
  collection.anki21  - Anki 2.1
  collection.anki21b - חדש, מכווץ ב-zstd (סכמה 18, שמות שדות בטבלה נפרדת)
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import Card

try:
    import zstandard
except ImportError:  # נדרש רק לפורמט anki21b
    zstandard = None

FIELD_SEP = "\x1f"
_SOUND_RE = re.compile(r"\[sound:([^\]]+)\]")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class RawNote:
    guid: str
    fields: dict[str, str]      # שם שדה -> תוכן גולמי
    tags: str


def clean(text: str) -> str:
    """מסיר HTML ו-[sound:..] ומחזיר טקסט נקי."""
    text = _SOUND_RE.sub("", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def sounds_in(text: str) -> list[str]:
    return _SOUND_RE.findall(text)


# ---------------------------------------------------------------- קריאה גולמית
def _decompress(data: bytes) -> bytes:
    if zstandard is None:
        raise RuntimeError("הקובץ בפורמט חדש. התקן: pip install zstandard")
    return zstandard.ZstdDecompressor().decompressobj().decompress(data)


def _is_zstd(data: bytes) -> bool:
    return data[:4] == b"\x28\xb5\x2f\xfd"


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _parse_media_protobuf(buf: bytes) -> dict[str, str]:
    """פענוח מינימלי של קובץ media בפורמט החדש (protobuf MediaEntries).
    מחזיר {"0": "file.mp3", ...} כמו בפורמט ה-JSON הישן."""
    out: dict[str, str] = {}
    i, idx = 0, 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        if key >> 3 == 1 and key & 7 == 2:          # repeated MediaEntry
            ln, i = _read_varint(buf, i)
            entry, i = buf[i:i + ln], i + ln
            j, name = 0, ""
            while j < len(entry):
                k, j = _read_varint(entry, j)
                wt = k & 7
                if wt == 2:
                    l2, j = _read_varint(entry, j)
                    if k >> 3 == 1:
                        name = entry[j:j + l2].decode("utf-8")
                    j += l2
                elif wt == 0:
                    _, j = _read_varint(entry, j)
                else:
                    break
            out[str(idx)] = name
            idx += 1
        else:
            break
    return out


def _field_names(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """notetype id -> שמות השדות לפי הסדר."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fields" in tables:                           # סכמה 18
        res: dict[int, list[str]] = {}
        for ntid, _ord, name in conn.execute("SELECT ntid, ord, name FROM fields ORDER BY ntid, ord"):
            res.setdefault(ntid, []).append(name)
        return res
    models = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
    return {int(mid): [f["name"] for f in sorted(m["flds"], key=lambda f: f["ord"])]
            for mid, m in models.items()}


def read_apkg(path: str | Path, media_out: str | Path | None = None) -> list[RawNote]:
    """קורא קובץ apkg ומחזיר רשימת פתקים גולמיים.
    אם media_out ניתן - קבצי המדיה (שמע) יחולצו לשם."""
    path = Path(path)
    with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
        names = set(z.namelist())
        for cand in ("collection.anki21b", "collection.anki21", "collection.anki2"):
            if cand in names:
                break
        else:
            raise ValueError("לא נמצא מסד נתונים בקובץ ה-apkg")

        data = z.read(cand)
        if cand.endswith("anki21b"):
            data = _decompress(data)
        db_file = Path(tmp) / "col.sqlite"
        db_file.write_bytes(data)

        conn = sqlite3.connect(db_file)
        try:
            fmap = _field_names(conn)
            notes = []
            for guid, mid, flds, tags in conn.execute("SELECT guid, mid, flds, tags FROM notes"):
                vals = flds.split(FIELD_SEP)
                fnames = fmap.get(mid) or [f"field{i}" for i in range(len(vals))]
                notes.append(RawNote(guid, dict(zip(fnames, vals)), tags.strip()))
        finally:
            conn.close()

        if media_out is not None and "media" in names:
            raw = z.read("media")
            if _is_zstd(raw):
                media_map = _parse_media_protobuf(_decompress(raw))
            else:
                media_map = json.loads(raw.decode("utf-8") or "{}")
            out = Path(media_out)
            out.mkdir(parents=True, exist_ok=True)
            for num, fname in media_map.items():
                if num in names and fname:
                    blob = z.read(num)
                    if _is_zstd(blob):
                        blob = _decompress(blob)
                    (out / fname).write_bytes(blob)
    return notes


# ---------------------------------------------------------------- המרה לכרטיסים
_HEB = re.compile(r"[֐-׿]")
_ARB = re.compile(r"[؀-ۿ]")


def guess_mapping(field_names: list[str]) -> dict[str, str]:
    """מנחש איזה שדה הוא ערבית/תעתיק/עברית לפי השם."""
    m: dict[str, str] = {}
    for f in field_names:
        low = f.lower()
        if "translit" in low or "תעתיק" in f:
            m.setdefault("translit", f)
        elif "arab" in low or "ערבית" in f:
            m.setdefault("arabic", f)
        elif "hebr" in low or "עברית" in f:
            m.setdefault("hebrew", f)
    return m


def notes_to_cards(notes: list[RawNote], deck_name: str,
                   mapping: dict[str, str] | None = None,
                   media_dir: str | Path | None = None) -> list[Card]:
    cards = []
    for n in notes:
        mp = mapping or guess_mapping(list(n.fields))
        arabic = clean(n.fields.get(mp.get("arabic", ""), ""))
        translit = clean(n.fields.get(mp.get("translit", ""), ""))
        hebrew = clean(n.fields.get(mp.get("hebrew", ""), ""))

        if not arabic:   # גיבוי: השדה הראשון שיש בו אותיות ערביות
            arabic = next((clean(v) for v in n.fields.values() if _ARB.search(v)), "")
        if not arabic:
            continue

        audio = ""
        if media_dir:
            for v in n.fields.values():
                for s in sounds_in(v):
                    p = Path(media_dir) / s
                    if p.exists():
                        audio = str(p)
                        break
                if audio:
                    break

        cards.append(Card(arabic=arabic, translit=translit, hebrew=hebrew,
                          audio_path=audio, tags=n.tags,
                          source_deck=deck_name, source_guid=n.guid))
    return cards
