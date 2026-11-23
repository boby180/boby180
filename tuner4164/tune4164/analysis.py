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
    max_error: float = 0.0               # largest |PE| in the recording [counts]
    following_pos: float | None = None   # mean PE while cruising in + direction
    following_neg: float | None = None   # mean PE while cruising in - direction
    cruise_ripple: float | None = None   # PE spread while cruising
    vib_freq: float | None = None        # main frequency of the PE ripple while cruising [Hz]
    vib_amp: float = 0.0                 # its amplitude (about the peak), counts
    stop_time: float | None = None       # time of the stop that was judged [s]
    end_error_peak: float = 0.0          # largest |PE| after that stop
    residual_freq: float | None = None   # Hz of the oscillation after the stop
    oscillations: int = 0
    settle_after_end: float | None = None
    final_error: float = 0.0             # PE at the end of the standstill
    tolerance: float = 0.0
    in_target: float | None = None       # allowed error window (TR by default): |PE| inside it is fine
    window_from_tr: bool = True          # the window is the controller's TR (else set by the user)
    settle_limit: float = 0.3            # allowed time to get inside the window after the stop [s]
    cruise_peak: float | None = None     # largest |PE| while moving at speed
    settle_tr: float | None = None       # stop -> |PE| inside TR for good [s]
    sustained: bool = False              # still oscillating at the end of the standstill
    stick: bool = False                  # friction: stuck while the command ramps, then breaks free
    stuck_error: float = 0.0             # PE while stuck [counts]
    stuck_time: float | None = None      # stop -> breakaway [s]; None = still stuck at the end
    cmd_at_stop: float = 0.0
    breakaway_cmd: float = 0.0           # driver command when it broke free (or at the end)
    stick_jump_over: bool = False        # after breaking free it jumped to the other side (hunting)
    has_stop: bool = False
    ok: bool = False
    note: str = ""


@dataclass
class CommandMetrics:
    saturated_pct: float = 0.0
    noise_ratio: float = 0.0             # sample-to-sample jitter vs. signal size
    peak: float = 0.0
    ok: bool = False


@dataclass
class TachoMetrics:
    """Speed sensor (Tacho on an analog input) against the encoder speed, both recorded."""
    gain: float = 0.0                    # tacho = gain * encoder + offset
    offset: float = 0.0
    correlation: float = 0.0
    residual_pct: float = 0.0            # rms of tacho - fit, % of the speed rms
    lag_samples: int = 0                 # tacho delayed (+) or ahead (-) of the encoder speed
    offset_pct: float = 0.0              # offset, % of the sensor reading at the top speed
    still_offset: float | None = None    # mean sensor reading while the axis stands (should be 0)
    ratio: float = 0.0                   # encoder speed / sensor (XVeldt / XAnlgInp) while moving
    ratio_pos: float | None = None       # the same, moving forward / backward only
    ratio_neg: float | None = None
    top_speed: float = 0.0               # largest |encoder speed| in the recording
    still_samples: int = 0
    offset_manual: bool = False          # the user zeroed AI by hand in SC-Shell: the offset is not judged
    ok: bool = False
    note: str = ""


@dataclass
class BacklashMetrics:
    """Backlash between the motor (tacho) and the load (encoder): at each change of direction, how far the
    motor turns before the load moves. Counts are encoder counts (the tacho scaled by Veldt / AnlgInp)."""
    travels: list = field(default_factory=list)    # one per direction reversal, in counts
    times: list = field(default_factory=list)      # when each reversal started [s]
    counts_per_deg: float | None = None

    @property
    def ok(self) -> bool:
        return bool(self.travels)

    def deg(self, counts: float) -> float | None:
        return counts / self.counts_per_deg if self.counts_per_deg else None

    @property
    def mean(self) -> float:
        return float(np.mean(self.travels)) if self.travels else 0.0

    @property
    def max(self) -> float:
        return float(np.max(self.travels)) if self.travels else 0.0


