"""Response metrics from a recording: step response or point-to-point move."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

DAC_FULL_SCALE = 32767  # 16-bit driver command (SC-AT-4M)


@dataclass
class StepMetrics:
    amplitude: float = 0.0
    overshoot_pct: float = 0.0
    rise_time: float | None = None        # 10% -> 90% [s]
    settling_time: float | None = None    # until it stays within the band [s]
    steady_error_pct: float = 0.0         # of the step size
    steady_error: float = 0.0             # in signal units
    damping: float | None = None          # estimated from the overshoot
    oscillations: int = 0                 # crossings of the final value after the step
    osc_freq: float | None = None         # Hz
    ok: bool = False
    note: str = ""


@dataclass
class MoveMetrics:
    max_error: float = 0.0               # largest |PE| during the move [counts]
    end_error_peak: float = 0.0          # largest |PE| after the motion ends
    residual_freq: float | None = None   # Hz of the oscillation after the end
    settle_after_end: float | None = None
    final_error: float = 0.0
    sustained: bool = False              # still oscillating at the end of the recording
    ok: bool = False
    note: str = ""


@dataclass
class CommandMetrics:
    saturated_pct: float = 0.0
    noise_ratio: float = 0.0             # sample-to-sample jitter vs. signal size
    peak: float = 0.0
    ok: bool = False


@dataclass
class Analysis:
    step: StepMetrics | None = None
    move: MoveMetrics | None = None
    command: CommandMetrics | None = None
    warnings: list[str] = field(default_factory=list)


def damping_from_overshoot(os_frac: float) -> float | None:
    if os_frac <= 0.002:
        return None  # no overshoot: over-damped, ζ ≥ ~0.9
    ln = math.log(os_frac)
    return -ln / math.sqrt(math.pi ** 2 + ln ** 2)


def _crossings(t: np.ndarray, y: np.ndarray, level: float, hyst: float) -> list[float]:
    """Times where y crosses level, ignoring wiggles smaller than hyst."""
    out, state = [], None
    for ti, yi in zip(t, y):
        s = 1 if yi > level + hyst else -1 if yi < level - hyst else 0
        if s == 0:
            continue
        if state is not None and s != state:
            out.append(float(ti))
        state = s
    return out


def step_metrics(t: np.ndarray, y: np.ndarray, ref: np.ndarray | None = None, band: float = 0.02) -> StepMetrics:
    m = StepMetrics()
    n = len(y)
    if n < 20:
        m.note = "מעט מדי נקודות"
        return m
    head, tail = max(3, n // 20), max(5, n // 10)
    y0 = float(np.mean(y[:head]))
    if ref is not None:
        r0, r1 = float(np.mean(ref[:head])), float(np.mean(ref[-tail:]))
        final = r1
        start_val = r0
    else:
        final = float(np.mean(y[-tail:]))
        start_val = y0
    amp = final - start_val
    noise = float(np.std(y[:head])) + 1e-12
    if abs(amp) < 5 * noise or abs(amp) == 0:
        m.note = "לא נמצאה קפיצה ברורה בהקלטה"
        return m
    m.amplitude = amp
    sgn = 1.0 if amp > 0 else -1.0
    z = (y - start_val) * sgn / abs(amp)          # 0 -> 1 normalized response
    # step start: reference crossing 50%, else the response leaving 5%
    if ref is not None:
        zr = (ref - start_val) * sgn / abs(amp)
        i0 = int(np.argmax(zr > 0.5))
    else:
        i0 = int(np.argmax(z > 0.05))
    i0 = max(0, i0 - 1)
    tt, zz = t[i0:] - t[i0], z[i0:]
    i10, i90 = np.argmax(zz >= 0.1), np.argmax(zz >= 0.9)
    if zz[i90] >= 0.9 and zz[i10] >= 0.1:
        m.rise_time = float(tt[i90] - tt[i10])
    peak = float(np.max(zz))
    m.overshoot_pct = max(0.0, (peak - 1.0) * 100)
    m.damping = damping_from_overshoot(m.overshoot_pct / 100)
    outside = np.where(np.abs(zz - 1.0) > band)[0]
    if len(outside) == 0:
        m.settling_time = 0.0
    elif outside[-1] < len(zz) - max(3, len(zz) // 50):
        m.settling_time = float(tt[outside[-1] + 1])
    m.steady_error_pct = float((1.0 - np.mean(zz[-tail:])) * 100)
    m.steady_error = m.steady_error_pct / 100 * abs(amp)
    cr = _crossings(tt, zz, 1.0, max(0.01, 3 * noise / abs(amp)))
    m.oscillations = len(cr)
    if len(cr) >= 3:
        m.osc_freq = float(1.0 / (2 * np.mean(np.diff(cr))))
    m.ok = True
    return m


def move_metrics(t: np.ndarray, pe: np.ndarray, moving: np.ndarray | None = None,
                 tolerance: float | None = None) -> MoveMetrics:
    """Point-to-point move judged from the position error. 'moving' marks motion samples if known
    (from a reference/desired-position column); otherwise the end is taken where |PE| peaks last."""
    m = MoveMetrics()
    n = len(pe)
    if n < 20:
        m.note = "מעט מדי נקודות"
        return m
    a = np.abs(pe)
    m.max_error = float(np.max(a))
    if moving is not None and np.any(moving):
        i_end = int(np.where(moving)[0][-1]) + 1
    else:
        i_end = int(np.argmax(a))
    i_end = min(i_end, n - 5)
    after_t, after = t[i_end:] - t[i_end], pe[i_end:]
    tail = max(5, n // 10)
    noise = float(np.std(pe[-tail:]))
    tol = tolerance if tolerance else max(3.0 * noise, 0.02 * m.max_error, 2.0)
    m.end_error_peak = float(np.max(np.abs(after)))
    m.final_error = float(np.mean(pe[-tail:]))
    outside = np.where(np.abs(after) > tol)[0]
    if len(outside) == 0:
        m.settle_after_end = 0.0
    elif outside[-1] < len(after) - tail // 2:
        m.settle_after_end = float(after_t[outside[-1] + 1])
    else:
        m.sustained = True
    cr = _crossings(after_t, after, float(np.mean(after[-tail:])), tol / 2)
    if len(cr) >= 3:
        m.residual_freq = float(1.0 / (2 * np.mean(np.diff(cr))))
    m.ok = True
    return m


def command_metrics(cmd: np.ndarray, limit: float | None = None) -> CommandMetrics:
    m = CommandMetrics()
    if cmd is None or len(cmd) < 10:
        return m
    lim = limit if limit else DAC_FULL_SCALE
    a = np.abs(cmd)
    m.peak = float(np.max(a))
    m.saturated_pct = float(np.mean(a >= 0.97 * lim) * 100)
    span = float(np.percentile(a, 95)) + 1e-9
    m.noise_ratio = float(np.std(np.diff(cmd)) / span)
    m.ok = True
    return m
