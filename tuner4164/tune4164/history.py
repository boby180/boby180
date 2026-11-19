"""Calibration memory: every analysis is kept per axis, and the next recording is compared with the
previous one (what changed in the PRM, was the last advice applied, did the motion get better).

Stored as JSON in the user's application data folder, so it survives closing the program."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .advise import Advice, Change
from .analysis import Analysis

# key, label, unit, relative and absolute change that counts, weight in the verdict
METRICS = [
    ("settle", "התייצבות אחרי העצירה", "s", 0.10, 0.05, 2.0),
    ("stuck_time", "זמן שהציר תקוע (חיכוך)", "s", 0.15, 0.10, 2.0),
    ("stuck_error", "שגיאה כשהציר תקוע", "ספירות", 0.15, 3, 1.0),
    ("end_peak", "שיא שגיאה אחרי העצירה", "ספירות", 0.10, 3, 1.0),
    ("final_error", "שגיאה סופית בעמידה", "ספירות", 0.25, 3, 1.0),
    ("oscillations", "תנודות אחרי העצירה", "", 0.25, 1, 1.0),
    ("following", "פיגור בזמן נסיעה", "ספירות", 0.10, 3, 1.0),
    ("ripple", "אדוות בזמן נסיעה", "ספירות", 0.15, 1, 0.5),
    ("tacho_err", "סטיית חיישן המהירות", "%", 0.25, 0.3, 1.0),
    ("cmd_noise", "רעש בפקודת הדרייבר", "", 0.25, 0.03, 0.5),
    ("overshoot", "חריגה בצעד", "%", 0.15, 2, 1.0),
    ("step_settle", "התייצבות בצעד", "s", 0.10, 0.02, 1.0),
]
NOT_SETTLED = math.inf  # "did not settle" / "still stuck" compares as the worst value


def metrics(an: Analysis) -> dict:
    m: dict = {}
    if an.tacho and an.tacho.ok:
        m["tacho_err"] = abs(abs(an.tacho.gain) - 1.0) * 100
    if an.command and an.command.ok:
        m["cmd_noise"] = an.command.noise_ratio
    mv = an.move
    if mv and mv.ok:
        fol = [abs(x) for x in (mv.following_pos, mv.following_neg) if x is not None]
        if fol:
            m["following"] = max(fol)
        if mv.cruise_ripple is not None:
            m["ripple"] = mv.cruise_ripple
        if mv.has_stop:
            m["settle"] = NOT_SETTLED if mv.settle_after_end is None else mv.settle_after_end
            m["end_peak"] = mv.end_error_peak
            m["final_error"] = abs(mv.final_error)
            m["oscillations"] = mv.oscillations
            m["stuck_time"] = (NOT_SETTLED if mv.stuck_time is None else mv.stuck_time) if mv.stick else 0.0
            m["stuck_error"] = abs(mv.stuck_error) if mv.stick else 0.0
    s = an.step
    if s and s.ok:
        m["overshoot"] = s.overshoot_pct
        m["step_settle"] = NOT_SETTLED if s.settling_time is None else s.settling_time
    return m


def _json_num(v):
    return None if v is None else ("inf" if v == math.inf else v)


def _from_json(v):
    return math.inf if v == "inf" else v


def make_entry(axis: str, test: str, gains: dict, an: Analysis, advice: Advice, rec_key: str,
               rec_name: str, prm_name: str, top_speed: float | None) -> dict:
    return {
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "axis": axis, "test": test, "rec_key": rec_key, "recording": rec_name, "prm": prm_name,
        "gains": {k: v for k, v in gains.items() if v is not None},
        "metrics": {k: _json_num(v) for k, v in metrics(an).items()},
        "top_speed": top_speed,
        **advice_fields(advice),
        "verdict": None,
    }


def advice_fields(advice: Advice) -> dict:
    """The change that was recommended now (the one the next recording will be judged against)."""
    return {"advice": [{"param": k, "old": o, "new": n} for c in advice.changes[:1] for k, o, n in c.items()],
            "advice_pct": advice.changes[0].percent if advice.changes else None,
            # every suggestion of this run, in order (the first one is "do this now")
            "advice_all": [{"items": [{"param": k, "old": o, "new": n} for k, o, n in c.items()],
                            "pct": c.percent, "reason": c.reason} for c in advice.changes]}


@dataclass
class Comparison:
    prev: dict
    rows: list = field(default_factory=list)      # (label, unit, before, after, +1 better / -1 worse / 0)
    changed: list = field(default_factory=list)   # (param, before, now)
    applied: bool | None = None                   # last advice applied exactly?
    not_applied: list = field(default_factory=list)
    verdict: str = "same"                         # better | worse | mixed | same
    score: float = 0.0
    notes: list = field(default_factory=list)

    @property
    def verdict_text(self) -> str:
        return {"better": "השתפר", "worse": "החמיר", "mixed": "מעורב: חלק השתפר וחלק החמיר",
                "same": "בלי שינוי משמעותי"}[self.verdict]


def compare(prev: dict, cur: dict) -> Comparison:
    c = Comparison(prev)
    pg, cg = prev.get("gains", {}), cur.get("gains", {})
    for k in cg:
        if k in pg and pg[k] != cg[k]:
            c.changed.append((k, pg[k], cg[k]))
    adv = prev.get("advice") or []
    if adv:
        c.applied = all(cg.get(a["param"]) == a["new"] for a in adv)
        c.not_applied = [(a["param"], a["new"], cg.get(a["param"])) for a in adv if cg.get(a["param"]) != a["new"]]
    sensor = [k for k in ("AS", "AG", "AF") if k in pg and k in cg and pg[k] != cg[k]]
    if sensor:
        c.notes.append(f"הטאכו כויל מחדש בין ההקלטות ({', '.join(sensor)}), ולכן ההשוואה של התנועה לא אומרת אם "
                       "שינוי הטאכו היה נכון. את הטאכו בודקים רק מול האנקודר (שלב 1).")
    if prev.get("test") != cur.get("test"):
        c.notes.append("סוג הבדיקה שונה מההקלטה הקודמת, ולכן ההשוואה פחות אמינה.")
    ps, cs = prev.get("top_speed"), cur.get("top_speed")
    if ps and cs and abs(cs - ps) > 0.15 * abs(ps):
        c.notes.append(f"המהירות בתנועה שונה מההקלטה הקודמת ({ps:.0f} מול {cs:.0f}). כדי להשוות נכון, "
                       "מקליטים את אותה תנועה באותה מהירות.")
    pm = {k: _from_json(v) for k, v in prev.get("metrics", {}).items()}
    cm = {k: _from_json(v) for k, v in cur.get("metrics", {}).items()}
    up = down = 0
    for key, label, unit, rel, ab, w in METRICS:
        if key not in pm or key not in cm or pm[key] is None or cm[key] is None:
            continue
        b, a = pm[key], cm[key]
        sign = 0
        if b == a:
            sign = 0
        elif math.isinf(b) or math.isinf(a):
            sign = 1 if math.isinf(b) else -1
        elif abs(a - b) >= max(ab, rel * max(abs(b), abs(a))):
            sign = 1 if a < b else -1
        c.rows.append((label, unit, b, a, sign))
        c.score += w * sign
        up += sign > 0
        down += sign < 0
    if c.score >= 1.5 and not (down and c.score < 2.5):
        c.verdict = "better"
    elif c.score <= -1.5 and not (up and c.score > -2.5):
        c.verdict = "worse"
    elif up and down:
        c.verdict = "mixed"
    elif c.score > 0:
        c.verdict = "better"
    elif c.score < 0:
        c.verdict = "worse"
    return c


def step_back(comp: Comparison, gains: dict) -> Change | None:
    """The last advice was applied and made things worse: go back half way."""
    # only loop gains are ever stepped back: the speed sensor (AS / AG / AF) is calibrated against the
    # encoder, not judged by how the motion looks
    adv = [a for a in comp.prev.get("advice") or [] if a.get("param", "")[:3] in ("KP[", "KI[", "KD[")]
    if not (comp.verdict == "worse" and comp.applied and adv):
        return None
    first = adv[0]
    now = gains.get(first["param"])
    if not now:
        return None
    mids = [(a["param"], round((a["old"] + a["new"]) / 2)) for a in adv if a.get("old") is not None]
    if not mids or mids[0][1] == now:
        mids = [(a["param"], a["old"]) for a in adv]  # half way is no change: go all the way back
    target = mids[0][1]
    extra = tuple((k, gains.get(k)) for k, _ in mids[1:])
    names = ", ".join(a["param"] for a in adv)
    return Change(first["param"], now, target / now,
                  f"ההקלטה הקודמת ({comp.prev.get('recording')}) הייתה טובה יותר: השינוי האחרון ב-{names} "
                  f"החמיר. חוזרים חצי דרך לכיוון הערך הקודם, מקליטים שוב, ואם עדיין לא טוב חוזרים לגמרי.",
                  extra)


DEFAULT_UNIT = "מסובב 1"
# Settings that belong to the machine, not to the tuning: if they differ, the PRM is from another unit.
FINGERPRINT_KEYS = ["PA[43]", "PA[52]", "PA[62]", "HL", "LL", "CL", "PL", "MP"]


def fingerprint(prm, axis: str) -> dict:
    return {k: v for k in FINGERPRINT_KEYS if (v := prm.get(axis, k)) is not None}


def other_unit(prev_fp: dict | None, fp: dict | None) -> list[str]:
    """Keys that differ between two fingerprints (empty = looks like the same unit)."""
    if not prev_fp or not fp:
        return []
    diff = [k for k in FINGERPRINT_KEYS if k in prev_fp and k in fp and prev_fp[k] != fp[k]]
    # bits per degree is fixed by the mechanics; one other setting (e.g. max speed) may be changed on purpose
    return diff if "PA[43]" in diff or len(diff) >= 2 else []


RUN_FORMAT = "tuner4164-run"


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "x"


def run_document(entry: dict, prm_name: str, prm_text: str | None, rec_name: str, rec_bytes: bytes | None,
                 rec_mtime: float | None) -> dict:
    """One self-contained run: the analysis, the whole PRM (readable) and the original recording
    (gzip + base64, so it can be restored byte for byte)."""
    return {
        "format": RUN_FORMAT, "version": 1,
        "entry": {k: v for k, v in entry.items() if k != "archive"},
        "prm": {"name": prm_name, "text": prm_text},
        "recording": {"name": rec_name, "mtime": rec_mtime,
                      "gz_b64": base64.b64encode(gzip.compress(rec_bytes)).decode("ascii") if rec_bytes else None},
    }


def load_run(path: str | Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != RUN_FORMAT:
        raise ValueError("זה לא קובץ ריצה של כיול 4164")
    return doc


def extract_run(doc: dict, dest: str | Path) -> tuple[Path | None, Path | None]:
    """Write the PRM and the recording of a run back to files (same names and time stamp, so analysing
    the restored recording updates its own history entry instead of adding a new one)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    prm_path = rec_path = None
    prm = doc.get("prm") or {}
    if prm.get("text") is not None:
        prm_path = dest / _safe(prm.get("name") or "restored.prm")
        prm_path.write_bytes(prm["text"].encode("cp1255", "replace"))
    rec = doc.get("recording") or {}
    if rec.get("gz_b64"):
        rec_path = dest / _safe(rec.get("name") or "restored.sdv")
        rec_path.write_bytes(gzip.decompress(base64.b64decode(rec["gz_b64"])))
        if rec.get("mtime"):
            os.utime(rec_path, (rec["mtime"], rec["mtime"]))
    return prm_path, rec_path