@dataclass
class Analysis:
    tacho: TachoMetrics | None = None
    step: StepMetrics | None = None
    move: MoveMetrics | None = None
    command: CommandMetrics | None = None
    backlash: BacklashMetrics | None = None
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


def _main_freq(t: np.ndarray, x: np.ndarray, mask: np.ndarray) -> float | None:
    """Main frequency (above 0.3 Hz) of x over the longest run of True in mask."""
    idx = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(int), [0]))))
    if len(idx) < 2:
        return None
    runs = list(zip(idx[::2], idx[1::2]))
    a, b = max(runs, key=lambda r: r[1] - r[0])
    if b - a < 32:
        return None
    seg = x[a:b] - np.mean(x[a:b])
    dt = float(np.median(np.diff(t[a:b])))
    if dt <= 0:
        return None
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), dt)
    ok = freqs >= 0.3
    if not np.any(ok):
        return None
    return float(freqs[ok][int(np.argmax(spec[ok]))])


def moving_from_velocity(t: np.ndarray, vel: np.ndarray, frac: float = 0.05, gap: float = 0.15) -> np.ndarray:
    """Moving = actual speed above 5% of the top speed, with gaps shorter than 'gap' seconds closed
    (used when the recording has no desired position)."""
    a = np.abs(np.nan_to_num(vel))
    top = float(np.max(a)) if len(a) else 0.0
    m = a > frac * top if top > 0 else np.zeros(len(a), dtype=bool)
    idx = np.flatnonzero(m)
    for i, j in zip(idx[:-1], idx[1:]):
        if j - i > 1 and t[j] - t[i] <= gap:
            m[i:j] = True
    return m


def _stick_slip(m: "MoveMetrics", t: np.ndarray, pe: np.ndarray, pos: np.ndarray, cmd: np.ndarray) -> None:
    """Friction after a stop: the position does not move (within 1 count) for at least 0.3 s while
    the driver command ramps (the integrator building force), then the axis breaks free."""
    n = len(pos)
    if n < 10:
        return
    dt = float(np.median(np.diff(t))) or 1e-3
    win = max(3, int(round(0.3 / dt)))
    s = None
    for i in range(0, n - win + 1):
        seg = pos[i:i + win]
        if seg.max() - seg.min() <= 1:
            s = i
            break
    if s is None:
        return
    off = np.flatnonzero(np.abs(pos[s:] - pos[s]) > 1)
    b = s + int(off[0]) if len(off) else n - 1
    ramp = abs(cmd[b] - cmd[s])
    scale = max(float(np.max(np.abs(cmd))), 1.0)
    if abs(pe[s]) <= m.tolerance or ramp < 0.15 * scale:
        return
    m.stick = True
    m.stuck_error = float(pe[s])
    m.cmd_at_stop = float(cmd[0])
    m.breakaway_cmd = float(cmd[b])
    if len(off):
        m.stuck_time = float(t[b] - t[0])
        rest = pe[b:]
        m.stick_jump_over = bool(np.any(np.sign(rest) == -np.sign(m.stuck_error))
                                 and np.max(np.abs(rest[np.sign(rest) == -np.sign(m.stuck_error)])) > 0.5 * abs(m.stuck_error))


