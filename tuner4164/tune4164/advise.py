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
    "full": "בדיקה מלאה: חיישן מהירות ואז הלולאה (מומלץ)",
    "tacho": "השוואת חיישן מהירות (Tacho) לאנקודר",
    "velocity_step": "צעד מהירות (KP=0)",
    "position_step": "צעד מיקום קטן",
    "move": "תנועה רגילה לנקודה (PTP)",
}


ALL = (1, 2, 3)   # the whole loop: in motion, after motion, standing still
STOP = (2, 3)     # after the profile ends: settling into the target and holding it
SET_NAMES = {1: "בזמן תנועה", 2: "אחרי סוף התנועה", 3: "בעמידה"}


def _scale(old: int, factor: float) -> int:
    v = int(round(old * factor))
    if v == old:
        v += (1 if factor > 1 else -1) * (1 if old >= 0 else -1)
    return v if old < 0 else max(0, v)  # AG may be negative; loop gains never are


@dataclass
class Change:
    param: str                     # e.g. "KP[2]" or "AG"
    old: int | None
    factor: float
    reason: str
    extra: tuple = ()              # more (param, old) pairs changed by the same factor, e.g. KP[3]
    targets: dict = field(default_factory=dict)   # explicit new values (set by the loop guard)

    def _new_of(self, k: str, o: int | None) -> int | None:
        if o is None:
            return None
        return self.targets[k] if k in self.targets else _scale(o, self.factor)

    @property
    def new(self) -> int | None:
        return self._new_of(self.param, self.old)

    def items(self) -> list[tuple[str, int | None, int | None]]:
        """Every parameter this change touches: (param, old, new)."""
        pairs = [(self.param, self.old)] + list(self.extra)
        return [(k, o, self._new_of(k, o)) for k, o in pairs]

    @property
    def complete(self) -> bool:
        return all(o is not None for _, o, _ in self.items())

    @property
    def label(self) -> str:
        return ", ".join(k for k, _, _ in self.items())

    @property
    def percent(self) -> int:
        if self.param in self.targets and self.old and self.param != "AG":   # AG may move together with AF
            return int(round((self.targets[self.param] / self.old - 1) * 100))
        return int(round((self.factor - 1) * 100))


AG_LIMIT = 32767          # AG is a 16-bit value
BIG_SENSOR_FIX = 0.25     # above this, AG is fixed together with an equivalent KP/KI/KD change


def sensor_warning(meas_gain: float) -> str:
    pct = abs(meas_gain) * 100
    out = (f"חיישן המהירות (הטאכו) לא מכויל: הוא קורא {pct:.1f}% ממהירות האנקודר (צריך 98%-102%). "
           f"מתקנים אותו לפני כל שינוי אחר, כי לולאת המהירות עובדת לפיו.")
    if abs(abs(meas_gain) - 1) > BIG_SENSOR_FIX:
        out += (" פער כל כך גדול אומר בדרך כלל שה-AG הועתק ממסובב אחר: אנקודר ברזולוציה אחרת (PA[43]), "
                "תמסורת אחרת או טאכו אחר.")
    return out


def sensor_change(gains: dict, meas_gain: float) -> Change | None:
    """AG (and AF only when AG would leave its 16-bit range) so the sensor reads the encoder speed.
    Nothing else changes: the speed sensor is fixed first, on its own."""
    ag, af = gains.get("AG"), gains.get("AF")
    if ag is None:
        return None
    f = 1.0 / abs(meas_gain)
    ag_new, af_new = ag * f, af
    if af is not None:
        while abs(ag_new) > AG_LIMIT and af_new > 0:
            ag_new /= 2
            af_new -= 1
    ag_new = int(round(ag_new))
    targets, extra = {"AG": ag_new}, []
    reason = (f"החיישן קורא {abs(meas_gain) * 100:.1f}% ממהירות האנקודר. לפי המדריך (סעיף 3.2) הם צריכים להיות שווים. "
              f"ההגבר של החיישן הוא AG×2^-AF, וצריך להגדיל אותו פי {f:.3f}.")
    if af is not None and af_new != af:
        extra.append(("AF", af))
        targets["AF"] = af_new
        reason += (f" AG לבד היה יוצא {int(round(ag * f))}, מחוץ לטווח (±{AG_LIMIT}), ולכן מקטינים גם את AF "
                   f"מ-{af} ל-{af_new} (כל 1 פחות ב-AF מכפיל את ההגבר פי 2).")
    if abs(f - 1) > BIG_SENSOR_FIX:
        reason += (f" זה שינוי גדול (פי {f:.1f}): בודקים במהירות נמוכה עם יד על לחצן החירום.")
    return Change("AG", ag, f, reason, tuple(extra), targets)


