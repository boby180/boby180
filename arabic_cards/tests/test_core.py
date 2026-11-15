"""בדיקות תשתית. הרצה: python -m pytest -q   (או: python tests/test_core.py)"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arabic_cards import srs
from arabic_cards.anki_import import notes_to_cards, read_apkg
from arabic_cards.models import Card, ReviewState
from arabic_cards.session import ReviewSession
from arabic_cards.storage import Storage
from arabic_cards.tts import CachedTTS, TTSProvider

FIELDS = ["Arabic", "Transliteration", "Hebrew"]
NOTES = [
    ("g1", "أَنَا", "אַנַא", "אני"),
    ("g2", "<b>هُون</b>", "הוֹן", "כאן [sound:hon.mp3]"),
]


def _make_legacy_apkg(path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "c.anki2"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE col (models TEXT)")
        models = {"111": {"flds": [{"name": n, "ord": i} for i, n in enumerate(FIELDS)]}}
        con.execute("INSERT INTO col VALUES (?)", (json.dumps(models),))
        con.execute("CREATE TABLE notes (guid TEXT, mid INTEGER, flds TEXT, tags TEXT)")
        for g, *f in NOTES:
            con.execute("INSERT INTO notes VALUES (?,?,?,?)", (g, 111, "\x1f".join(f), " basics "))
        con.commit(); con.close()
        with zipfile.ZipFile(path, "w") as z:
            z.write(db, "collection.anki21")
            z.writestr("media", json.dumps({"0": "hon.mp3"}))
            z.writestr("0", b"FAKEMP3")


def _make_new_apkg(path: Path) -> None:
    import zstandard
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "c.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB)")
        for i, n in enumerate(FIELDS):
            con.execute("INSERT INTO fields VALUES (222, ?, ?, x'')", (i, n))
        con.execute("CREATE TABLE notes (guid TEXT, mid INTEGER, flds TEXT, tags TEXT)")
        for g, *f in NOTES:
            con.execute("INSERT INTO notes VALUES (?,?,?,?)", (g, 222, "\x1f".join(f), ""))
        con.commit(); con.close()
        cctx = zstandard.ZstdCompressor()
        name = b"hon.mp3"
        entry = b"\x0a" + bytes([len(name)]) + name + b"\x10\x07"
        media_pb = b"\x0a" + bytes([len(entry)]) + entry
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("collection.anki21b", cctx.compress(db.read_bytes()))
            z.writestr("media", cctx.compress(media_pb))
            z.writestr("0", cctx.compress(b"FAKEMP3"))


def _check_import(maker) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        apkg = tmp / "deck.apkg"
        maker(apkg)
        notes = read_apkg(apkg, media_out=tmp / "media")
        assert len(notes) == 2 and list(notes[0].fields) == FIELDS
        cards = notes_to_cards(notes, "deck", media_dir=tmp / "media")
        assert cards[0].arabic == "أَنَا" and cards[0].hebrew == "אני"
        assert cards[1].arabic == "هُون"            # HTML הוסר
        assert cards[1].hebrew == "כאן"             # [sound:] הוסר
        assert cards[1].audio_path.endswith("hon.mp3")
        assert (tmp / "media" / "hon.mp3").read_bytes() == b"FAKEMP3"

        db = Storage(tmp / "t.db")
        assert sum(db.add_card(c) is not None for c in cards) == 2
        assert sum(db.add_card(c) is not None for c in cards) == 0   # אין כפילויות
        assert len(db.due_card_ids()) == 2
        db.close()


def test_import_legacy():
    _check_import(_make_legacy_apkg)


def test_import_new_format():
    _check_import(_make_new_apkg)


def test_srs():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    st = ReviewState(card_id=1, due=now)
    st = srs.schedule(st, srs.GOOD, now)
    assert st.interval_days == 1
    st = srs.schedule(st, srs.GOOD, now)
    assert st.interval_days == 2.5
    st = srs.schedule(st, srs.AGAIN, now)
    assert st.interval_days == 0 and st.lapses == 1 and st.ease < 2.5


def _db_with_cards(tmp: Path, n: int) -> Storage:
    db = Storage(tmp / "s.db")
    for i in range(n):
        db.add_card(Card(arabic=f"كلمة{i}", translit=f"מילה{i}", hebrew=f"תרגום{i}"))
    return db


def test_session():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db_with_cards(Path(tmp), 5)
        s = ReviewSession(db, new_per_day=3)
        assert s.counts() == {"new": 3, "review": 0, "again": 0}
        first = s.current().id
        assert s.preview()[srs.GOOD] == "יום" and s.preview()[srs.AGAIN] == "דקה"
        s.answer(srs.AGAIN)                       # חוזר לסוף התור באותו סבב
        assert s.counts() == {"new": 2, "review": 0, "again": 1}
        s.answer(srs.GOOD); s.answer(srs.EASY)
        assert s.current().id == first
        s.answer(srs.GOOD)
        assert s.current() is None and s.answered == 3

        # המכסה היומית נשמרת גם בסבב חדש, ו-extra_new מוסיף מעבר לה
        assert db.new_done_today() == 3
        assert ReviewSession(db, new_per_day=3).current() is None
        assert ReviewSession(db, new_per_day=3, extra_new=1).counts()["new"] == 1
        assert db.counts() == (0, 2)
        db.close()


def test_search_and_delete():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db_with_cards(Path(tmp), 3)
        assert len(db.search("")) == 3
        (card, state), = db.search("תרגום1")
        assert card.arabic == "كلمة1" and state.reps == 0
        s = ReviewSession(db)
        db.delete_card(s.queue[0])                # כרטיס שנמחק באמצע סבב מדולג
        assert s.current().id == 2 and db.count() == 2
        db.close()


def test_tts_cache_no_partial_file():
    class Broken(TTSProvider):
        name = "broken"
        def synthesize(self, text, out_path, voice):
            out_path.write_bytes(b"half")
            raise OSError("no network")

    class Fake(TTSProvider):
        name = "fake"
        calls = 0
        def synthesize(self, text, out_path, voice):
            Fake.calls += 1
            out_path.write_bytes(b"MP3")
            return out_path

    with tempfile.TemporaryDirectory() as tmp:
        try:
            CachedTTS(Broken(), tmp, "v").get("x")
            raise AssertionError("expected OSError")
        except OSError:
            pass
        assert list(Path(tmp).iterdir()) == []    # לא נשאר קובץ חלקי במטמון
        tts = CachedTTS(Fake(), tmp, "v")
        assert tts.get("x") == tts.get("x") and Fake.calls == 1


def test_describe_interval():
    assert [srs.describe_interval(d) for d in (0, 1, 2, 2.5, 45, 400)] == \
        ["דקה", "יום", "יומיים", "יומיים", "1.5 חודשים", "שנה"]


def test_config_ignores_unknown_keys(monkeypatch=None):
    from arabic_cards import config
    with tempfile.TemporaryDirectory() as tmp:
        old, config.CONFIG_FILE = config.CONFIG_FILE, Path(tmp) / "config.json"
        try:
            config.CONFIG_FILE.write_text('{"voice": "ar-LB-RamiNeural", "old_key": 1}')
            assert config.Config.load().voice == "ar-LB-RamiNeural"
        finally:
            config.CONFIG_FILE = old


if __name__ == "__main__":
    for fn in (test_import_legacy, test_import_new_format, test_srs, test_session,
               test_search_and_delete, test_tts_cache_no_partial_file,
               test_describe_interval, test_config_ignores_unknown_keys):
        fn(); print("OK", fn.__name__)