def move_metrics(t: np.ndarray, pe: np.ndarray, moving: np.ndarray | None = None,
                 tolerance: float | None = None, vref: np.ndarray | None = None,
                 pos: np.ndarray | None = None, cmd: np.ndarray | None = None,
                 tr: float | None = None, settle_limit: float = 0.3, window_from_tr: bool = True) -> MoveMetrics:
    """Point-to-point motion judged from the position error.

    'moving' marks samples where the desired position changes (from the desired position or
    desired velocity). Cruise = moving at (near) the top desired speed. The stop judged is the
    one followed by the longest standstill (at least 0.2 s)."""
    m = MoveMetrics()
    m.settle_limit, m.window_from_tr = settle_limit, window_from_tr
    n = len(pe)
    if n < 20:
        m.note = "מעט מדי נקודות"
        return m
    a = np.abs(pe)
    m.max_error = float(np.max(a))
    if moving is None:
        moving = np.zeros(n, dtype=bool)
        moving[: int(np.argmax(a)) + 1] = True
    moving = moving.astype(bool)
    still = ~moving
    # sample-to-sample jitter, so a slow drift or a friction jump is not mistaken for noise
    quiet = pe[still] if still.sum() > 10 else pe[-n // 10:]
    noise = float(np.std(np.diff(quiet)) / np.sqrt(2)) if len(quiet) > 2 else 0.0
    m.tolerance = tol = tolerance if tolerance else max(5.0, 3.0 * noise)
    if vref is not None and np.any(moving):
        top = np.max(np.abs(vref[moving]))
        cruise = moving & (np.abs(np.abs(vref) - top) <= 0.02 * top)
        if np.any(cruise & (vref > 0)):
            m.following_pos = float(np.mean(pe[cruise & (vref > 0)]))
        if np.any(cruise & (vref < 0)):
            m.following_neg = float(np.mean(pe[cruise & (vref < 0)]))
        if np.any(cruise):
            m.cruise_ripple = float(np.std(pe[cruise] - np.where(vref[cruise] > 0, m.following_pos or 0, m.following_neg or 0)))
            m.vib_amp = m.cruise_ripple * np.sqrt(2)
            m.vib_freq = _main_freq(t, pe, cruise)
            m.cruise_peak = float(np.max(np.abs(pe[cruise])))
    elif np.any(moving):
        # no desired speed recorded: judge the moving part without its first and last 0.5 s (accelerations)
        dt = float(np.median(np.diff(t))) or 1e-3
        k = max(1, int(round(0.5 / dt)))
        core = moving.copy()
        edges_m = np.flatnonzero(np.diff(np.concatenate(([0], moving.astype(int), [0]))))
        for a0, b0 in zip(edges_m[::2], edges_m[1::2]):
            core[a0:min(b0, a0 + k)] = False
            core[max(a0, b0 - k):b0] = False
        if np.any(core):
            m.cruise_peak = float(np.max(np.abs(pe[core])))
    # standstill segments after a motion
    best = None
    edges = np.flatnonzero(np.diff(moving.astype(int)))
    for e in edges:
        if moving[e] and not moving[e + 1]:                      # motion -> standstill
            j = e + 1
            nxt = np.flatnonzero(moving[j:])
            k = j + (int(nxt[0]) if len(nxt) else n - j)
            if t[k - 1] - t[j] >= 0.2 and (best is None or k - j > best[1] - best[0]):
                best = (j, k)
    m.ok = True
    if best is None:
        m.note = "בהקלטה אין עצירה עם לפחות 0.2 שניות אחריה, ולכן אי אפשר לבדוק התייצבות"
        return m
    j, k = best
    m.has_stop = True
    m.stop_time = float(t[j])
    after_t, after = t[j:k] - t[j], pe[j:k]
    tail = max(5, len(after) // 5)
    m.final_error = float(np.mean(after[-tail:]))
    m.end_error_peak = float(np.max(np.abs(after)))
    outside = np.flatnonzero(np.abs(after - m.final_error) > tol)
    if len(outside) == 0:
        m.settle_after_end = 0.0
    elif outside[-1] < len(after) - tail:
        m.settle_after_end = float(after_t[outside[-1] + 1])
    if tr:
        m.in_target = float(tr)
        out_tr = np.flatnonzero(np.abs(after) > tr)
        if len(out_tr) == 0:
            m.settle_tr = 0.0
        elif out_tr[-1] < len(after) - tail:
            m.settle_tr = float(after_t[out_tr[-1] + 1])
    cr = _crossings(after_t, after, m.final_error, tol / 2)
    m.oscillations = len(cr)
    if len(cr) >= 3:
        m.residual_freq = float(1.0 / (2 * np.mean(np.diff(cr))))
        m.sustained = cr[-1] > after_t[-1] * 0.8
    if pos is not None and cmd is not None:
        _stick_slip(m, t[j:k], after, pos[j:k], cmd[j:k])
    return m


def _standstill(t: np.ndarray, v: np.ndarray, min_len: float = 0.5, settle: float = 0.3,
                vref: np.ndarray | None = None) -> np.ndarray:
    """Real standstill only: runs where the speed stays at ~0 for at least 'min_len' s, without their first
    'settle' s (right after a stop the tacho is still coming down, and that is not an offset)."""
    still = np.abs(v) <= 0.005 * (np.max(np.abs(v)) + 1e-12)
    if vref is not None and len(vref) >= len(v):
        # the encoder may not have moved yet while the motor (and the tacho on it) already turns - e.g. taking up
        # backlash at the start of a move: that is not standstill
        still &= np.abs(vref[:len(v)]) <= 0.005 * (np.max(np.abs(v)) + 1e-12)
    out = np.zeros(len(v), dtype=bool)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], still.astype(int), [0]))))
    for a, b in zip(edges[::2], edges[1::2]):
        if t[b - 1] - t[a] >= min_len:
            out[a:b] = t[a:b] >= t[a] + settle
    return out


