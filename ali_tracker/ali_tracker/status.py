"""מיפוי הטקסט באנגלית מהמיילים לשלב במשלוח, ולמה שצריך לעשות."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# שלבים לפי הסדר. סטטוס לא יורד לשלב מוקדם יותר בגלל מייל ישן.
CONFIRMED, SHIPPED, TRANSIT, CUSTOMS, LOCAL, PICKUP, DELIVERED, PROBLEM = (
    "confirmed", "shipped", "transit", "customs", "local", "pickup", "delivered", "problem")
STAGE = {CONFIRMED: 1, SHIPPED: 2, TRANSIT: 3, CUSTOMS: 4, LOCAL: 5, PICKUP: 6,
         DELIVERED: 7, PROBLEM: 5}

# הסדר חשוב: הביטוי הראשון שמתאים קובע
RULES: list[tuple[str, tuple[str, ...]]] = [
    (DELIVERED, ("delivered", "how did it go", "confirm receipt", "received your")),
    (PICKUP, ("ready for pickup", "ready to pick up", "pick up your", "pickup point", "awaiting collection")),
    (PROBLEM, ("failed", "returned", "return to sender", "exception", "problem", "unsuccessful",
               "delayed", "lost", "cancel", "refund")),
    (LOCAL, ("local carrier", "out for delivery", "arrived in destination", "arrived at destination",
             "destination country", "arrived in your country", "delivery attempt", "handed to")),
    (CUSTOMS, ("customs", "clearance")),
    (TRANSIT, ("left the departure", "global transit", "in transit", "departed", "has an update",
               "flight", "arrived at transit", "international")),
    (SHIPPED, ("shipped", "collected", "left warehouse", "sorting center", "carrier picked",
               "received by", "dispatched")),
    (CONFIRMED, ("confirmed", "ready to ship", "awaiting shipment", "processing", "paid", "placed")),
]

LABEL = {
    CONFIRMED: "ההזמנה אושרה, המוכר עוד לא שלח",
    SHIPPED: "נשלח, יוצא מסין",
    TRANSIT: "בדרך לישראל",
    CUSTOMS: "במכס",
    LOCAL: "בישראל, אצל חברת השילוח",
    PICKUP: "מחכה לך בנקודת איסוף",
    DELIVERED: "נמסר",
    PROBLEM: "בעיה במשלוח",
    "": "לא ידוע",
}

STALE_DAYS = 20          # בלי עדכון יותר מזה = כנראה תקוע
NOT_SHIPPED_DAYS = 7     # המוכר לא שלח יותר מזה
DELIVERED_QUIET_DAYS = 14  # אחרי זה כבר לא מציקים על חבילה שנמסרה


def classify(text: str) -> str:
    low = text.lower()
    for code, words in RULES:
        if any(w in low for w in words):
            return code
    return ""


@dataclass
class Advice:
    text: str
    urgent: bool        # דורש פעולה ממך


def advice(status: str, status_at: datetime | None, done: bool,
           now: datetime | None = None) -> Advice:
    """מה צריך לעשות עכשיו עם החבילה."""
    if done:
        return Advice("הכול בסדר, אין מה לעשות", False)
    now = now or datetime.now(timezone.utc)
    days = (now - status_at).days if status_at else 0
    if status == PICKUP:
        return Advice("ללכת לאסוף מנקודת האיסוף", True)
    if status == DELIVERED and days >= DELIVERED_QUIET_DAYS:
        return Advice(f"נמסר לפני {days} ימים. אם הכול תקין, לסמן «קיבלתי»", False)
    if status == DELIVERED:
        return Advice("לבדוק שהמוצר תקין, לאשר קבלה באלי אקספרס ולסמן «קיבלתי»", True)
    if status == PROBLEM:
        return Advice("לבדוק את פרטי המעקב, ואם צריך לפנות למוכר או לפתוח מחלוקת", True)
    if status == CONFIRMED and days >= NOT_SHIPPED_DAYS:
        return Advice(f"המוכר לא שלח כבר {days} ימים: לשלוח לו הודעה", True)
    if status in (SHIPPED, TRANSIT, CUSTOMS) and days >= STALE_DAYS:
        return Advice(f"אין עדכון {days} ימים: לבדוק, ואם צריך לפתוח מחלוקת לפני שתם זמן ההגנה", True)
    if status == LOCAL:
        return Advice("להיות זמין: השליח או הודעת איסוף בדרך", False)
    return Advice("לחכות, אין צורך לעשות כלום", False)
