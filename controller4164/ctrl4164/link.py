"""Real SC-NT-4M controller over Ethernet (or RS-232), using the ASCII protocol in protocol.py.

A background thread owns the connection. GUI calls queue commands; the thread sends them,
then polls positions and status about 10 times a second. Logging goes through the log
callback, which the GUI makes thread-safe.

Commands used (all from the SC-AT manuals that came with the controller):
    _PS  position [counts]        _MS  motion status (0 = stopped)   _MO  motor on (1/0)
    _SR  status register          _EM  end of motion reason          XIP / XOP  I/O ports
    _MM=0 point-to-point, _MM=1 jog; _SM=0; _SP speed [counts/s]; _AP target [counts]
    _BG begin, _ST stop, _AB abort, _LL / _HL position limits [counts]
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from .model import N_BITS, Axis, BaseController, LogFn
from .protocol import ProtocolError, Reply, check_clauses

POLL_INTERVAL = 0.1


class LinkController(BaseController):
    simulated = False

    def __init__(self, axes: list[Axis], transport, log: LogFn | None = None):
        super().__init__(axes, log)
        self.transport = transport
        self.port = transport.describe()
        self.in_bits = [False] * N_BITS
        self.limits_read = False  # set by read_limits(); the GUI refreshes its table
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_poll = 0.0
        for a in self.axes:
            a.ms = a.sr = a.em = None
            a.motor_on = False

    # ---- thread ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ctrl4164-link", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self.transport.close()
        self.connected = False

    def disconnect(self) -> None:
        self.log("tx", "DISCONNECT")
        self.close()

    def _run(self) -> None:
        try:
            self.transport.open()
        except Exception as e:  # noqa: BLE001 - shown to the operator
            self.log("err", f"החיבור ל-{self.port} נכשל: {e}")
            return
        self.connected = True
        self.log("rx", f"CONNECTED {self.port}")
        try:
            self._poll()
            while not self._stop.is_set():
                try:
                    clauses, done = self._q.get(timeout=0.02)
                except queue.Empty:
                    if time.monotonic() - self._last_poll >= POLL_INTERVAL:
                        self._poll()
                    continue
                replies = self.transport.transact(clauses)
                self._log_replies(replies)
                if done:
                    done(replies)
        except (OSError, ProtocolError) as e:
            if not self._stop.is_set():
                self.log("err", f"התקשורת עם הבקר נותקה: {e}")
        finally:
            self.connected = False
            self.servo = False
            self.transport.close()

    def _log_replies(self, replies: list[Reply]) -> None:
        for r in replies:
            if r.ok:
                self.log("rx", f"{r.clause}  {r.text}>" if r.text else f"{r.clause}  >")
            else:
                self.log("err", f"{r.clause}  ?>  (הבקר דחה את הפקודה. EC מחזיק את קוד השגיאה)")

    def _send(self, clauses: list[str], done: Callable[[list[Reply]], None] | None = None) -> None:
        clauses = check_clauses(clauses)  # raises ValueError here, not in the link thread
        self.log("tx", ";".join(clauses))
        self._q.put((clauses, done))

    def _send_now(self, clauses: list[str]) -> None:
        """Drop everything queued and send this next (used by E-stop)."""
        with self._q.mutex:
            self._q.queue.clear()
        self._send(clauses)

    # ---- polling ----
    def _poll(self) -> None:
        self._last_poll = now = time.monotonic()
        letters = [a.letter for a in self.axes]
        main = ([f"{L}PS" for L in letters] + [f"{L}MS" for L in letters]
                + [f"{L}MO" for L in letters] + ["XIP", "XOP"])
        extra = [f"{L}SR" for L in letters] + [f"{L}EM" for L in letters]
        r1 = self.transport.transact(main)
        r2 = self.transport.transact(extra)
        self._apply_poll(r1, r2, now)

    def _apply_poll(self, r1: list[Reply], r2: list[Reply], now: float) -> None:
        n = len(self.axes)
        dt = max(1e-3, now - getattr(self, "_prev_poll_time", now - POLL_INTERVAL))
        self._prev_poll_time = now
        for i, a in enumerate(self.axes):
            ps, ms, mo = r1[i].value, r1[n + i].value, r1[2 * n + i].value
            if ps is not None:
                new = ps / a.cfg.bits_per_deg
                a.vel = (new - a.pos) / dt
                a.pos = new
            if mo is not None:
                a.motor_on = mo != 0
            if ms is not None:
                was_moving = a.ms not in (None, 0)
                a.ms = ms
                if ms == 0 and was_moving:
                    a.target = None
                    a.vel = 0.0
                    self.log("rx", f"{a.name} נעצר ב-{a.pos:.3f}° ({a.bits()} bits)")
                    if a.at_home:
                        a.homed = True
            if ms == 0 and a.target is not None and abs(a.bits() - a.bits(a.target)) <= 1:
                a.target = None  # short move finished between two polls
            a.sr = r2[i].value
            a.em = r2[n + i].value
            a.soft_hit = a.em in (4, 5)
        self.servo = all(a.motor_on for a in self.axes)
        ip, op = r1[3 * n].value, r1[3 * n + 1].value
        if ip is not None:
            self.in_bits = [bool(ip >> b & 1) for b in range(N_BITS)]
        if op is not None:
            self.outputs = [bool(op >> b & 1) for b in range(N_BITS)]

    # ---- commands ----
    def why_cant_move(self) -> str | None:
        reason = super().why_cant_move()
        if reason and "סרבו" in reason:
            off = [a.name for a in self.axes if not a.motor_on]
            return f"המנוע כבוי ב-{', '.join(off)}. הדלק סרבו לפני תנועה."
        return reason

    def set_servo(self, on: bool) -> bool:
        if not self.connected:
            self.log("err", "אין חיבור לבקר.")
            return False
        if on and self.estop:
            self.log("err", 'עצירת חירום פעילה. לחץ "איפוס תקלה" לפני הדלקת הסרבו.')
            return False
        self._send([f"{a.letter}MO={int(on)}" for a in self.axes])
        return True

    def emergency_stop(self) -> None:
        self.estop = True
        if self.connected:
            self._send_now([f"{a.letter}AB" for a in self.axes] + [f"{a.letter}MO=0" for a in self.axes])
        for a in self.axes:
            a.target, a.jog = None, 0
        self.log("err", "E-STOP (תוכנה): נשלחה עצירה וכיבוי מנועים. זה לא מחליף את לחצן החירום של המכונה.")

    def reset(self) -> None:
        self.estop = self.alarm = False
        for a in self.axes:
            a.soft_hit = a.hw_hit = False
        self.log("tx", "RESET (מקומי)")

    def move_to(self, i: int, deg: float, verb: str = "MOVE") -> bool:
        if not self._check_move():
            return False
        a = self.axes[i]
        if a.moving:
            self.log("err", f"{a.name} עדיין בתנועה. חכה שייעצר או לחץ עצירה.")
            return False
        deg = self._clamp_target(a, deg)
        a.target, a.jog = deg, 0
        L = a.letter
        self._send([f"{L}MM=0", f"{L}SM=0", f"{L}SP={self.speed_bits(a)}", f"{L}AP={a.bits(deg)}", f"{L}BG"])
        return True

    def jog_start(self, i: int, direction: int) -> bool:
        if not self._check_move():
            return False
        a = self.axes[i]
        if a.moving:
            self.log("err", f"{a.name} עדיין בתנועה.")
            return False
        a.jog, a.target = direction, None
        L = a.letter
        # Jog mode: the controller stops by itself at LL/HL (New Motion Modes manual, section 6).
        self._send([f"{L}MM=1", f"{L}SM=0", f"{L}SP={direction * self.speed_bits(a)}", f"{L}BG"])
        return True

    def jog_stop(self, i: int) -> None:
        a = self.axes[i]
        if a.jog:
            a.jog = 0
            self._send([f"{a.letter}ST"])

    def stop_all(self) -> None:
        self._send([f"{a.letter}ST" for a in self.axes])

    def set_output(self, bit: int, on: bool) -> None:
        self.log("err", "הדלקת יציאה עוד לא נתמכת: הפקודה לא מופיעה בתיעוד שהתקבל. "
                        "המצב מוצג לפי OP מהבקר.")

    def send_raw(self, text: str) -> None:
        clauses = [c.strip() for c in text.replace("\r", ";").split(";") if c.strip()]
        if not self.connected:
            self.log("err", "אין חיבור לבקר.")
        elif clauses:
            try:
                self._send(clauses)
            except ValueError as e:
                self.log("err", str(e))

    def read_limits(self) -> None:
        clauses = [f"{a.letter}{k}" for a in self.axes for k in ("LL", "HL")]

        def done(replies: list[Reply]) -> None:
            for j, a in enumerate(self.axes):
                ll, hl = replies[2 * j].value, replies[2 * j + 1].value
                if ll is not None and hl is not None:
                    a.cfg.min, a.cfg.max = ll / a.cfg.bits_per_deg, hl / a.cfg.bits_per_deg
            self.limits_read = True

        self._send(clauses, done)

    def write_limits(self) -> None:
        self._send([f"{a.letter}{k}={a.bits(v)}" for a in self.axes
                    for k, v in (("LL", a.cfg.min), ("HL", a.cfg.max))])

    def inputs(self) -> list[bool]:
        return list(self.in_bits)