def gain_change(gains: dict, base: str, sets: tuple, factor: float, why: str) -> Change:
    keys = [f"{base}[{i}]" for i in sets]
    return Change(keys[0], gains.get(keys[0]), factor, why, tuple((k, gains.get(k)) for k in keys[1:]))


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
    add = lambda p, sets, f, why: adv.changes.append(gain_change(gains, p, sets, f, why))  # noqa: E731

    if test == "full":
        return _full(mode, gains, an, adv)

    cmd = an.command if test != "tacho" else None   # a sensor check advises the sensor only
    if cmd and cmd.ok:
        adv.findings.append(f"פקודת הדרייבר: שיא {cmd.peak:.0f}, רוויה {cmd.saturated_pct:.1f}% מהזמן, "
                            f"רעש {cmd.noise_ratio:.2f}")
        if cmd.saturated_pct > 2:
            adv.status = "stop"
            adv.findings.append("הפקודה למנוע מגיעה לתקרה (רוויה). במצב כזה ההקלטה לא מייצגת את הכיול. "
                                "הקטן מהירות או תאוצה בבדיקה והקלט שוב. אל תשנה כיול לפי ההקלטה הזו.")
            return adv
        if cmd.noise_ratio > 0.15:
            add("KD", ALL, 0.8, "פקודת הדרייבר רועשת (קפיצות בין דגימות). בדרך כלל זה KD גבוה מדי, "
                           "מה שנשמע כזמזום או שריקה. שקול גם מסנן מסדר 2 (100-400Hz) כפי שהיצרן ממליץ.")

    if test == "tacho":
        tm = an.tacho
        if not (tm and tm.ok):
            adv.findings.append(tm.note if tm else "בחר עמודת מהירות מהאנקודר ועמודת חיישן מהירות")
            adv.status = "stop"
            return adv
        _sensor_checks(tm, gains, adv)
        adv.findings.append("ההקלטה הזו בודקת את חיישן המהירות, לא את הכיול של KP/KI/KD. להמלצות על הכיול צריך "
                            "הקלטה עם מיקום רצוי / שגיאת מיקום ופקודת דרייבר.")
        if adv.changes:
            adv.status = "change"
        return adv

    if test == "velocity_step" and mode == "PIV":
        if g("KP[1]"):
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
            add("KD", ALL, 0.6, "לולאת המהירות קרובה לגבול היציבות (תנודות כמעט בלי דעיכה). "
                           "לפי המדריך, KD צריך להיות פי 3-4 מתחת לגבול היציבות.")
        elif s.damping is None or s.damping > 0.6:
            f = 1.5 if s.steady_error_pct > 2 else 1.3
            add("KI", ALL, f, f"התגובה מרוסנת מדי ואיטית (ζ {_fmt_z(s.damping)}). לפי המדריך מעלים KI עד ריסון "
                         f"של פחות מ-0.4 בצעד מהירות.")
        elif s.damping < 0.25:
            add("KI", ALL, 0.8, f"ריסון נמוך (ζ≈{s.damping:.2f}): הרבה חריגה בצעד המהירות. מורידים KI מעט.")
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
            add("KD", ALL, 0.8, f"תנודה מהירה ({s.osc_freq:.0f}Hz): ב-PID זה בדרך כלל KD גבוה מדי.")
        if s.overshoot_pct > 25 or (s.damping is not None and s.damping < 0.3) or s.oscillations >= 4:
            if mode == "PID" and s.oscillations < 4:
                add("KD", ALL, 1.25, "חריגה גדולה מעבר ליעד עם מעט תנודות: ב-PID מוסיפים ריסון (KD).")
            add("KP", ALL, 0.75, "חריגה ותנודות סביב היעד: KP גבוה מדי. במדריך, הורדת KP מ-120 ל-60 העלימה "
                            "תנודות דומות. לולאת המיקום צריכה להיות איטית לפחות פי 2 מלולאת המהירות.")
        elif s.overshoot_pct > 10:
            add("KP", ALL, 0.85, "חריגה בינונית מעבר ליעד. הורדה קטנה של KP תרגיע אותה.")
        elif s.overshoot_pct < 2:
            add("KP", ALL, 1.2, "הציר מגיע ליעד בלי שום חריגה (מרוסן מאוד). אם רוצים הגעה מהירה יותר, יש מקום "
                           "להעלות KP. אם המהירות הנוכחית מספיקה, אפשר להשאיר.")
        if abs(s.steady_error_pct) > 1:
            add("KI", ALL, 1.25, f"נשארת שגיאה קבועה של {s.steady_error_pct:.1f}% מהצעד: מעלים KI "
                            f"(ובודקים ש-IS לא נמוך מדי).")
        if mode == "PID" and s.oscillations >= 6 and s.overshoot_pct < 5:
            add("KI", ALL, 0.75, "תנודה קטנה ומתמשכת סביב היעד (Limit Cycle): KI גבוה מדי, או חיכוך/משחק במכניקה.")
        if not adv.changes:
            adv.findings.append("התגובה נראית טובה. אין צורך לשנות.")
    else:  # point-to-point move judged from the position error
        mv = an.move
        if not (mv and mv.ok):
            adv.findings.append(mv.note if mv else "לא נבחרה עמודת שגיאת מיקום (PE)")
            adv.status = "stop"
            return adv
        _move_advice(mv, adv, add, an.command)
        if not adv.changes:
            adv.findings.append("התנועה נראית טובה. אין צורך לשנות.")

    if adv.changes and adv.status != "stop":
        adv.status = "change"
    return adv