def tacho_metrics(vel: np.ndarray, tacho: np.ndarray, max_lag: int = 40, t: np.ndarray | None = None,
                  vref: np.ndarray | None = None) -> TachoMetrics:
    m = TachoMetrics()
    n = min(len(vel), len(tacho))
    if n < 50 or np.std(vel[:n]) == 0:
        m.note = "אין מספיק תנועה בהקלטה כדי להשוות"
        return m
    v, a = vel[:n].astype(float), tacho[:n].astype(float)
    m.gain, m.offset = (float(x) for x in np.polyfit(v, a, 1))
    m.correlation = float(np.corrcoef(v, a)[0, 1])
    res = a - (m.gain * v + m.offset)
    m.residual_pct = float(np.std(res) / (np.std(a) + 1e-12) * 100)
    m.offset_pct = float(abs(m.offset) / (abs(m.gain) * np.max(np.abs(v)) + 1e-12) * 100)
    tt = t[:n].astype(float) if t is not None and len(t) >= n else np.arange(n) * 0.003
    still = _standstill(tt, v, vref=None if vref is None else np.asarray(vref, dtype=float))
    m.still_samples = int(still.sum())
    if m.still_samples >= 20:
        m.still_offset = float(np.mean(a[still]))
    # XVeldt / XAnlgInp while moving (|speed| above 30% of the top), offset at standstill taken off
    m.top_speed = float(np.max(np.abs(v)))
    a0 = a - (m.still_offset or 0.0)
    mov = np.abs(v) > 0.3 * np.max(np.abs(v))
    if np.any(mov) and np.dot(a0[mov], a0[mov]) > 0:
        m.ratio = float(np.dot(v[mov], a0[mov]) / np.dot(a0[mov], a0[mov]))
        for side, sel in (("ratio_pos", mov & (v > 0)), ("ratio_neg", mov & (v < 0))):
            if sel.sum() >= 10 and np.dot(a0[sel], a0[sel]) > 0:
                setattr(m, side, float(np.dot(v[sel], a0[sel]) / np.dot(a0[sel], a0[sel])))
    x, y = a - a.mean(), v - v.mean()
    L = min(max_lag, n // 4)
    lags = range(-L, L + 1)
    cc = [float(np.dot(x[L + k:n - L + k], y[L:n - L])) for k in lags]
    m.lag_samples = list(lags)[int(np.argmax(cc))]
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


def backlash_metrics(t: np.ndarray, pos: np.ndarray, tacho: np.ndarray, ratio: float, offset: float = 0.0,
                     vref: np.ndarray | None = None, counts_per_deg: float | None = None,
                     idle: float = 0.2, move_counts: float = 2.0, max_wait: float = 2.0,
                     cmd: np.ndarray | None = None, vel: np.ndarray | None = None) -> BacklashMetrics:
    """Backlash (lost motion) between the motor (tacho) and the load (encoder).

    With the driver command: at every torque reversal (the command changes sign) the gear teeth change flanks, and
    for a moment the motor turns while the load does not. That extra motor travel is the integral of
    (motor speed - load speed) over a short window, after taking off the bias measured just before it. The flanks
    usually change during the deceleration (the motor brakes), not at the start of the next move.
    Without the command: fall back to the start of each reversed move."""
    if cmd is not None and vel is not None:
        return _backlash_by_torque(t, tacho, ratio, offset, cmd, vel, counts_per_deg)
    m = BacklashMetrics(counts_per_deg=counts_per_deg)
    n = min(len(t), len(pos), len(tacho))
    if n < 50 or not ratio:
        return m
    t, pos = t[:n].astype(float), pos[:n].astype(float)
    sp = (tacho[:n].astype(float) - offset) * ratio
    top = float(np.max(np.abs(sp))) or 1.0
    if vref is not None and len(vref) >= n:
        vr = vref[:n].astype(float)
        cmd = np.abs(vr) > 0.002 * (np.max(np.abs(vr)) or 1.0)
        direction = np.sign(vr)
    else:
        cmd = np.abs(sp) > 0.02 * top
        direction = np.sign(sp)
    dt = float(np.median(np.diff(t))) or 1e-3
    edges = np.flatnonzero(np.diff(cmd.astype(int)) == 1) + 1
    last_dir = 0.0
    prev_end = None
    for s in edges:
        before = np.flatnonzero(cmd[:s])
        if len(before):
            prev_end = before[-1]
            last_dir = float(np.sign(np.mean(direction[max(0, prev_end - 20):prev_end + 1])))
        if prev_end is None or t[s] - t[prev_end] < idle:
            continue                                   # not a fresh start after a stop
        ahead = np.flatnonzero(cmd[s:s + int(0.5 / dt) + 1])
        now = float(np.sign(np.mean(direction[s:s + max(5, len(ahead))])))
        if not last_dir or now == last_dir:
            continue                                   # backlash shows only when the direction reverses
        moved = np.flatnonzero(np.abs(pos[s:] - pos[s]) >= move_counts)
        if not len(moved) or t[s + moved[0]] - t[s] > max_wait:
            continue
        e = s + int(moved[0])
        travel = abs(float(np.sum(sp[s:e])) * dt)
        m.travels.append(travel)
        m.times.append(float(t[s]))
    return m


def _backlash_by_torque(t, tacho, ratio, offset, cmd, vel, counts_per_deg, pre=0.3, before=0.05, after=0.30,
                        level=0.1) -> BacklashMetrics:
    m = BacklashMetrics(counts_per_deg=counts_per_deg)
    n = min(len(t), len(tacho), len(cmd), len(vel))
    if n < 100 or not ratio:
        return m
    t = t[:n].astype(float)
    r = (tacho[:n].astype(float) - offset) * ratio - vel[:n].astype(float)      # motor speed - load speed
    c = cmd[:n].astype(float)
    lvl = level * (np.percentile(np.abs(c), 95) or 1.0)
    sign = np.where(c > lvl, 1, np.where(c < -lvl, -1, 0))
    dt = float(np.median(np.diff(t))) or 1e-3
    last, last_i, last_event = 0, 0, -1e9
    for i in np.flatnonzero(sign):
        if last and sign[i] != last:
            t0 = t[last_i]                                   # the command was last clearly on the old side here
            a, b, e = np.searchsorted(t, [t0 - pre, t0 - before, t0 + after])
            if 0 < a < b < e < n and t0 - last_event > pre + after:
                bias = float(np.mean(r[a:b]))
                m.travels.append(abs(float(np.sum(r[b:e] - bias)) * dt))
                m.times.append(float(t0))
                last_event = t0
        last, last_i = sign[i], i
    return m
