"""Controller state, a simulator, and the settings file.

BaseController holds what the simulator and the real controller link (link.py) share, so the
GUI talks to either one through the same methods.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable

N_AXES = 4
N_BITS = 16
AXIS_LETTERS = "XYZW"
HW_MARGIN = 5.0  # limit switch sits this many degrees past the soft limit (simulation only)
SETTINGS_FILE = "settings_4164.json"

# Status word: bit number -> (code, Hebrew label)
STATUS_BITS = [
    ("Ready", "מוכן"),
    ("Moving", "בתנועה"),
    ("Homed", "כל הצירים ב-Home"),
    ("Alarm", "תקלה"),
    ("E-Stop", "עצירת חירום"),
    ("SoftLim", "לימיט תוכנה"),
    ("HwLim", "לימיט חומרה"),
    ("Servo", "סרבו פעיל"),
    ("Conn", "מחובר"),
]
INPUT_NAMES = ["E-Stop OK", "HOME J1", "HOME J2", "HOME J3", "HOME J4", "", "", "",
               "דלת סגורה", "לחץ אוויר", "", "", "", "", "", ""]
OUTPUT_NAMES = ["מנורה ירוקה", "מנורה אדומה", "זמזם", "תפסנית", "ואקום", "", "", "",
                "", "", "", "", "", "", "", ""]
# EM, end of motion reason (SC-AT New Motion Modes manual). Other codes are shown as numbers.
END_MOTION_REASONS = {4: "לימיט HL", 5: "לימיט LL", 10: "נעצר באירוע"}

LogFn = Callable[[str, str], None]  # (kind: "tx" | "rx" | "err", text)

EPS = 1e-6


@dataclass
class AxisSettings:
    min: float = -200.0
    max: float = 200.0
    vmax: float = 120.0          # deg/s at 100% speed
    home: float = 0.0
    bits_per_deg: float = 1000.0  # constant: encoder bits per degree


@dataclass
class Axis:
    name: str
    cfg: AxisSettings
    letter: str = ""              # controller axis letter (X, Y, Z, W)
    pos: float = 0.0
    target: float | None = None
    jog: int = 0
    vel: float = 0.0
    homed: bool = False
    soft_hit: bool = False
    hw_hit: bool = False
    motor_on: bool = False
    ms: int | None = None         # MS, motion status from the controller (0 = stopped)
    sr: int | None = None         # SR, status register from the controller
    em: int | None = None         # EM, end of motion reason from the controller

    def bits(self, deg: float | None = None) -> int:
        return round((self.pos if deg is None else deg) * self.cfg.bits_per_deg)

    @property
    def at_home(self) -> bool:
        return abs(self.pos - self.cfg.home) < 0.5

    @property
    def at_soft_limit(self) -> bool:
        return self.soft_hit or self.pos <= self.cfg.min + EPS or self.pos >= self.cfg.max - EPS

    @property
    def at_hw_min(self) -> bool:
        return self.pos <= self.cfg.min - HW_MARGIN + EPS

    @property
    def at_hw_max(self) -> bool:
        return self.pos >= self.cfg.max + HW_MARGIN - EPS

    @property
    def moving(self) -> bool:
        if self.ms is not None:
            return self.ms != 0
        return abs(self.vel) > 0.01


def make_axes(n_axes: int = N_AXES) -> list[Axis]:
    return [Axis(f"J{i + 1}", AxisSettings(), letter=AXIS_LETTERS[i]) for i in range(n_axes)]


class BaseController:
    """State and checks shared by the simulator and the real controller link."""

    simulated = True

    def __init__(self, axes: list[Axis], log: LogFn | None = None):
        self.axes = axes
        self.log: LogFn = log or (lambda kind, text: None)
        self.connected = False
        self.port = ""
        self.servo = False
        self.estop = False
        self.alarm = False
        self.soft_limits = True
        self.speed_pct = 40
        self.outputs = [False] * N_BITS

    def set_soft_limits(self, on: bool) -> None:
        self.soft_limits = on
        if on:
            self.log("tx", "SOFT LIMITS ON")
        else:
            self.log("err", "SOFT LIMITS OFF: הציר יעצור רק בלימיט החומרה")

    def why_cant_move(self) -> str | None:
        if not self.connected:
            return "אין חיבור לבקר."
        if self.estop:
            return 'עצירת חירום פעילה. לחץ "איפוס תקלה".'
        if self.alarm:
            return 'יש תקלה פעילה. לחץ "איפוס תקלה".'
        if not self.servo:
            return "הסרבו כבוי. הדלק סרבו לפני תנועה."
        return None

    def _check_move(self) -> bool:
        reason = self.why_cant_move()
        if reason:
            self.log("err", reason)
        return reason is None

    def _clamp_target(self, a: Axis, deg: float) -> float:
        if self.soft_limits and not (a.cfg.min <= deg <= a.cfg.max):
            clamped = min(a.cfg.max, max(a.cfg.min, deg))
            self.log("err", f"{a.name}: {deg:.3f}° מחוץ ללימיט [{a.cfg.min:g}, {a.cfg.max:g}]. "
                            f"היעד הוגבל ל-{clamped:.3f}°")
            a.soft_hit = True
            return clamped
        return deg

    def speed_bits(self, a: Axis) -> int:
        """Speed for the controller SP parameter, in counts per second."""
        return max(1, round(a.cfg.vmax * self.speed_pct / 100 * a.cfg.bits_per_deg))

    def step(self, i: int, direction: int, step_deg: float) -> bool:
        a = self.axes[i]
        base = a.target if a.target is not None else a.pos
        return self.move_to(i, base + direction * step_deg, "JOG")

    def home(self, i: int) -> bool:
        return self.move_to(i, self.axes[i].cfg.home, "HOME")

    def home_all(self) -> bool:
        if not self._check_move():
            return False
        self.log("tx", "HOME ALL")
        return all([self.move_to(i, a.cfg.home, "HOME") for i, a in enumerate(self.axes)])

    def move_to(self, i: int, deg: float, verb: str = "MOVE") -> bool:
        raise NotImplementedError

    def inputs(self) -> list[bool]:
        raise NotImplementedError

    def status_bits(self) -> list[bool]:
        return [
            self.connected and self.servo and not self.alarm and not self.estop,
            any(a.moving for a in self.axes),
            all(a.homed for a in self.axes),
            self.alarm,
            self.estop,
            any(a.at_soft_limit for a in self.axes),
            any(a.hw_hit for a in self.axes),
            self.servo,
            self.connected,
        ]

    def status_word(self) -> int:
        return sum(1 << i for i, b in enumerate(self.status_bits()) if b)

    def read_limits(self) -> None:
        self.log("err", "קריאת לימיטים אפשרית רק בחיבור לבקר.")

    def write_limits(self) -> None:
        self.log("err", "כתיבת לימיטים אפשרית רק בחיבור לבקר.")

    def tick(self, dt: float) -> None:
        """Advance the simulation. The real link updates from its own thread."""

    def close(self) -> None:
        """Release the connection. Nothing to do for the simulator."""


class SimController(BaseController):
    def __init__(self, n_axes: int = N_AXES, log: LogFn | None = None, axes: list[Axis] | None = None):
        super().__init__(axes if axes is not None else make_axes(n_axes), log)
        for a in self.axes:
            a.ms = a.sr = a.em = None
        self.manual_inputs = [False] * N_BITS
        self.manual_inputs[8] = True  # door closed
        self.manual_inputs[9] = True  # air pressure ok

    # ---- connection / machine state ----
    def connect(self, port: str, baud: int) -> None:
        self.connected, self.port = True, port
        self.log("rx", f"CONNECTED {port} @ {baud} (SIM)")

    def disconnect(self) -> None:
        self.connected = False
        self.log("tx", "DISCONNECT")

    def set_servo(self, on: bool) -> bool:
        if on and (self.estop or self.alarm):
            self.log("err", "אי אפשר להדליק סרבו כשיש עצירת חירום או תקלה.")
            return False
        self.servo = on
        for a in self.axes:
            a.motor_on = on
        if not on:
            self._stop_all()
        self.log("tx", f"SERVO {'ON' if on else 'OFF'}")
        return True

    def emergency_stop(self) -> None:
        self.estop = True
        self.servo = False
        self._stop_all()
        self.log("err", "E-STOP: כל הצירים נעצרו, הסרבו כובה")

    def reset(self) -> None:
        self.estop = self.alarm = False
        for a in self.axes:
            a.soft_hit = a.hw_hit = False
        self.log("tx", "RESET")

    def _stop_all(self) -> None:
        for a in self.axes:
            a.jog, a.target = 0, None

    # ---- motion commands ----
    def move_to(self, i: int, deg: float, verb: str = "MOVE") -> bool:
        if not self._check_move():
            return False
        a = self.axes[i]
        deg = self._clamp_target(a, deg)
        a.target, a.jog = deg, 0
        self.log("tx", f"{a.name} {verb} {deg:.3f} ({a.bits(deg)} bits) V{self.speed_pct}")
        return True

    def jog_start(self, i: int, direction: int) -> bool:
        if not self._check_move():
            return False
        a = self.axes[i]
        a.jog, a.target = direction, None
        self.log("tx", f"{a.name} JOG {'+' if direction > 0 else '-'} V{self.speed_pct}")
        return True

    def jog_stop(self, i: int) -> None:
        a = self.axes[i]
        if a.jog:
            a.jog = 0
            self.log("tx", f"{a.name} STOP")

    def set_output(self, bit: int, on: bool) -> None:
        self.outputs[bit] = on
        name = f"  ({OUTPUT_NAMES[bit]})" if OUTPUT_NAMES[bit] else ""
        self.log("tx", f"OUT {bit} {int(on)}{name}")

    def send_raw(self, text: str) -> None:
        self.log("tx", text)
        parts = text.split()
        if len(parts) == 3 and parts[1].upper() == "MOVE" and parts[0].upper().startswith("J"):
            try:
                i, deg = int(parts[0][1:]) - 1, float(parts[2])
            except ValueError:
                i, deg = -1, 0.0
            if 0 <= i < len(self.axes):
                self.move_to(i, deg)
                return
        self.log("rx", "OK (סימולציה: הפקודה לא פוענחה)")

    # ---- simulation ----
    def tick(self, dt: float) -> None:
        dt = min(dt, 0.1)
        if dt <= 0:
            return
        for a in self.axes:
            v = a.cfg.vmax * self.speed_pct / 100
            old = a.pos
            if a.jog:
                new = a.pos + a.jog * v * dt
                if self.soft_limits and not (a.cfg.min <= new <= a.cfg.max):
                    new = min(a.cfg.max, max(a.cfg.min, new))
                    if not a.soft_hit:
                        self.log("err", f"{a.name}: הגיע ללימיט תוכנה {new:.3f}°")
                    a.soft_hit, a.jog = True, 0
                a.pos = new
            elif a.target is not None:
                d, s = a.target - a.pos, v * dt
                if abs(d) <= s:
                    a.pos, a.target = a.target, None
                    self.log("rx", f"{a.name} IN POS {a.pos:.3f} ({a.bits()} bits)")
                    if a.at_home:
                        a.homed = True
                else:
                    a.pos += s if d > 0 else -s
            if not a.hw_hit and (a.at_hw_min or a.at_hw_max):
                hit_min = a.at_hw_min
                a.pos = a.cfg.min - HW_MARGIN if hit_min else a.cfg.max + HW_MARGIN
                a.hw_hit, self.alarm, self.servo = True, True, False
                self._stop_all()
                self.log("err", f"{a.name}: מתג גבול {'LIM−' if hit_min else 'LIM+'} הופעל. תקלה, הסרבו כובה")
            a.vel = (a.pos - old) / dt

    def inputs(self) -> list[bool]:
        ins = list(self.manual_inputs)
        ins[0] = not self.estop
        for i, a in enumerate(self.axes):
            ins[i + 1] = a.at_home
        return ins


# ---- settings file ----
def app_dir() -> Path:
    """Folder of the EXE when frozen by PyInstaller, otherwise the project folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def save_settings(path: Path, axes: list[Axis]) -> None:
    data = {"axes": {a.name: asdict(a.cfg) for a in axes}}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_settings(path: Path, axes: list[Axis]) -> bool:
    """Apply saved per-axis settings. Unknown keys and bad values are ignored."""
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {f.name for f in fields(AxisSettings)}
    for a in axes:
        for key, value in data.get("axes", {}).get(a.name, {}).items():
            if key in names and isinstance(value, (int, float)):
                setattr(a.cfg, key, float(value))
        if a.cfg.min > a.cfg.max:
            a.cfg.min, a.cfg.max = a.cfg.max, a.cfg.min
        if a.cfg.bits_per_deg <= 0:
            a.cfg.bits_per_deg = AxisSettings.bits_per_deg
    return True