VIB_COUNTS = 20      # ripple amplitude (counts) while cruising that counts as vibration
NOISY_CMD = 0.5      # driver-command jitter that counts as very noisy


def _move_advice(mv, adv: Advice, add, cmd=None) -> None:
    fol = [abs(x) for x in (mv.following_pos, mv.following_neg) if x is not None]
    if fol:
        adv.findings.append(f"פיגור בזמן נסיעה במהירות קבועה: {max(fol):.0f} ספירות"
                            + (f", אדוות ±{mv.cruise_ripple:.0f}" if mv.cruise_ripple is not None else ""))
    # vibration while moving comes first: nothing else can be judged while the axis shakes
    noisy = cmd is not None and cmd.ok and cmd.noise_ratio > NOISY_CMD
    shaking = mv.vib_amp > max(VIB_COUNTS, 2 * mv.tolerance)
    if shaking or noisy:
        what = (f"רעידות בזמן הנסיעה: שגיאת המיקום רועדת ±{mv.vib_amp:.0f} ספירות"
                + (f" בתדר של כ-{mv.vib_freq:.1f}Hz" if mv.vib_freq else "") if shaking else "")
        if noisy:
            what += ("; " if what else "") + f"פקודת הדרייבר רועשת מאוד (רעש {cmd.noise_ratio:.2f})"
        adv.findings.append("✘ " + what + ". מתקנים את זה לפני ההתייצבות והחיכוך.")
        if noisy or (mv.vib_freq or 0) > 15:
            add("KD", ALL, 0.5 if (noisy and cmd.noise_ratio > 0.8) or mv.vib_amp > 3 * VIB_COUNTS else 0.7,
                "לולאת המהירות חזקה מדי: פקודת הדרייבר קופצת והציר רועד. מורידים KD (בשלוש הקבוצות). אחרי תיקון "
                "גדול של AG זה צפוי: הגבר הטאכו גדל, ולכן אותו KD נותן לולאת מהירות חזקה יותר באותו יחס.")
        else:
            add("KP", ALL, 0.75, f"רעידה איטית בזמן הנסיעה ({mv.vib_freq:.1f}Hz) עם פקודת דרייבר חלקה: לולאת המיקום "
                                 "חזקה מדי. מורידים KP. אם זה לא עוזר, הרעידה כנראה מכנית (תמסורת או אנקודר), "
                                 "ובודקים אם התדר שלה משתנה עם המהירות.")
        return
    if not mv.has_stop:
        adv.findings.append(mv.note)
        return
    adv.findings.append(
        f"אחרי העצירה ב-{mv.stop_time:.2f}s: שיא שגיאה {mv.end_error_peak:.0f}, "
        + (f"התייצבות תוך {mv.settle_after_end * 1000:.0f}ms" if mv.settle_after_end is not None else "לא התייצב")
        + f", שגיאה סופית {mv.final_error:.0f} (סף {mv.tolerance:.0f})"
        + (f", תנודה {mv.residual_freq:.1f}Hz" if mv.residual_freq else ""))
    stop_note = " משנים רק את [2] ו-[3] (אחרי סוף התנועה ובעמידה). [1] בזמן תנועה נשאר כמו שהוא."
    if mv.stick:
        free = (f"עד שהציר השתחרר אחרי {mv.stuck_time:.1f} שניות" if mv.stuck_time is not None
                else "והציר לא השתחרר עד סוף העמידה")
        adv.findings.append(f"חיכוך (stick-slip): אחרי העצירה הציר נתקע בשגיאה של \u200e{mv.stuck_error:.0f}\u200e ספירות. "
                            f"הפקודה למנוע השתנתה לאט מ-\u200e{mv.cmd_at_stop:.0f}\u200e ל-\u200e{mv.breakaway_cmd:.0f}\u200e (האינטגרל בונה כוח) "
                            f"{free}. זה לא רעש ולא תנודה, ומסנן לא יעזור כאן.")
        if mv.stick_jump_over:
            add("KI", STOP, 0.8, "אחרי השחרור הציר קופץ לצד השני של המטרה ונתקע שוב (נדנוד חיכוך): האינטגרל חזק מדי "
                                 "ביחס לחיכוך. מורידים מעט KI." + stop_note)
        else:
            add("KI", STOP, 1.3, "האינטגרל בונה את הכוח לאט מדי מול החיכוך, ולכן הציר נשאר תקוע. מעלים KI כדי "
                                 "שהפקודה תגיע מהר יותר לכוח השחרור. אם אחרי השינוי הציר יתחיל לקפוץ לצד השני של "
                                 "המטרה, זה סימן שהגזמנו וחוזרים חצי דרך." + stop_note)
        return
    slow = mv.residual_freq is not None and mv.residual_freq < 1.0
    wobbles = mv.sustained or mv.oscillations >= 3
    if wobbles and slow:
        add("KI", STOP, 0.7, f"נדנוד איטי אחרי העצירה ({mv.residual_freq:.2f}Hz, מחזור של כ-"
                             f"{1 / mv.residual_freq:.0f} שניות). תנודה איטית כזו נגרמת בדרך כלל מהאינטגרל (KI) "
                             f"יחד עם חיכוך, ולא מ-KP: האינטגרל מצטבר עד שהציר קופץ ועובר את המטרה. מורידים KI."
                             + stop_note + " אם אין שיפור, מחזירים את KI ומנסים להוריד KP[2]/KP[3].")
    elif mv.sustained:
        add("KP", STOP, 0.7, "הציר ממשיך להתנדנד עד סוף העמידה. מתחילים בהורדת KP." + stop_note)
    elif wobbles:
        add("KP", STOP, 0.8, "יש תנודה אחרי העצירה: KP גבוה מדי (לולאת מיקום מהירה מדי)." + stop_note)
    elif mv.settle_after_end is not None and mv.settle_after_end > 0.3:
        add("KP", STOP, 1.2, f"הציר מתייצב לאט ({mv.settle_after_end * 1000:.0f}ms) ובלי תנודה: יש מקום להעלות KP."
                             + stop_note)
    if abs(mv.final_error) > 2 * mv.tolerance and not (wobbles and slow):
        add("KI", STOP, 1.25, f"נשארת שגיאת מיקום של {mv.final_error:.0f} ספירות בעמידה: מעלים KI." + stop_note)


