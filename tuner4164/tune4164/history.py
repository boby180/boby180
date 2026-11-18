"""Calibration memory: every analysis is kept per axis, and the next recording is compared with the
previous one (what changed in the PRM, was the last advice applied, did the motion get better).

Stored as JSON in the user's application data folder, so it survives closing the program."""

from __future__ import annotations

import json
import math
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
            "advice_pct": advice.changes[0].percent if advice.changes else None}


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
    adv = comp.prev.get("advice") or []
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


class History:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: list[dict] = []
        try:
            self.entries = json.loads(self.path.read_text(encoding="utf-8")).get("entries", [])
        except (OSError, ValueError, AttributeError):
            self.entries = []

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"entries": self.entries}, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def for_axis(self, axis: str) -> list[dict]:
        return [e for e in self.entries if e.get("axis") == axis]

    def previous(self, axis: str, rec_key: str) -> dict | None:
        """The last entry of this axis made from a different recording."""
        for e in reversed(self.entries):
            if e.get("axis") == axis and e.get("rec_key") != rec_key:
                return e
        return None

    def put(self, entry: dict) -> None:
        """Add, or replace the entry of the same recording and axis (analysing it again)."""
        for i, e in enumerate(self.entries):
            if e.get("axis") == entry["axis"] and e.get("rec_key") == entry["rec_key"]:
                del self.entries[i]
                break
        self.entries.append(entry)
        self.save()

    def clear(self, axis: str) -> None:
        self.entries = [e for e in self.entries if e.get("axis") != axis]
        self.save()
