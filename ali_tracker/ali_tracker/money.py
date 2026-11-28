"""מחירים: מהטקסט במייל (₪12.65, US $30.40, €7,50) לסכום בשקלים."""
from __future__ import annotations

import re

NUM_RE = re.compile(r"\d[\d.,]*")


def amount(text: str) -> float | None:
    """המספר שבטקסט. '1,234.56' -> 1234.56, '7,50' -> 7.5."""
    m = NUM_RE.search(text or "")
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." not in s and re.search(r",\d{1,2}$", s):
        s = s.replace(",", ".")                      # פסיק עשרוני
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def to_ils(text: str, usd: float, eur: float) -> tuple[float, bool] | None:
    """(סכום בשקלים, האם הומר משער). None אם אין מחיר או שהמטבע לא מוכר."""
    value = amount(text)
    if value is None:
        return None
    up = (text or "").upper()
    if "₪" in up or "ILS" in up or "NIS" in up:
        return value, False
    if "€" in up or "EUR" in up:
        return value * eur, True
    if "$" in up or "USD" in up:
        return value * usd, True
    return None


def fmt(value: float, converted: bool = False) -> str:
    return ("≈ " if converted else "") + f"₪{value:,.2f}"


def ils_text(text: str, usd: float, eur: float) -> str:
    """המחיר להצגה בשקלים; אם המטבע לא מוכר, הטקסט המקורי."""
    r = to_ils(text, usd, eur)
    return fmt(*r) if r else (text or "")
