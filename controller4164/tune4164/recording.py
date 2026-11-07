"""Recorded data (SC-AT Shell data recording exported as text).

The Shell records up to 10 vectors and appends a time vector. Its own viewer format is not
documented, so this reads text exports: CSV / TXT / TSV with one column per vector and an
optional header line. The time column is found by name, otherwise samples are spaced by
RG x the servo sample time (61.035 us on the SC-AT-4M / NT-4M).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_TIME_4M = 1 / 16384  # 61.035 us: 65536 samples = 4 s (Users Manual)

_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")

ROLES = {
    "time": ("time", "זמן", "sec", "[s]"),
    "reference": ("desired", "dp", "reference", "command pos", "ref", "רצוי"),
    "actual": ("position", "pos", "ps", "actual", "בפועל"),
    "error": ("error", "pe", "שגיאה"),
    "velocity": ("velocity", "vel", "speed", "vl", "tacho", "מהירות"),
    "command": ("command", "driver", "po", "dac", "פקודה"),
}


@dataclass
class Recording:
    path: Path | None
    names: list[str]
    data: np.ndarray                     # shape (samples, columns)
    time: np.ndarray                     # seconds
    time_column: int | None = None
    notes: list[str] = field(default_factory=list)

    def column(self, i: int | None) -> np.ndarray | None:
        return None if i is None else self.data[:, i]

    def guess(self, role: str) -> int | None:
        """Best column index for a role ("reference", "actual", ...), from the header names."""
        keys = ROLES[role]
        for i, name in enumerate(self.names):
            low = name.lower()
            if i == self.time_column:
                continue
            if role == "actual" and any(k in low for k in ROLES["reference"] + ROLES["error"]):
                continue
            if role == "velocity" and "desired" in low:
                continue
            if any(re.search(rf"(^|[^a-z]){re.escape(k)}([^a-z]|$)", low) for k in keys):
                return i
        return None


def _split(line: str, delim: str | None) -> list[str]:
    parts = line.split(delim) if delim else line.split()
    return [p.strip().strip('"') for p in parts]


def parse_recording(text: str, path: Path | None = None, dt: float | None = None) -> Recording:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("'", "#", "//"))]
    if not lines:
        raise ValueError("הקובץ ריק")
    sample = lines[min(len(lines) - 1, 5)]
    delim = max([",", "\t", ";"], key=sample.count)
    if sample.count(delim) == 0:
        delim = None  # whitespace separated
    header: list[str] | None = None
    rows: list[list[float]] = []
    skipped = 0
    for ln in lines:
        parts = [p for p in _split(ln, delim) if p != ""]
        if not parts:
            continue
        if all(_NUM.match(p) for p in parts):
            rows.append([float(p) for p in parts])
        elif header is None and not rows:
            header = parts
        else:
            skipped += 1
    if len(rows) < 10:
        raise ValueError("לא נמצאו מספיק שורות מספריות בקובץ (צריך לפחות 10)")
    width = max(set(len(r) for r in rows), key=[len(r) for r in rows].count)
    rows = [r for r in rows if len(r) == width]
    data = np.array(rows, dtype=float)
    names = header[:width] if header and len(header) >= width else [f"עמודה {i + 1}" for i in range(width)]
    rec = Recording(path, names, data, np.arange(len(rows), dtype=float))
    if skipped:
        rec.notes.append(f"{skipped} שורות לא מספריות דולגו")
    tcol = next((i for i, n in enumerate(names) if any(k in n.lower() for k in ROLES["time"])), None)
    if tcol is None and width > 1:
        first = data[:, 0]
        d = np.diff(first)
        if np.all(d > 0) and np.std(d) < 0.05 * abs(np.mean(d)):
            tcol = 0  # an evenly increasing first column is the time vector
    if tcol is not None:
        t = data[:, tcol].copy()
        if t[-1] - t[0] > 1000:      # milliseconds or samples rather than seconds
            rec.notes.append("עמודת הזמן נראית במילישניות. הומרה לשניות")
            t = t / 1000.0
        rec.time, rec.time_column = t - t[0], tcol
    else:
        step = dt if dt else SAMPLE_TIME_4M
        rec.time = np.arange(len(rows)) * step
        rec.notes.append(f"אין עמודת זמן. מרווח בין דגימות: {step * 1e3:.4f} ms (אפשר לשנות לפי RG)")
    return rec


def load_recording(path: str | Path, dt: float | None = None) -> Recording:
    path = Path(path)
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("זה קובץ בינארי (כנראה בפורמט של SDViewer). שמור את ההקלטה כקובץ טקסט/CSV "
                         "מתוך SDViewer, או שלח לי דוגמה של הקובץ כדי שאוסיף תמיכה.")
    for enc in ("utf-8-sig", "cp1255", "latin-1"):
        try:
            return parse_recording(raw.decode(enc), path, dt)
        except UnicodeDecodeError:
            continue
    return parse_recording(raw.decode("latin-1", "replace"), path, dt)
