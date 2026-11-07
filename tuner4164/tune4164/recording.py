"""Recorded data: SC-AT Shell / Data Viewer .sdv files, or text exports (CSV / TXT / TSV).

.sdv layout (read from a real file, not documented): for each vector a 16-byte header
<u32 0, u32 1, u32 samples, u32 0>, <u32 name length>, the NUL-terminated name, then
'samples' little-endian float64 values. The vectors are followed by text viewer properties
("PropMotionRecorder ..."). The Shell appends a "Time" vector in milliseconds.

Text files: one column per vector and an optional header line. Without a time column samples
are spaced by RG x the servo sample time (61.035 us on the SC-AT-4M / NT-4M).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_TIME_4M = 1 / 16384  # 61.035 us: 65536 samples = 4 s (Users Manual)

_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")

ROLES = {
    "tacho": ("anlginp", "analog", "tacho", "ai"),
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
        """Best column index for a role ("reference", "actual", ...), from the vector names.
        Shell names glue the axis letter to the name (YPos, YVeldt, YAnlgInp), so it is dropped."""
        for i, name in enumerate(self.names):
            if i == self.time_column:
                continue
            if _role_of(name) == role:
                return i
        return None


def _role_of(name: str) -> str | None:
    low = re.sub(r"^[XYZWEFGHUV](?=[A-Z])", "", name.strip()).lower()
    words = re.findall(r"[a-zא-ת]+", low)
    joined = "".join(words)
    checks = [
        ("time", lambda: "time" in joined or "זמן" in joined),
        ("error", lambda: "err" in joined or joined in ("pe",) or "שגיאה" in joined),
        ("reference", lambda: any(k in joined for k in ("desired", "despos", "reference", "רצוי")) or joined == "dp"),
        ("command", lambda: any(k in joined for k in ("command", "driver", "drvcmd", "dac", "פקודה"))
         or joined in ("po", "cmd")),
        ("tacho", lambda: any(k in joined for k in ("anlg", "analog", "tacho")) or joined == "ai"),
        ("velocity", lambda: any(k in joined for k in ("vel", "speed", "מהירות")) or joined == "vl"),
        ("actual", lambda: any(k in joined for k in ("pos", "actual", "בפועל")) or joined == "ps"),
    ]
    for role, test in checks:
        if test():
            return role
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


def parse_sdv(raw: bytes, path: Path | None = None, dt: float | None = None) -> Recording:
    vectors: list[tuple[str, np.ndarray]] = []
    off = 0
    while off + 20 <= len(raw):
        zero, one, n, pad = struct.unpack_from("<4I", raw, off)
        name_len = struct.unpack_from("<I", raw, off + 16)[0]
        end = off + 20 + name_len + 8 * n
        if zero != 0 or one != 1 or pad != 0 or not 0 < name_len < 256 or n == 0 or end > len(raw):
            break  # the viewer properties text starts here
        name = raw[off + 20: off + 20 + name_len].split(b"\0", 1)[0].decode("latin-1").strip()
        vectors.append((name, np.frombuffer(raw, "<f8", n, off + 20 + name_len).astype(float)))
        off = end
    if not vectors:
        raise ValueError("לא זוהו וקטורים בקובץ ה-SDV")
    n = min(len(v) for _, v in vectors)
    names = [nm for nm, _ in vectors]
    data = np.column_stack([v[:n] for _, v in vectors])
    rec = Recording(path, names, data, np.arange(n, dtype=float))
    tcol = next((i for i, nm in enumerate(names) if nm.lower() == "time"), None)
    if tcol is not None:
        rec.time, rec.time_column = (data[:, tcol] - data[0, tcol]) / 1000.0, tcol  # Shell time vector is in ms
    else:
        step = dt if dt else SAMPLE_TIME_4M
        rec.time = np.arange(n) * step
        rec.notes.append(f"אין וקטור זמן. מרווח בין דגימות: {step * 1e3:.4f} ms")
    rec.notes.append(f"קובץ SDV: {len(names)} וקטורים, {n} דגימות, "
                     f"{np.median(np.diff(rec.time)) * 1e3:.4f} ms בין דגימות")
    return rec


def load_recording(path: str | Path, dt: float | None = None) -> Recording:
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix.lower() == ".sdv" or raw[:8] == b"\0\0\0\0\x01\0\0\0":
        return parse_sdv(raw, path, dt)
    if b"\x00" in raw[:4096]:
        raise ValueError("קובץ בינארי שלא זוהה. אפשר לפתוח קובצי SDV או קובצי טקסט/CSV.")
    for enc in ("utf-8-sig", "cp1255", "latin-1"):
        try:
            return parse_recording(raw.decode(enc), path, dt)
        except UnicodeDecodeError:
            continue
    return parse_recording(raw.decode("latin-1", "replace"), path, dt)
