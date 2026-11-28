"""שער היציג של בנק ישראל: כתובות, ופענוח התשובות (בלי רשת, כדי שאפשר לבדוק)."""
from __future__ import annotations

import csv
import io
import json

# הממשק הציבורי של בנק ישראל: כל המטבעות, השער היציג האחרון
JSON_URL = "https://boi.org.il/PublicApi/GetExchangeRates"
# גיבוי: סדרת השער היציג של מטבע אחד (SDMX), התצפית האחרונה
CSV_URL = ("https://edge.boi.org.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0/"
           "RER_{cur}_ILS?format=csv&lastNObservations=1")
CURRENCIES = ("USD", "EUR")


def parse_json(text: str) -> dict[str, tuple[float, str]]:
    """{'USD': (3.293, '2026-11-27'), ...} מתוך GetExchangeRates."""
    out = {}
    for r in json.loads(text).get("exchangeRates", []):
        try:
            rate = float(r["currentExchangeRate"]) / float(r.get("unit") or 1)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            out[str(r.get("key", "")).upper()] = (rate, str(r.get("lastUpdate", ""))[:10])
    return out


def parse_csv(text: str) -> tuple[float, str] | None:
    """(שער, תאריך) מהשורה האחרונה של סדרת SDMX בפורמט CSV."""
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("OBS_VALUE")]
    if not rows:
        return None
    try:
        rate = float(rows[-1]["OBS_VALUE"])
    except ValueError:
        return None
    return (rate, rows[-1].get("TIME_PERIOD", "")) if rate > 0 else None


def date_he(iso: str) -> str:
    """'2026-11-27' -> '27.11.2026'."""
    parts = iso.split("-")
    return ".".join(reversed(parts)) if len(parts) == 3 else iso
