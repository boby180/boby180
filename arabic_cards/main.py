"""נקודת הכניסה. בלי פרמטרים נפתח הממשק הגרפי; עם פקודה - כלי שורת פקודה.

דוגמאות:
  python main.py                  # הממשק הגרפי
  python main.py import "Speaking_Arabic_Palestinian.apkg"
  python main.py list
  python main.py say 5            # מקריא כרטיס 5
  python main.py voices "مرحبا"   # משווה את כל הקולות הלבנטיניים
  python main.py review           # חזרה בסיסית בטרמינל
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from arabic_cards import srs
from arabic_cards.anki_import import notes_to_cards, read_apkg
from arabic_cards.config import Config
from arabic_cards.storage import Storage
from arabic_cards.tts import LEVANTINE_VOICES, CachedTTS, EdgeTTS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def play(path: Path) -> None:
    """ניגון זמני: פותח בנגן ברירת המחדל. בממשק הגרפי נחליף בניגון פנימי."""
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        print(f"[audio] {path}")


def cmd_import(cfg: Config, args) -> None:
    apkg = Path(args.file)
    notes = read_apkg(apkg, media_out=cfg.media_dir)
    cards = notes_to_cards(notes, deck_name=apkg.stem, media_dir=cfg.media_dir)
    db = Storage(cfg.db_path)
    added = sum(1 for c in cards if db.add_card(c) is not None)
    print(f"נמצאו {len(notes)} פתקים, נוספו {added} כרטיסים חדשים (סה\"כ {db.count()}).")
    if notes:
        print("שדות בחפיסה:", list(notes[0].fields))


def cmd_list(cfg: Config, args) -> None:
    for c in Storage(cfg.db_path).all_cards()[: args.n]:
        print(f"{c.id:4d} | {c.arabic:<20} | {c.translit:<20} | {c.hebrew}")


def _tts(cfg: Config, voice: str | None = None) -> CachedTTS:
    return CachedTTS(EdgeTTS(rate=cfg.tts_rate), cfg.audio_cache, voice or cfg.voice)


def cmd_say(cfg: Config, args) -> None:
    card = Storage(cfg.db_path).get_card(args.id)
    if not card:
        sys.exit("כרטיס לא נמצא")
    path = Path(card.audio_path) if card.audio_path else _tts(cfg).get(card.speak_text())
    print(f"{card.arabic}  ({card.translit})  =  {card.hebrew}")
    play(path)


def cmd_voices(cfg: Config, args) -> None:
    for key, voice in LEVANTINE_VOICES.items():
        p = _tts(cfg, voice).get(args.text)
        print(f"{key:10} {voice:22} -> {p}")
        play(p)
        input("Enter לקול הבא...")


def cmd_review(cfg: Config, args) -> None:
    db = Storage(cfg.db_path)
    tts = _tts(cfg)
    for cid in db.due_card_ids(new_limit=cfg.new_cards_per_day):
        card = db.get_card(cid)
        print("\n" + "=" * 40 + f"\n  {card.arabic}")
        play(Path(card.audio_path) if card.audio_path else tts.get(card.speak_text()))
        input("  [Enter] להצגת התשובה")
        print(f"  {card.translit}  =  {card.hebrew}")
        g = input("  1=Again 2=Hard 3=Good 4=Easy  q=יציאה: ").strip()
        if g == "q":
            break
        if g in "1234" and g:
            st = srs.schedule(db.get_state(cid), int(g))
            db.save_state(st, int(g))


def cmd_gui(cfg: Config, args) -> None:
    try:
        from arabic_cards.gui import run
    except ImportError as e:
        sys.exit(f"חסרה ספרייה לממשק הגרפי ({e}). הרץ: pip install -r requirements.txt")
    sys.exit(run())


def main() -> None:
    cfg = Config.load()
    if len(sys.argv) == 1:
        cmd_gui(cfg, None)
    p = argparse.ArgumentParser(description="Arabic Cards")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("list"); s.add_argument("-n", type=int, default=30); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("say"); s.add_argument("id", type=int); s.set_defaults(fn=cmd_say)
    s = sub.add_parser("voices"); s.add_argument("text"); s.set_defaults(fn=cmd_voices)
    s = sub.add_parser("review"); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("gui"); s.set_defaults(fn=cmd_gui)
    args = p.parse_args()
    args.fn(cfg, args)


if __name__ == "__main__":
    main()
