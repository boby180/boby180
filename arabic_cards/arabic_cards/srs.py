"""מתזמן חזרה מרווחת - גרסה פשוטה של SM-2 (כמו ב-Anki הקלאסי).

ציונים: 1=Again, 2=Hard, 3=Good, 4=Easy
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .models import ReviewState

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
MIN_EASE = 1.3


def schedule(st: ReviewState, grade: int, now: datetime | None = None) -> ReviewState:
    if grade not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError(f"grade must be 1-4, got {grade}")
    now = now or datetime.now(timezone.utc)
    s = replace(st)

    if grade == AGAIN:
        s.lapses += 1 if s.reps > 0 else 0
        s.ease = max(MIN_EASE, s.ease - 0.2)
        s.interval_days = 0
        s.due = now + timedelta(minutes=1)
        s.reps += 1
        return s

    if s.interval_days < 1:            # כרטיס חדש / אחרי טעות
        first = {HARD: 1, GOOD: 1, EASY: 4}[grade]
        s.interval_days = first
    else:
        factor = {HARD: 1.2, GOOD: s.ease, EASY: s.ease * 1.3}[grade]
        s.interval_days = max(s.interval_days + 1, s.interval_days * factor)

    s.ease += {HARD: -0.15, GOOD: 0.0, EASY: 0.15}[grade]
    s.ease = max(MIN_EASE, s.ease)
    s.reps += 1
    s.due = now + timedelta(days=s.interval_days)
    return s


def describe_interval(days: float) -> str:
    """מרווח בעברית קצרה, לתצוגה על כפתורי הציון."""
    if days < 1:
        return "דקה"
    d = round(days)
    if d == 1:
        return "יום"
    if d == 2:
        return "יומיים"
    if d < 30:
        return f"{d} ימים"
    if d < 365:
        m = d / 30
        return "חודש" if m < 1.5 else f"{m:.1f} חודשים".replace(".0", "")
    y = d / 365
    return "שנה" if y < 1.5 else f"{y:.1f} שנים".replace(".0", "")
