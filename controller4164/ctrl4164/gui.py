"""PySide6 operator screen for the 4164 controller."""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .link import LinkController
from .model import (
    END_MOTION_REASONS, HW_MARGIN, INPUT_NAMES, MOTOR_FAULTS, N_BITS, N_OUTPUTS, OUTPUT_NAMES, SETTINGS_FILE, STATUS_BITS, Axis,
    DEFAULT_HOME_MACRO, SimController, app_dir, load_app_settings, load_settings, save_settings,
)
from .protocol import LAN_PORTS, SERIAL_BAUDS, LanTransport, SerialTransport
from .version import __version__

SIM_PORT = "סימולציה"
LAN_PORT = "Ethernet (LAN)"
DEFAULT_IP = "10.0.0.105"
SIM_NOTE = "מצב סימולציה: הפקודות לא נשלחות לבקר. לחיבור אמיתי בחר Ethernet או פורט COM ולחץ התחבר."
# Connection banner: (text, background, text colour). The numbers on screen are real only in "link".
BANNER = {
    "link": ("●  מחובר לבקר  ·  {port}  ·  {fw}", "#1f9d55", "#ffffff"),
    "connecting": ("◌  מתחבר לבקר  ·  {port} ...", "#e08a00", "#1c1400"),
    "down": ("✖  לא מחובר לבקר: החיבור נכשל או נותק. המספרים במסך לא מעודכנים. לחץ \"התחבר\".", "#d62f2f", "#ffffff"),
    "sim": ("✖  לא מחובר לבקר  ·  מצב סימולציה: המספרים במסך לא מהבקר. לחץ \"התחבר\".", "#d62f2f", "#ffffff"),
}
LINK_NOTE = ("מחובר לבקר SC-NT-4M. הפקודות זזות באמת. עצירת החירום בתוכנה שולחת AB ו-MO=0, "
             "והיא לא מחליפה את לחצן החירום של המכונה.")
TICK_MS = 20
LRM = "\u200e"
MONO = QFont("Consolas")
MONO.setStyleHint(QFont.Monospace)

LED_COLORS = {"off": "#c3cbd3", "on": "#1f9d55", "bad": "#d62f2f", "warn": "#d99a00", "out": "#e08a00"}
LOG_COLORS = {"tx": "#b86e00", "rx": "#1f8a4c", "err": "#c62f2f"}


def fmt_num(v: float) -> str:
    """Up to 4 decimals, no trailing zeros: 4551.1111, 800, 2.1972."""
    return f"{v:.4f}".rstrip("0").rstrip(".")


def mono(size: int, bold: bool = False) -> QFont:
    f = QFont(MONO)
    f.setPointSize(size)
    f.setBold(bold)
    return f


class Led(QLabel):
    def __init__(self, size: int = 12):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self.set("off")

    def set(self, state: str) -> None:
        self.setStyleSheet(f"background:{LED_COLORS[state]}; border-radius:{self._size // 2}px;")


