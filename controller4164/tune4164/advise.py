"""Rule-based tuning advice from the metrics, following the C&RS "SC-AT PIV Control Loop Tuning
Guidelines" (Rev 2.0) for PIV and the usual PID practice:

PIV (inner velocity loop KD + KI, outer position loop KP):
  1. velocity step with KP=0, KI=0: raise KD to the stability limit, use 1/3..1/4 of it;
  2. raise KI until the velocity step damping is below ~0.4;
  3. then raise KP; the position loop must stay at least 2x slower than the velocity loop
     (KP too high gives low-frequency oscillation, as in the guide's KP=120 vs 60 example).
Advice is one change at a time, of about 20-40%, never absolute "final" values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .analysis import Analysis

TESTS = {
    "velocity_step": "צעד מהירות (KP=0)",
    "position_step": "צעד מיקום קטן",
    "move": "תנועה רגילה לנקודה (PTP)",
}


@dataclass
class Change:
    param: str
    old: int | None
    factor: float
    reason: str

    @property
    def new(self) -> int | None:
        if self.old is None:
            return None
        v = int(round(self.old * self.factor))
        if v == self.old:
            v += 1 if self.factor > 1 else -1
        return max(0, v)

    @property
    def percent(self) -> int:
        return int(round((self.factor - 1) * 100))


@dataclass
class Advice:
    status: str = "ok"                 # ok | change | stop
    findings: list[str] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)   # first one = do this now
    warnings: list[str] = field(default_factory=list)

    @property
    def primary(self) -> Change | None:
        return self.changes[0] if self.changes else None


def _fmt_z(z: float | None) -> str:
    return "מעל 0.9 (בלי חריגה)" if z is None else f"{z:.2f}"


def advise(mode: str, test: str, gains: dict[str, int | None], an: Analysis) -> Advice:
    adv = Advice(warnings=list(an.warnings))
    g = lambda k: gains.get(k)  # noqa: E731
    add = lambda p, f, why: adv.changes.append(Change(p, g(p), f, why))  # noqa: E731

    cmd = an.command
    if cmd and cmd.ok:
        adv.findings.append(f"פקודת הדרייבר: שיא {cmd.peak:.0f}, רוויה {cmd.saturated_pct:.1f}% מהזמן, "
                            f"רעש {cmd.noise_ratio:.2f}")
        if cmd.saturated_pct > 2:
            adv.status = "stop"
            adv.findings.append("הפקודה למנוע מגיעה לתקרה (רוויה). במצב כזה ההקלטה לא מייצגת את הכיול. "
                                "הקטן מהירות או תאוצה בבדיקה והקלט שוב. אל תשנה כיול לפי ההקלטה הזו.")
            return adv
        if cmd.noise_ratio > 0.15:
            add("KD", 0.8, "פקודת הדרייבר רועשת (קפיצות בין דגימות). בדרך כלל זה KD גבוה מדי, "
                           "מה שנשמע כזמזום או שריקה. שקול גם מסנן מסדר 2 (100-400Hz) כפי שהיצרן ממליץ.")

    if test == "velocity_step" and mode == "PIV":
        if g("KP"):
            adv.warnings.append("בבדיקת צעד מהירות היצרן מכבה את לולאת המיקום (KP=0). ב-PRM הזה KP אינו 0, "
                                "ולכן ההמלצות ל-KD/KI פחות מדויקות.")
        s = an.step
        if not (s and s.ok):
            adv.findings.append(s.note if s else "לא נבחר אות מהירות לניתוח")
            adv.status = "stop"
            return adv
        adv.findings.append(f"תגובת המהירות: חריגה {s.overshoot_pct:.0f}%, ריסון ζ≈{_fmt_z(s.damping)}, "
                            f"תנודות {s.oscillations}" + (f" ב-{s.osc_freq:.0f}Hz" if s.osc_freq else ""))
        if s.oscillations >= 6 or (s.damping is not None and s.damping < 0.15):
            add("KD", 0.6, "לולאת המהירות קרובה לגבול היציבות (תנודות כמעט בלי דעיכה). "
                           "לפי המדריך, KD צריך להיות פי 3-4 מתחת לגבול היציבות.")
        elif s.damping is None or s.damping > 0.6:
            f = 1.5 if s.steady_error_pct > 2 else 1.3
            add("KI", f, f"התגובה מרוסנת מדי ואיטית (ζ {_fmt_z(s.damping)}). לפי המדריך מעלים KI עד ריסון "
                         f"של פחות מ-0.4 בצעד מהירות.")
        elif s.damping < 0.25:
            add("KI", 0.8, f"ריסון נמוך (ζ≈{s.damping:.2f}): הרבה חריגה בצעד המהירות. מורידים KI מעט.")
        else:
            adv.findings.append("לולאת המהירות נראית טובה. השלב הבא לפי המדריך: להחזיר ולכייל את KP "
                                "בצעדי מיקום קטנים.")
    elif test in ("position_step", "velocity_step"):
        s = an.step
        if not (s and s.ok):
            adv.findings.append(s.note if s else "לא נבחר אות מיקום לניתוח")
            adv.status = "stop"
            return adv
        adv.findings.append(f"תגובת המיקום: חריגה {s.overshoot_pct:.0f}%, ריסון ζ≈{_fmt_z(s.damping)}, "
                            f"תנודות {s.oscillations}" + (f" ב-{s.osc_freq:.0f}Hz" if s.osc_freq else "")
                            + (f", התייצבות {s.settling_time * 1000:.0f}ms" if s.settling_time is not None else
                               ", לא התייצב עד סוף ההקלטה"))
        if mode == "PID" and s.osc_freq and s.osc_freq > 40 and s.oscillations >= 4:
            add("KD", 0.8, f"תנודה מהירה ({s.osc_freq:.0f}Hz): ב-PID זה בדרך כלל KD גבוה מדי.")
        if s.overshoot_pct > 25 or (s.damping is not None and s.damping < 0.3) or s.oscillations >= 4:
            if mode == "PID" and s.oscillations < 4:
                add("KD", 1.25, "חריגה גדולה מעבר ליעד עם מעט תנודות: ב-PID מוסיפים ריסון (KD).")
            add("KP", 0.75, "חריגה ותנודות סביב היעד: KP גבוה מדי. במדריך, הורדת KP מ-120 ל-60 העלימה "
                            "תנודות דומות. לולאת המיקום צריכה להיות איטית לפחות פי 2 מלולאת המהירות.")
        elif s.overshoot_pct > 10:
            add("KP", 0.85, "חריגה בינונית מעבר ליעד. הורדה קטנה של KP תרגיע אותה.")
        elif s.overshoot_pct < 2:
            add("KP", 1.2, "הציר מגיע ליעד בלי שום חריגה (מרוסן מאוד). אם רוצים הגעה מהירה יותר, יש מקום "
                           "להעלות KP. אם המהירות הנוכחית מספיקה, אפשר להשאיר.")
        if abs(s.steady_error_pct) > 1:
            add("KI", 1.25, f"נשארת שגיאה קבועה של {s.steady_error_pct:.1f}% מהצעד: מעלים KI "
                            f"(ובודקים ש-IS לא נמוך מדי).")
        if mode == "PID" and s.oscillations >= 6 and s.overshoot_pct < 5:
            add("KI", 0.75, "תנודה קטנה ומתמשכת סביב היעד (Limit Cycle): KI גבוה מדי, או חיכוך/משחק במכניקה.")
        if not adv.changes:
            adv.findings.append("התגובה נראית טובה. אין צורך לשנות.")
    else:  # point-to-point move judged from the position error
        mv = an.move
        if not (mv and mv.ok):
            adv.findings.append(mv.note if mv else "לא נבחרה עמודת שגיאת מיקום (PE)")
            adv.status = "stop"
            return adv
        adv.findings.append(
            f"שגיאת מיקום: מקסימום {mv.max_error:.0f} בזמן התנועה, {mv.end_error_peak:.0f} אחרי סוף התנועה, "
            f"סופית {mv.final_error:.1f}" + (f", תנודה {mv.residual_freq:.1f}Hz" if mv.residual_freq else "")
            + (f", התייצבות {mv.settle_after_end * 1000:.0f}ms" if mv.settle_after_end is not None else ""))
        if mv.sustained:
            add("KP", 0.7, "הציר ממשיך להתנדנד עד סוף ההקלטה. מתחילים בהורדת KP.")
        elif mv.residual_freq and mv.end_error_peak > 0.2 * mv.max_error:
            add("KP", 0.8, "יש תנודה אחרי סוף התנועה: KP גבוה מדי (לולאת מיקום מהירה מדי).")
        elif mv.settle_after_end is not None and mv.settle_after_end > 0.3:
            add("KP", 1.2, "הציר מתייצב לאט אחרי סוף התנועה ובלי תנודה: יש מקום להעלות KP.")
        if mv.max_error > 0 and abs(mv.final_error) > max(2.0, 0.05 * mv.max_error):
            add("KI", 1.25, "נשארת שגיאת מיקום בסוף: מעלים KI.")
        if not adv.changes:
            adv.findings.append("התנועה נראית טובה. אין צורך לשנות.")

    if adv.changes and adv.status != "stop":
        adv.status = "change"
    return adv