class History:
    """All units (rotators / controllers) and axes in one file; every (unit, axis) is its own sequence."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: list[dict] = []
        self.extra_units: list[str] = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = data.get("entries", [])
            self.extra_units = data.get("units", [])
        except (OSError, ValueError, AttributeError):
            self.entries = []
        for e in self.entries:          # files from 0.4.x had no units
            e.setdefault("unit", DEFAULT_UNIT)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"units": self.extra_units, "entries": self.entries},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def units(self) -> list[str]:
        out = []
        for u in [DEFAULT_UNIT] + [e["unit"] for e in self.entries] + self.extra_units:
            if u not in out:
                out.append(u)
        return out

    def add_unit(self, name: str) -> None:
        if name not in self.units():
            self.extra_units.append(name)
            self.save()

    def for_axis(self, unit: str, axis: str) -> list[dict]:
        return [e for e in self.entries if e.get("unit") == unit and e.get("axis") == axis]

    def previous(self, unit: str, axis: str, rec_key: str) -> dict | None:
        """The entry before this recording in the unit/axis sequence (for a recording analysed for the
        first time: the last one)."""
        seq = self.for_axis(unit, axis)
        keys = [e.get("rec_key") for e in seq]
        if rec_key in keys:
            i = keys.index(rec_key)
            return seq[i - 1] if i > 0 else None
        return seq[-1] if seq else None

    def get(self, unit: str, axis: str, rec_key: str) -> dict | None:
        for e in self.entries:
            if (e.get("unit"), e.get("axis"), e.get("rec_key")) == (unit, axis, rec_key):
                return e
        return None

    def put(self, entry: dict) -> None:
        """Add, or replace in place the entry of the same recording, unit and axis (analysing it again)."""
        entry.setdefault("unit", DEFAULT_UNIT)
        for i, e in enumerate(self.entries):
            if (e.get("unit"), e.get("axis"), e.get("rec_key")) == (entry["unit"], entry["axis"], entry["rec_key"]):
                self.entries[i] = entry
                break
        else:
            self.entries.append(entry)
        self.save()

    @property
    def archive_dir(self) -> Path:
        return self.path.parent / "archive"

    def save_run(self, entry: dict, doc: dict) -> None:
        """Write the run file and note its place in the entry."""
        rel = (Path(_safe(entry.get("unit", DEFAULT_UNIT))) / entry["axis"] /
               f"{hashlib.sha1(entry['rec_key'].encode('utf-8')).hexdigest()[:10]}_{_safe(entry.get('recording', 'rec'))}.json")
        try:
            path = self.archive_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            entry["archive"] = rel.as_posix()
        except OSError:
            entry.pop("archive", None)

    def run_path(self, entry: dict) -> Path | None:
        rel = entry.get("archive")
        return self.archive_dir / rel if rel else None

    def clear(self, unit: str, axis: str) -> None:
        for e in self.for_axis(unit, axis):
            p = self.run_path(e)
            if p:
                try:
                    p.unlink()
                except OSError:
                    pass
        self.entries = [e for e in self.entries if not (e.get("unit") == unit and e.get("axis") == axis)]
        self.save()