class Flag(QLabel):
    """Small labelled state chip: LIM−, LIM+, soft limit, Home, moving."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.set("")

    def set(self, state: str) -> None:
        color = {"bad": LED_COLORS["bad"], "ok": LED_COLORS["on"], "warn": LED_COLORS["warn"]}.get(state)
        if color:
            self.setStyleSheet(f"color:{color}; border:1px solid {color}; border-radius:4px; padding:1px 6px; font-weight:bold;")
        else:
            self.setStyleSheet("color:#7a8794; border:1px solid #c9d1da; border-radius:4px; padding:1px 6px;")


class LimitBar(QWidget):
    """Position between the hardware limits; the green part is the soft-limit range."""

    def __init__(self, axis: Axis):
        super().__init__()
        self.axis = axis
        self.setFixedHeight(16)
        self.setLayoutDirection(Qt.LeftToRight)

    def paintEvent(self, _event) -> None:
        a, p = self.axis, QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        lo, hi = a.cfg.min - HW_MARGIN, a.cfg.max + HW_MARGIN

        def x(deg: float) -> float:
            return max(0.0, min(1.0, (deg - lo) / (hi - lo))) * w

        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e1e6ec"))
        p.drawRoundedRect(0, 4, w, 8, 4, 4)
        p.setBrush(QColor(31, 157, 85, 110))
        p.drawRoundedRect(x(a.cfg.min), 4, x(a.cfg.max) - x(a.cfg.min), 8, 4, 4)
        p.setBrush(QColor(LED_COLORS["out"]))
        p.drawRoundedRect(x(a.pos) - 2, 0, 4, h, 2, 2)
        p.end()


class AxisPanel(QGroupBox):
    def __init__(self, win: "MainWindow", index: int):
        self.win, self.i = win, index
        self.axis = win.ctrl.axes[index]
        super().__init__(f"{self.axis.name}  ·  ציר {self.axis.letter}")
        lay = QVBoxLayout(self)

        self.readout = QLabel()
        self.readout.setFont(mono(24, bold=True))
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.readout.setLayoutDirection(Qt.LeftToRight)
        self.set_live(False)
        top = QHBoxLayout()
        self.servo_btn = QPushButton()
        self.servo_btn.setMinimumWidth(110)
        self.servo_btn.clicked.connect(lambda: self.win.ctrl.set_axis_servo(self.i, not self.axis.motor_on))
        self.macro_led = Led()
        self.macro_lbl = QLabel("מאקרו")
        self.macro_lbl.setStyleSheet("color:#5d6b7a;")
        top.addWidget(self.servo_btn)
        top.addStretch(1)
        top.addWidget(self.macro_lbl)
        top.addWidget(self.macro_led)
        lay.addLayout(top)
        lay.addWidget(self.readout)

        self.sub1, self.sub2, self.sub3 = QLabel(), QLabel(), QLabel()
        for s in (self.sub1, self.sub2, self.sub3):
            s.setStyleSheet("color:#5d6b7a;")
            lay.addWidget(s)

        self.bar = LimitBar(self.axis)
        lay.addWidget(self.bar)
        self.range_lbl = QLabel()
        self.range_lbl.setStyleSheet("color:#5d6b7a;")
        self.range_lbl.setLayoutDirection(Qt.LeftToRight)
        lay.addWidget(self.range_lbl)

        flags = QHBoxLayout()
        self.f_min, self.f_max = Flag("LIM−"), Flag("LIM+")
        self.f_soft, self.f_home, self.f_move = Flag("לימיט תוכנה"), Flag("Home"), Flag("בתנועה")
        for f in (self.f_min, self.f_max, self.f_soft, self.f_home, self.f_move):
            flags.addWidget(f)
        flags.addStretch(1)
        lay.addLayout(flags)

        jog = QHBoxLayout()
        jog_w = QWidget()
        jog_w.setLayout(jog)
        jog_w.setLayoutDirection(Qt.LeftToRight)
        self.minus, home, self.plus = QPushButton("−"), QPushButton("⌂ Home"), QPushButton("+")
        for b in (self.minus, home, self.plus):
            b.setMinimumHeight(36)
            b.setFont(mono(13, bold=True))
            jog.addWidget(b)
        self._bind_jog(self.minus, -1)
        self._bind_jog(self.plus, +1)
        home.clicked.connect(lambda: self.win.ctrl.home(self.i))
        lay.addWidget(jog_w)

        go = QHBoxLayout()
        self.goto = QDoubleSpinBox()
        self.goto.setDecimals(3)
        self.goto.setRange(-100000, 100000)
        self.goto.setSuffix("°")
        self.goto.setLayoutDirection(Qt.LeftToRight)
        go_btn = QPushButton("סע ליעד")
        go_btn.clicked.connect(lambda: self.win.ctrl.move_to(self.i, self.goto.value()))
        go.addWidget(QLabel("זווית יעד:"))
        go.addWidget(self.goto, 1)
        go.addWidget(go_btn)
        lay.addLayout(go)

    def _bind_jog(self, btn: QPushButton, direction: int) -> None:
        def pressed() -> None:
            step = self.win.step_deg()
            if step > 0:
                self.win.ctrl.step(self.i, direction, step)
            else:
                self.win.ctrl.jog_start(self.i, direction)

        btn.pressed.connect(pressed)
        btn.released.connect(lambda: self.win.ctrl.jog_stop(self.i))

    def set_live(self, live: bool) -> None:
        """Dark digits when the angle comes from the controller, grey and dashed when it does not."""
        self.readout.setStyleSheet(
            "background:#eef1f4; color:#111820; border:2px solid #1f9d55; border-radius:6px; padding:4px 10px;"
            if live else
            "background:#eef1f4; color:#9aa5b1; border:2px dashed #d62f2f; border-radius:6px; padding:4px 10px;")
        self.readout.setToolTip("" if live else "לא מחובר לבקר: הזווית לא מהבקר")

    def refresh(self) -> None:
        a = self.axis
        self.readout.setText(f"{a.pos:+.3f}°")
        target = f"{a.target:+.3f}°" if a.target is not None else "—"
        self.sub1.setText(f"יעד: {target}    מהירות: {a.vel:+.1f} °/ש'")
        self.sub2.setText(f"ביטים: {a.bits():,}    קבוע: {fmt_num(a.cfg.bits_per_deg)} ביט/°")
        self.range_lbl.setText(f"{LRM}{a.cfg.min:g}°  …  {a.cfg.max:g}°")
        on = a.motor_on
        if on != getattr(self, "_servo_shown", None):
            self._servo_shown = on
            self.servo_btn.setText("Servo ON" if on else "Servo OFF")
            self.servo_btn.setStyleSheet(
                f"QPushButton {{ background:{'#1f9d55' if on else '#d62f2f'}; color:white; font-weight:bold;"
                " border-radius:4px; padding:4px 10px; }")
        if a.macro_running is None:
            self.macro_led.set("off")
            self.macro_lbl.setToolTip("")
        else:
            self.macro_led.set("bad" if a.macro_rte else "on" if a.macro_running else "off")
            self.macro_lbl.setToolTip(f"מאקרו {a.letter}: " + ("נעצר בשגיאה" if a.macro_rte else
                                      "רץ" if a.macro_running else "לא רץ"))
        if a.ms is None:
            self.sub3.setText("")
        else:
            em = "—" if a.em is None else f"{a.em} {END_MOTION_REASONS.get(a.em, '')}".strip()
            parts = [f"MS: {a.ms}", f"סיום: {em}"]
            if a.pe is not None:
                parts.insert(0, f"שגיאת מיקום: {a.pe}")
            if a.in_target:
                parts.append("במטרה")
            if a.mf:
                parts.append(f"תקלה MF={a.mf} {MOTOR_FAULTS.get(a.mf, '')}".rstrip())
            if a.drv_fault:
                parts.append("תקלת דרייבר")
            self.sub3.setText("    ".join(parts))
        self.f_min.set("bad" if a.at_hw_min else "")
        self.f_max.set("bad" if a.at_hw_max else "")
        self.f_soft.set("warn" if a.at_soft_limit else "")
        self.f_home.set("warn" if a.homing else "ok" if a.homed else "")
        self.f_home.setText("Home…" if a.homing else "Home")
        self.f_move.set("ok" if a.moving else "")
        alarm = a.hw_hit or a.drv_fault or bool(a.mf)
        if alarm != getattr(self, "_alarm_style", None):
            self._alarm_style = alarm
            self.setStyleSheet(
                "QGroupBox { border:2px solid #d62f2f; border-radius:4px; margin-top:1.4em; }"
                " QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top right;"
                " padding: 0 6px; color:#d62f2f; font-weight:bold; }" if alarm else "")
        self.bar.update()


class MainWindow(QMainWindow):
    LIMIT_COLS = [("min", "מינימום (°)"), ("max", "מקסימום (°)"), ("vmax", "מהירות מקס' (°/ש')"),
                  ("accel", "תאוצה (°/ש²)"), ("home", "מיקום Home (°)"), ("bits_per_deg", "קבוע: ביטים למעלה")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"בקר 4164  ·  גרסה {__version__}")
        self.setLayoutDirection(Qt.RightToLeft)
        self._log_q: queue.Queue = queue.Queue()
        self.ctrl = SimController(log=self.log)
        # The last settings file the operator saved or loaded (one file per controller), else the default.
        self.prefs = QSettings("ctrl4164", "Controller4164")
        last = self.prefs.value("last_settings_file", "")
        self.settings_path = Path(last) if last and Path(last).exists() else app_dir() / SETTINGS_FILE

        root = QWidget()
        scroll = QScrollArea()  # small screens: scroll instead of cutting off the limits table
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(root)
        outer.addWidget(self._top_bar())
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setFont(QFont(self.font().family(), 13, QFont.Bold))
        outer.addWidget(self.banner)
        self.note = QLabel(SIM_NOTE)
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        self._conn_state = None

        body = QHBoxLayout()
        outer.addLayout(body, 1)
        body.addWidget(self._axes_area(), 3)
        body.addWidget(self._side_panel(), 2)

        if self._load_settings():
            self.log("rx", f"הגדרות נטענו מ-{self.settings_path}")
        self._show_settings_file()
        self._fill_limits_table()

        self._last = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)
        self.refresh()

    # ---- building ----
    def _top_bar(self) -> QWidget:
        w = QFrame()
        rows = QVBoxLayout(w)
        rows.setContentsMargins(0, 0, 0, 0)
        lay, conn = QHBoxLayout(), QHBoxLayout()
        rows.addLayout(lay)
        rows.addLayout(conn)
        title = QLabel(f"<b style='font-size:18pt'>בקר 4164</b>  <span style='color:#5d6b7a'>גרסה {__version__}</span>")
        lay.addWidget(title)
        self.fw_lbl = QLabel()
        self.fw_lbl.setStyleSheet("color:#5d6b7a;")
        self.fw_lbl.setLayoutDirection(Qt.LeftToRight)

        lay.addStretch(1)

        self.port = QComboBox()
        self.port.addItems([SIM_PORT, LAN_PORT])
        try:
            from serial.tools import list_ports
            self.port.addItems([p.device for p in list_ports.comports()])
        except Exception:
            pass
        self.port.setCurrentText(LAN_PORT)
        self.ip = QLineEdit(DEFAULT_IP)
        self.ip.setLayoutDirection(Qt.LeftToRight)
        self.ip.setFixedWidth(120)
        self.lan_port = QComboBox()
        self.lan_port.addItems([str(p) for p in LAN_PORTS])
        self.lan_port.setEditable(True)
        self.baud = QComboBox()
        self.baud.addItems([str(b) for b in SERIAL_BAUDS])
        self.baud.setCurrentText("115200")
        self.lbl_ip, self.lbl_lan, self.lbl_baud = QLabel("IP:"), QLabel("פורט TCP:"), QLabel("מהירות:")
        self.port.currentTextChanged.connect(self._port_kind_changed)
        self.conn_btn = QPushButton("התחבר")
        self.conn_btn.clicked.connect(self._toggle_conn)
        self.conn_led = Led()
        for x in (QLabel("חיבור:"), self.port, self.lbl_ip, self.ip, self.lbl_lan, self.lan_port,
                  self.lbl_baud, self.baud, self.conn_btn, self.conn_led):
            conn.addWidget(x)
        conn.addWidget(self.fw_lbl)
        conn.addStretch(1)
        self._port_kind_changed(self.port.currentText())

        self.servo_btn = QPushButton()
        self.servo_btn.clicked.connect(lambda: self.ctrl.set_servo(not self.ctrl.servo))
        kill = QPushButton("עצור מאקרו (QK)")
        kill.setToolTip("עוצר את כל תוכניות המאקרו בבקר ואת התנועה בכל הצירים")
        kill.clicked.connect(lambda: self.ctrl.kill_macros())
        lay.addWidget(kill)
        home_all = QPushButton("Home לכל הצירים")
        home_all.clicked.connect(lambda: self.ctrl.home_all())
        reset = QPushButton("איפוס תקלה")
        reset.clicked.connect(lambda: self.ctrl.reset())
        estop = QPushButton("עצירת חירום")
        estop.setStyleSheet("QPushButton { background:#d62f2f; color:white; font-weight:bold; font-size:13pt;"
                            " border:3px solid #e08a00; border-radius:18px; padding:6px 18px; }")
        estop.clicked.connect(lambda: self.ctrl.emergency_stop())
        for x in (self.servo_btn, home_all, reset, estop):
            lay.addWidget(x)
        return w

    def _axes_area(self) -> QWidget:
        box = QGroupBox("צירים")
        lay = QVBoxLayout(box)

        controls = QHBoxLayout()
        self.step = QComboBox()
        for label, val in [("רציף (החזק)", 0.0), ("0.001°", 0.001), ("0.01°", 0.01), ("0.1°", 0.1),
                           ("1°", 1.0), ("5°", 5.0), ("10°", 10.0), ("45°", 45.0)]:
            self.step.addItem(label, val)
        self.step.setCurrentText("1°")
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(1, 100)
        self.speed.setValue(self.ctrl.speed_pct)
        self.speed_lbl = QLabel()
        self.speed.valueChanged.connect(self._speed_changed)
        self.soft = QCheckBox("לימיטים בתוכנה")
        self.soft.setChecked(True)
        self.soft.toggled.connect(lambda on: self.ctrl.set_soft_limits(on))
        self.home_mode = QComboBox()
        self.home_mode.addItems(["נסיעה למיקום Home", "מאקרו Home בבקר"])
        self.home_mode.setCurrentIndex(1)  # the controller has #HOME_X ... #HOME_W macros
        self.home_label = QLineEdit(DEFAULT_HOME_MACRO)
        self.home_label.setLayoutDirection(Qt.LeftToRight)
        self.home_label.setFixedWidth(100)
        self.home_label.setToolTip("שם התווית במאקרו של הבקר. {L} מוחלף באות הציר: #HOME_X, #HOME_Y ...\n"
                                   "נשלח כ-XQE,#HOME_X. עד 6 תווים אחרי #.")
        self.home_mode.currentIndexChanged.connect(self._home_mode_changed)
        self.home_label.editingFinished.connect(self._home_mode_changed)
        for x in (QLabel("צעד:"), self.step, QLabel("מהירות:"), self.speed, self.speed_lbl, self.soft):
            controls.addWidget(x)
        controls.addStretch(1)
        lay.addLayout(controls)
        home_row = QHBoxLayout()
        for x in (QLabel("כפתור Home:"), self.home_mode, self.home_label):
            home_row.addWidget(x)
        home_row.addStretch(1)
        lay.addLayout(home_row)

        grid = QGridLayout()
        self.panels = [AxisPanel(self, i) for i in range(len(self.ctrl.axes))]
        for i, p in enumerate(self.panels):
            grid.addWidget(p, i // 2, i % 2)
        lay.addLayout(grid)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>לימיטים וקבועים</b>"))
        head.addStretch(1)
        self.read_pa = QPushButton("קרא קבוע/מהירות/תאוצה")
        self.read_pa.setToolTip("קורא מכל ציר את PA[43] (ביטים למעלה), PA[52] (מהירות מקס' בביטים/ש')\n"
                                "ו-PA[62] (תאוצה בביטים/ש²), ומציב בטבלה. נקרא גם אוטומטית בכל התחברות.")
        self.read_pa.clicked.connect(lambda: self.ctrl.read_axis_params())
        head.addWidget(self.read_pa)
        self.read_lim = QPushButton("קרא לימיטים מהבקר")
        self.read_lim.setToolTip("LL / HL של כל ציר, מומרים למעלות לפי הקבוע")
        self.read_lim.clicked.connect(lambda: self.ctrl.read_limits())
        self.write_lim = QPushButton("כתוב לימיטים לבקר")
        self.write_lim.setToolTip("שולח LL / HL לכל ציר (בלי SV, כלומר לא נשמר בפלאש של הבקר)")
        self.write_lim.clicked.connect(lambda: self.ctrl.write_limits())
        head.addWidget(self.read_lim)
        head.addWidget(self.write_lim)
        save = QPushButton("שמור הגדרות לקובץ...")
        save.setToolTip("שומר את הטבלה, ה-IP והפורט ומצב ה-Home לקובץ שתבחר. אפשר קובץ נפרד לכל בקר.")
        save.clicked.connect(self._save_settings)
        load = QPushButton("טען הגדרות מקובץ...")
        load.setToolTip("טוען קובץ הגדרות שנשמר קודם. הקובץ האחרון נטען אוטומטית בפתיחה.")
        load.clicked.connect(self._reload_settings)
        self.sv_btn = QPushButton("כבה מנועים ושמור בבקר")
        self.sv_btn.setToolTip("מכבה את המנועים בכל הצירים (MO=0) ושומר את הפרמטרים בפלאש של הבקר (XSV,1),\n"
                               "כך שיישארו אחרי RS או כיבוי. תוכנית המאקרו לא נשמרת מחדש.")
        self.sv_btn.clicked.connect(self._save_flash)
        head.addWidget(self.sv_btn)
        head.addWidget(save)
        head.addWidget(load)
        lay.addLayout(head)

        self.table = QTableWidget(len(self.ctrl.axes), len(self.LIMIT_COLS))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.LIMIT_COLS])
        self.table.setVerticalHeaderLabels([a.name for a in self.ctrl.axes])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.table.setFixedHeight(self.table.verticalHeader().defaultSectionSize() * (len(self.ctrl.axes) + 1) + 6)
        self.table.cellChanged.connect(self._limit_edited)
        lay.addWidget(self.table)
        return box

    def _side_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)

        sw = QGroupBox("מילת סטטוס")
        swl = QVBoxLayout(sw)
        self.sw_hex = QLabel()
        self.sw_hex.setFont(mono(18, bold=True))
        self.sw_bin = QLabel()
        self.sw_bin.setFont(mono(9))
        for lbl in (self.sw_hex, self.sw_bin):
            lbl.setLayoutDirection(Qt.LeftToRight)
            swl.addWidget(lbl)
        legend = QGridLayout()
        self.sw_leds = []
        for b, (_, he) in enumerate(STATUS_BITS):
            led = Led()
            self.sw_leds.append(led)
            cell = QHBoxLayout()
            bit = QLabel(f"b{b}")
            bit.setFont(mono(9))
            bit.setStyleSheet("color:#7a8794;")
            cell.addWidget(led)
            cell.addWidget(bit)
            cell.addWidget(QLabel(he))
            cell.addStretch(1)
            legend.addLayout(cell, b // 2, b % 2)
        swl.addLayout(legend)
        lay.addWidget(sw)

        ins = QGroupBox("כניסות (IN)")
        inl = QGridLayout(ins)
        self.in_leds = []
        for b in range(N_BITS):
            led = Led(14)
            led.setToolTip(f"Din{b + 1}" + (f" (בסימולציה: {INPUT_NAMES[b]})" if INPUT_NAMES[b] else ""))
            self.in_leds.append(led)
            cell = QVBoxLayout()
            cell.addWidget(led, 0, Qt.AlignHCenter)
            cell.addWidget(QLabel(f"I{b + 1}"), 0, Qt.AlignHCenter)
            inl.addLayout(cell, b // 8, b % 8)
        ins.setLayoutDirection(Qt.LeftToRight)
        lay.addWidget(ins)

        outs = QGroupBox("יציאות (OUT), לחץ להחלפה")
        outl = QGridLayout(outs)
        self.out_btns = []
        for b in range(N_OUTPUTS):
            btn = QPushButton(f"O{b + 1}")
            btn.setMinimumWidth(40)  # Fusion's default minimum (~80px) makes 8 buttons too wide
            btn.setCheckable(True)
            btn.setToolTip(OUTPUT_NAMES[b] or f"OUT{b}")
            btn.setStyleSheet(f"QPushButton:checked {{ background:{LED_COLORS['out']}; color:black; font-weight:bold; }}")
            btn.toggled.connect(lambda on, b=b: self.ctrl.set_output(b, on))
            self.out_btns.append(btn)
            outl.addWidget(btn, b // 8, b % 8)
        outs.setLayoutDirection(Qt.LeftToRight)
        lay.addWidget(outs)

        con = QGroupBox("קונסולה")
        cl = QVBoxLayout(con)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(mono(9))
        self.console.setLayoutDirection(Qt.LeftToRight)
        self.console.setMaximumBlockCount(1000)
        cl.addWidget(self.console)
        cmd = QHBoxLayout()
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("פקודה לבקר, למשל: XPS  או  XSP;YSP  (בסימולציה: J1 MOVE 30)")
        self.cmd.setLayoutDirection(Qt.LeftToRight)
        self.cmd.returnPressed.connect(self._send_cmd)
        send = QPushButton("שלח")
        send.clicked.connect(self._send_cmd)
        cmd.addWidget(self.cmd, 1)
        cmd.addWidget(send)
        cl.addLayout(cmd)
        lay.addWidget(con, 1)
        return w

    # ---- actions ----
    def log(self, kind: str, text: str) -> None:
        """Safe from any thread: lines are queued and written by the GUI timer."""
        self._log_q.put((time.strftime("%H:%M:%S"), kind, text))

    def _flush_log(self) -> None:
        while True:
            try:
                stamp, kind, text = self._log_q.get_nowait()
            except queue.Empty:
                return
            self._write_log(stamp, kind, text)

    def _write_log(self, stamp: str, kind: str, text: str) -> None:
        tag = {"tx": "TX", "rx": "RX", "err": "ERR"}[kind]
        self.console.appendHtml(
            f"<span style='color:#7a8794'>{stamp}</span> <span style='color:{LOG_COLORS[kind]}'>{tag}</span> "
            + text.replace("&", "&amp;").replace("<", "&lt;"))
        bar = self.console.verticalScrollBar()
        bar.setValue(bar.maximum())  # always show the newest line

    def step_deg(self) -> float:
        return float(self.step.currentData())

    def _speed_changed(self, value: int) -> None:
        self.ctrl.speed_pct = value
        self.speed_lbl.setText(f"{value}%")

    def _port_kind_changed(self, kind: str) -> None:
        lan, serial = kind == LAN_PORT, kind not in (LAN_PORT, SIM_PORT)
        for w in (self.lbl_ip, self.ip, self.lbl_lan, self.lan_port):
            w.setVisible(lan)
        for w in (self.lbl_baud, self.baud):
            w.setVisible(serial)

    def _connecting(self) -> bool:
        c = self.ctrl
        return isinstance(c, LinkController) and not c.connected and c._thread is not None \
            and c._thread.is_alive()

    def _toggle_conn(self) -> None:
        if self.ctrl.connected or self._connecting():
            self.ctrl.disconnect()
            return
        old = self.ctrl
        old.close()
        kind = self.port.currentText()
        if kind == SIM_PORT:
            self.ctrl = SimController(log=self.log, axes=old.axes)
        else:
            if kind == LAN_PORT:
                transport = LanTransport(self.ip.text().strip(), int(self.lan_port.currentText()))
            else:
                transport = SerialTransport(kind, int(self.baud.currentText()))
            self.ctrl = LinkController(old.axes, transport, log=self.log)
        self.ctrl.speed_pct = old.speed_pct
        self.ctrl.soft_limits = old.soft_limits
        self.ctrl.home_macro = old.home_macro
        if isinstance(self.ctrl, LinkController):
            self.log("tx", f"מתחבר ל-{self.ctrl.port} ...")
            self.ctrl.start()
        else:
            self.ctrl.connect(SIM_PORT, 0)

    def _send_cmd(self) -> None:
        text = self.cmd.text().strip()
        if not text:
            return
        if any("\u0590" <= ch <= "\u05ff" for ch in text):
            self.log("err", f"הפקודה '{text}' כתובה באותיות עבריות. החלף את המקלדת לאנגלית (Alt+Shift) ונסה שוב.")
            return
        # Keywords must be upper case; macro labels after '#' are case-sensitive, so keep them as typed.
        head, sep, label = text.partition("#")
        self.ctrl.send_raw(head.upper() + sep + label)
        self.cmd.clear()

    def _fill_limits_table(self) -> None:
        self.table.blockSignals(True)
        for r, a in enumerate(self.ctrl.axes):
            for c, (key, _) in enumerate(self.LIMIT_COLS):
                item = QTableWidgetItem(LRM + fmt_num(getattr(a.cfg, key)))  # LRM keeps "-200" from showing as "200-"
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)

    def _limit_edited(self, row: int, col: int) -> None:
        a, key = self.ctrl.axes[row], self.LIMIT_COLS[col][0]
        try:
            value = float(self.table.item(row, col).text().replace(LRM, "").replace(",", ".").strip())
        except ValueError:
            value = None
        if value is None or (key in ("bits_per_deg", "vmax") and value <= 0) or (key == "accel" and value < 0):
            self.log("err", f"{a.name}: ערך לא תקין ב'{self.LIMIT_COLS[col][1]}'. הערך הקודם הוחזר.")
        else:
            setattr(a.cfg, key, value)
            if a.cfg.min > a.cfg.max:
                a.cfg.min, a.cfg.max = a.cfg.max, a.cfg.min
            self.log("tx", f"{a.name} SET {key.upper()} {value:g}")
        self._fill_limits_table()

    def _load_settings(self) -> bool:
        try:
            ok = load_settings(self.settings_path, self.ctrl.axes)
            app = load_app_settings(self.settings_path)
        except (OSError, ValueError) as e:
            self.log("err", f"לא הצלחתי לקרוא את {self.settings_path}: {e}")
            return False
        if "ip" in app:
            self.ip.setText(app["ip"])
        if "lan_port" in app:
            self.lan_port.setEditText(app["lan_port"])
        if app.get("connection") in (SIM_PORT, LAN_PORT):
            self.port.setCurrentText(app["connection"])
        if "home_label" in app:
            self.home_label.setText(app["home_label"])
        # "home_mode_v2" is written since v0.7.0. Older files saved "position" only because it was the
        # default then, so they keep the macro default instead.
        if "home_mode_v2" in app:
            self.home_mode.setCurrentIndex(1 if app["home_mode_v2"] == "macro" else 0)
        self._home_mode_changed()
        return ok

    def _show_settings_file(self) -> None:
        self.statusBar().showMessage(f"גרסה {__version__}    ·    קובץ הגדרות: {self.settings_path.name}")
        status = "מחובר" if getattr(self, "_conn_state", None) == "link" else "לא מחובר"
        self.setWindowTitle(f"[{status}]  בקר 4164  ·  {self.settings_path.stem}  ·  גרסה {__version__}")

    def _remember_settings_file(self, path: Path) -> None:
        self.settings_path = path
        self.prefs.setValue("last_settings_file", str(path))
        self._show_settings_file()

    def _reload_settings(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "טעינת הגדרות", str(self.settings_path.parent),
                                              "קובצי הגדרות (*.json);;כל הקבצים (*)")
        if not name:
            return
        old = self.settings_path
        self.settings_path = Path(name)
        if self._load_settings():
            self._fill_limits_table()
            self._remember_settings_file(Path(name))
            self.log("rx", f"הגדרות נטענו מ-{self.settings_path}")
        else:
            self.settings_path = old
            self.log("err", f"לא נטענו הגדרות מ-{name}")

    def _save_settings(self) -> None:
        name, _ = QFileDialog.getSaveFileName(self, "שמירת הגדרות", str(self.settings_path),
                                              "קובצי הגדרות (*.json)")
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() != ".json":
            path = path.with_name(path.name + ".json")
        try:
            app = {"ip": self.ip.text().strip(), "lan_port": self.lan_port.currentText(),
                   "connection": self.port.currentText(), "home_label": self.home_label.text().strip(),
                   "home_mode_v2": "macro" if self.home_mode.currentIndex() == 1 else "position"}
            save_settings(path, self.ctrl.axes, app)
            self._remember_settings_file(path)
            self.log("rx", f"הגדרות נשמרו ב-{self.settings_path}")
        except OSError as e:
            self.log("err", f"השמירה נכשלה: {e}")

    def _home_mode_changed(self, *_args) -> None:
        macro = self.home_mode.currentIndex() == 1
        self.home_label.setEnabled(macro)
        self.ctrl.home_macro = self.home_label.text().strip() if macro else ""

    def _save_flash(self) -> None:
        if self.ctrl.simulated or not self.ctrl.connected:
            self.ctrl.save_flash()
            return
        moving = [a.name for a in self.ctrl.axes if a.moving]
        if moving:
            QMessageBox.warning(self, "שמירה בבקר", f"{', '.join(moving)} בתנועה. עצור את התנועה ונסה שוב.")
            return
        answer = QMessageBox.question(
            self, "שמירה בבקר",
            "המנועים בכל הצירים יכובו (MO=0), ואז הפרמטרים של הבקר יישמרו בפלאש (XSV,1).\n\n"
            "הערכים יחליפו את מה שנשמר בבקר עד עכשיו. תוכנית המאקרו לא משתנה.\n"
            "אחרי השמירה צריך להדליק שוב את הסרבו.\n\nלהמשיך?")
        if answer == QMessageBox.Yes:
            self.ctrl.save_flash()

    def _tick(self) -> None:
        now = time.monotonic()
        self.ctrl.tick(now - self._last)
        self._last = now
        if getattr(self.ctrl, "limits_read", False):
            self.ctrl.limits_read = False
            self._fill_limits_table()
        self.refresh()
        self._flush_log()

    def closeEvent(self, event) -> None:
        self.ctrl.close()
        super().closeEvent(event)

    def refresh(self) -> None:
        c = self.ctrl
        for p in self.panels:
            p.refresh()
        connecting = self._connecting()
        self.conn_btn.setText("התנתק" if c.connected else "מתחבר..." if connecting else "התחבר")
        self.conn_led.set("on" if c.connected else "warn" if connecting else "off")
        real = not c.simulated
        state = "link" if real and c.connected else "connecting" if connecting else "down" if real else "sim"
        if state != self._conn_state:
            self._conn_state = state
            text, bg, fg = BANNER[state]
            self.banner.setText(text.format(port=getattr(c, "port", ""), fw=c.firmware or "SC-NT-4M"))
            self.banner.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:6px; padding:8px 12px;")
            self.note.setVisible(state == "link")
            self.note.setText(LINK_NOTE)
            self.note.setStyleSheet("background:#fff4e0; border:1px solid #e08a00; border-radius:6px; "
                                    "padding:4px 8px; color:#5a3b00;")
            for p in self.panels:
                p.set_live(state == "link")
            self._show_settings_file()
        elif state == "link" and c.firmware and c.firmware not in self.banner.text():
            self.banner.setText(BANNER["link"][0].format(port=c.port, fw=c.firmware))
        self.fw_lbl.setText(c.firmware if real and c.connected else "")
        self.sv_btn.setEnabled(real and c.connected)
        self.read_lim.setEnabled(real and c.connected)
        self.read_pa.setEnabled(real and c.connected)
        self.write_lim.setEnabled(real and c.connected)
        for b, (on, btn) in enumerate(zip(c.outputs, self.out_btns)):
            if btn.isChecked() != on:
                btn.blockSignals(True)
                btn.setChecked(on)
                btn.blockSignals(False)
        self.servo_btn.setText("כבה סרבו בכל הצירים" if c.servo else "הדלק סרבו בכל הצירים")
        self.speed_lbl.setText(f"{c.speed_pct}%")

        word = c.status_word()
        self.sw_hex.setText(f"0x{word:04X}")
        bits = f"{word:016b}"
        self.sw_bin.setText(" ".join(bits[i:i + 4] for i in range(0, 16, 4)))
        for b, (on, led) in enumerate(zip(c.status_bits(), self.sw_leds)):
            led.set(("bad" if b in (3, 4, 6) else "warn" if b == 5 else "on") if on else "off")
        for on, led in zip(c.inputs(), self.in_leds):
            led.set("on" if on else "off")


def light_palette() -> QPalette:
    """Fixed light colours. Without this, Windows dark mode turns the text white while
    the readouts and chips keep their light backgrounds, and the angles disappear."""
    pal = QPalette()
    colors = {
        QPalette.Window: "#e9edf1", QPalette.WindowText: "#1c2530", QPalette.Base: "#ffffff",
        QPalette.AlternateBase: "#f2f4f7", QPalette.Text: "#1c2530", QPalette.Button: "#f7f9fb",
        QPalette.ButtonText: "#1c2530", QPalette.Highlight: "#2f6fb3", QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: "#ffffff", QPalette.ToolTipText: "#1c2530", QPalette.PlaceholderText: "#7a8794",
        QPalette.BrightText: "#ffffff", QPalette.Link: "#2f6fb3",
    }
    for role, color in colors.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#9aa5b1"))
    return pal


def main() -> None:
    app = QApplication(sys.argv)
    if hasattr(app.styleHints(), "setColorScheme"):  # Qt 6.8+
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setLayoutDirection(Qt.RightToLeft)
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    sys.exit(app.exec())