SENSOR_GAIN_TOL = 0.02    # 98%-102%
SENSOR_MIN_CORR = 0.99
SENSOR_MAX_NOISE = 5.0    # % of the speed
SENSOR_MAX_OFFSET = 2.0   # % of the reading at the top speed


def _sensor_checks(tm, gains: dict, adv: Advice) -> bool:
    """All the speed-sensor checks; returns True when anything is not OK (then nothing else is advised)."""
    bad = False
    adv.findings.append(f"חיישן המהירות = {tm.gain:.4f} × מהירות האנקודר {tm.offset:+.0f}  "
                        f"(התאמה {tm.correlation:.5f}, רעש {tm.residual_pct:.2f}%, השהיה {tm.lag_samples} דגימות)")
    if tm.gain < 0:
        bad = True
        adv.warnings.insert(0, "חיישן המהירות (הטאכו) לא מכויל: הסימן שלו הפוך לאנקודר. צריך להפוך את סימן AG "
                               "(או את החיווט) לפני כל דבר אחר.")
        if gains.get("AG") is not None:
            ch = sensor_change(gains, tm.gain)
            ch.targets["AG"] = -ch.targets["AG"]        # flip the sign (and fix the size, if needed)
            ch.reason = "הסימן של הטאכו הפוך לאנקודר: הופכים את סימן AG. " + ch.reason
            adv.changes.append(ch)
    err = abs(abs(tm.gain) - 1.0)
    if err > SENSOR_GAIN_TOL:
        bad = True
        if tm.gain >= 0:
            adv.warnings.insert(0, sensor_warning(tm.gain))
            if gains.get("AG") is not None:
                adv.changes.append(sensor_change(gains, tm.gain))
            else:
                adv.findings.append("כדי לחשב AG חדש טען את קובץ ה-PRM.")
    else:
        adv.findings.append(f"✔ ההגבר של הטאכו טוב: הפרש {err * 100:.2f}% (היעד: פחות מ-2%).")
    if abs(tm.correlation) < SENSOR_MIN_CORR:
        bad = True
        adv.findings.append(f"✘ ההתאמה בין הטאכו לאנקודר חלשה ({tm.correlation:.4f}, צריך לפחות {SENSOR_MIN_CORR}): "
                            "בודקים חיווט, הארקה ורעש בטאכו לפני כל דבר אחר.")
    if tm.residual_pct > SENSOR_MAX_NOISE:
        bad = True
        adv.findings.append(f"✘ רעש גבוה בטאכו ({tm.residual_pct:.1f}%, צריך פחות מ-{SENSOR_MAX_NOISE:.0f}%): "
                            "בודקים חיווט, הארקה ומיסוך של הטאכו.")
    if tm.offset_pct > SENSOR_MAX_OFFSET:
        bad = True
        adv.findings.append(f"✘ לטאכו יש אופסט: הוא קורא {tm.offset:+.0f} גם כשהציר עומד ({tm.offset_pct:.1f}% מהקריאה "
                            f"במהירות המרבית, צריך פחות מ-{SENSOR_MAX_OFFSET:.0f}%). מאפסים את האופסט בדרייבר או בכניסה "
                            "האנלוגית.")
    if not bad:
        adv.findings.append("✔ הטאכו תקין לגמרי (הגבר, סימן, התאמה, רעש ואופסט). עוברים לשלב 2.")
    return bad


