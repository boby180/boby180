"""Loop guard: the advice looks at the whole calibration sequence of the axis, not only at the previous
recording, so it does not send the user back to values that were already tried and were worse
(e.g. KI 26 -> 34 worse -> 30 -> 39 -> 34 ...).

Rules, for each parameter a change touches (C = now, N = proposed):
  1. Head for the best run: if an earlier run, equal in the other gains, was better than now and N moves
     away from its value, go half way towards that value instead (or back to it when close).
  2. No revisits: if a value between C and N (or N itself) was already tried, with the other gains the
     same, and that run was worse than now (or no better when it is N itself), go only half way to it.
  3. When half way is less than ~5% from C, the parameter has converged: the change is dropped.
  4. When the last two runs after the best one did not beat it, go back to the best run's gains and stop
     (the axis is as good as these gains get; more changes only go round in circles).
"""

from __future__ import annotations

from .advise import Advice, Change
from .history import compare

GAIN_PREFIXES = ("KP[", "KI[", "KD[")


def _gain_keys(gains: dict) -> set:
    return {k for k in gains if k.startswith(GAIN_PREFIXES)}


def _others_equal(run: dict, cur: dict, keys: set) -> bool:
    rg, cg = run.get("gains", {}), cur.get("gains", {})
    return all(rg.get(k) == cg.get(k) for k in (_gain_keys(cg) | _gain_keys(rg)) - keys)


def _wins(run: dict, seq: list) -> float:
    return sum(compare(o, run).score for o in seq if o is not run)


def best_run(seq: list) -> dict | None:
    """The run that beats the others most often (by the same comparison the user sees)."""
    runs = [e for e in seq if e.get("metrics")]
    return max(runs, key=lambda e: _wins(e, runs)) if len(runs) >= 2 else None


def _small(step: float, c: int) -> bool:
    return abs(step) < max(1.0, 0.05 * abs(c))


def _restore_best(advice: Advice, cur: dict, seq: list) -> list[str] | None:
    """Rule 4. Returns notes when it decided (and set advice.changes), None otherwise."""
    allruns = seq + [cur]
    best = best_run(allruns)
    if best is None or best is cur:
        return None
    after = allruns[allruns.index(best) + 1:]
    if len(after) < 2 or any(compare(best, r).verdict == "better" for r in after):
        return None
    diff = sorted(k for k in _gain_keys(cur["gains"]) | _gain_keys(best["gains"])
                  if best["gains"].get(k) is not None and cur["gains"].get(k) != best["gains"].get(k))
    name = f"{best.get('recording')} ({best.get('time')})"
    if not diff:
        advice.changes = [c for c in advice.changes if not {k for k, _, _ in c.items()} <= _gain_keys(
            {k: 0 for k, _, _ in c.items()})]
        if not advice.changes and advice.status == "change":
            advice.status = "ok"
        return [f"הערכים עכשיו זהים לריצה הטובה ביותר {name}, ושני הניסיונות שאחריה לא שיפרו אותה. הכיול של "
                f"KP/KI/KD בציר הזה התכנס: לא ממליצים על שינוי נוסף. אם התנועה עדיין לא מספיקה, כדאי לבדוק מכניקה "
                f"(חיכוך, משחק, איזון) או את פרופיל התנועה."]
    first = diff[0]
    restore = Change(first, cur["gains"].get(first), 1.0,
                     f"{len(after)} הריצות האחרונות לא שיפרו את הריצה הטובה ביותר עד עכשיו, {name}. "
                     f"חוזרים לערכים שלה ועוצרים כאן: עוד שינויים רק מסתובבים במעגל.",
                     tuple((k, cur["gains"].get(k)) for k in diff[1:]),
                     {k: best["gains"][k] for k in diff})
    advice.changes = [restore] + [c for c in advice.changes if not ({k for k, _, _ in c.items()} & set(diff))
                                  and not {k for k, _, _ in c.items()} <= _gain_keys({k: 0 for k, _, _ in c.items()})]
    advice.status = "change"
    return [f"מניעת לולאה: חוזרים לריצה הטובה ביותר {name}."]


def guard(advice: Advice, cur: dict, seq: list) -> list[str]:
    """Adjust advice.changes in place; returns notes for the user. 'seq' = earlier runs of this axis."""
    if any(not {k for k, _, _ in c.items()} <= _gain_keys({k: 0 for k, _, _ in c.items()}) for c in advice.changes):
        return []   # a sensor (AG) fix comes first; gains measured with another sensor scale are not comparable
    done = _restore_best(advice, cur, seq)
    if done is not None:
        return done
    notes: list[str] = []
    kept: list[Change] = []
    for ch in advice.changes:
        items = ch.items()
        keys = {k for k, _, _ in items}
        if not keys <= _gain_keys({k: 0 for k in keys}):
            kept.append(ch)          # AG and other non-gain changes are not guarded
            continue
        evidence = [r for r in seq if _others_equal(r, cur, keys)]
        # rule 1: is there a better earlier run, and are we moving away from it?
        better = [r for r in evidence if compare(r, cur).verdict == "worse"]   # cur worse than r
        best = max(better, key=lambda r: _wins(r, evidence + [cur])) if better else None
        targets, why, drop = {}, [], False
        for k, c, n in items:
            if c is None or n is None or n == c:
                continue
            if (best is not None and (tb := best["gains"].get(k)) is not None and (n - c) * (tb - c) < 0
                    and not _small(tb - c, c)):     # a 1% difference says nothing about which way is better
                mid = round((c + tb) / 2)
                targets[k] = tb if _small(mid - c, c) else mid
                why.append(f"{k}: הריצה {best.get('recording')} עם {k}={tb} הייתה טובה יותר מעכשיו, ולכן מתקרבים "
                           f"אליה ({targets[k]}) ולא מתרחקים ממנה")
                continue
            # rule 2: values already tried on the way to n
            tried = []
            for r in evidence:
                t = r["gains"].get(k)
                if t is None or (t - c) * (n - c) <= 0 or abs(t - c) > abs(n - c) * 1.03 + 1 or _small(t - c, c):
                    continue
                v = compare(r, cur).verdict
                at_n = abs(t - n) <= max(1, 0.03 * abs(n))
                if v == "better" or (at_n and v in ("same", "mixed")):
                    tried.append((abs(t - c), t, r, v))
            if not tried:
                continue
            _, t, r, v = min(tried, key=lambda x: x[0])
            mid = round((c + t) / 2)
            res = "גרועה יותר מעכשיו" if v == "better" else "לא טובה יותר מעכשיו"
            if _small(mid - c, c):
                drop = True
                why.append(f"{k}: הערך {t} כבר נבדק בהקלטה {r.get('recording')} והתוצאה הייתה {res}, ובין {c} ל-{t} "
                           f"אין כמעט מקום. {k} התכנס: משאירים {c}")
            else:
                targets[k] = mid
                why.append(f"{k}: הערך {t} כבר נבדק בהקלטה {r.get('recording')} והתוצאה הייתה {res}, ולכן לא חוזרים "
                           f"אליו אלא מנסים באמצע: {mid}")
        if drop:
            notes.append("מניעת לולאה: " + "; ".join(why) + ".")
            continue
        if targets:
            ch.targets.update(targets)
            ch.reason += " (מניעת לולאה: " + "; ".join(why) + ".)"
            if all(o is not None and n == o for _, o, n in ch.items()):
                continue
        kept.append(ch)
    advice.changes = kept
    if not kept and advice.status == "change":
        advice.status = "ok"
    return notes