def _full(mode: str, gains: dict, an: Analysis, adv: Advice) -> Advice:
    """Speed sensor first (the velocity loop runs on it), then the position loop."""
    g = lambda k: gains.get(k)  # noqa: E731
    sensor_bad = False
    adv.findings.append("שלב 1: חיישן המהירות (טאכו) מול האנקודר")
    tm = an.tacho
    if tm and tm.ok:
        sensor_bad = _sensor_checks(tm, gains, adv)
    elif mode == "PIV":
        sensor_bad = True
        adv.warnings.insert(0, "חיישן המהירות (הטאכו) לא נבדק: אין בהקלטה כניסה אנלוגית (Analog Input) או מהירות "
                               "אנקודר. ב-PIV לולאת המהירות עובדת לפי הטאכו, ולכן קודם מקליטים גם Analog Input "
                               "ובודקים אותו.")
    else:
        adv.findings.append("ב-PID אין חיישן מהירות, ולכן שלב 1 לא נדרש.")
    if sensor_bad:
        adv.findings.append("שלב 2 (לולאת הבקרה: KP / KI / KD) לא נבדק. עוברים אליו רק כשהטאכו תקין לגמרי: "
                            "קודם מתקנים את הטאכו, מקליטים שוב, ורק אז ממשיכים.")
        adv.status = "change" if adv.changes else "stop"
        return adv

    adv.findings.append("שלב 2: לולאת הבקרה")
    cmd = an.command
    if cmd and cmd.ok:
        adv.findings.append(f"פקודת הדרייבר: שיא {cmd.peak:.0f}, רוויה {cmd.saturated_pct:.1f}%, רעש {cmd.noise_ratio:.2f}")
    loop = Advice()
    loop_add = lambda p, sets, f, why: loop.changes.append(gain_change(gains, p, sets, f, why))  # noqa: E731
    if cmd and cmd.ok and cmd.saturated_pct > 2:
        adv.findings.append("הפקודה למנוע ברוויה: הקטן מהירות או תאוצה והקלט שוב לפני שינוי כיול.")
    else:
        if an.move and an.move.ok:
            _move_advice(an.move, loop, loop_add, cmd)
        elif an.step and an.step.ok:
            sub = advise(mode, "position_step", gains, Analysis(step=an.step))
            loop.findings += sub.findings
            loop.changes += sub.changes
        else:
            loop.findings.append("אין בהקלטה שגיאת מיקום או מיקום רצוי, ולכן הלולאה לא נבדקה.")
        if not loop.changes and cmd and cmd.ok and cmd.noise_ratio > 0.15:
            loop_add("KD", ALL, 0.8, "פקודת הדרייבר רועשת: בדרך כלל KD גבוה מדי.")
    adv.findings += loop.findings
    adv.changes += loop.changes
    if not loop.changes and an.move and an.move.ok:
        adv.findings.append("הלולאה נראית טובה. אין צורך לשנות.")
    adv.status = "change" if adv.changes else "ok"
    return adv
