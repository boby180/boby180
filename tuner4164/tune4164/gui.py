"""PySide6 screen of the 4164 tuning assistant."""

from __future__ import annotations

import html
import sys
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
    QWidget,
)

from .advise import TESTS, Advice, advise
from .analysis import Analysis, command_metrics, move_metrics, step_metrics, tacho_metrics
from .prm import AXES, TUNING_KEYS, PrmFile, load_prm, prm_text
from .recording import SAMPLE_TIME_4M, Recording, load_recording
from .version import __version__

NONE = "—"
LRM = "‎"
ROLE_LABELS = [("time", "זמן"), ("reference", "רצוי (Desired)"), ("actual", "בפועל (Position/Velocity)"),
               ("error", "שגיאת מיקום (PE)"), ("command", "פקודת דרייבר"),
               ("velocity", "מהירות מהאנקודר"), ("tacho", "חיישן מהירות / כניסה אנלוגית")]
SAFETY = ("ההמלצות מבוססות על ההקלטה בלבד, ולא מחליפות שיקול דעת. משנים פרמטר אחד בכל פעם, "
          "בודקים במהירות נמוכה עם יד על לחצן החירום, ושומרים בבקר (SV) רק כשמרוצים.")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"כיול 4164  ·  גרסה {__version__}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.prefs = QSettings("Tuner4164", "Tuner4164")
        self.prm = PrmFile()
        self.rec: Recording | None = None
        self.advice: Advice | None = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        top = QHBoxLayout()
        title = QLabel(f"<b style='font-size:16pt'>כיול 4164</b>  <span style='color:#5d6b7a'>גרסה {__version__}</span>")
        top.addWidget(title)
        top.addStretch(1)
        b_prm = QPushButton("פתח קובץ PRM...")
        b_prm.clicked.connect(self.open_prm)
        b_rec = QPushButton("פתח הקלטה...")
        b_rec.clicked.connect(self.open_recording)
        self.files = QLabel("לא נטענו קבצים")
        self.files.setStyleSheet("color:#5d6b7a;")
        for w in (self.files, b_prm, b_rec):
            top.addWidget(w)
        outer.addLayout(top)

        safety = QLabel(SAFETY)
        safety.setWordWrap(True)
        safety.setStyleSheet("background:#fff4e0; border:1px solid #e08a00; border-radius:6px; padding:6px; color:#5a3b00;")
        outer.addWidget(safety)

        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split, 1)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setSizes([520, 880])

    # ---- building ----
    def _left(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("פרמטרי כיול (מה-PRM, אפשר לערוך)")
        bl = QVBoxLayout(box)
        self.table = QTableWidget(len(AXES), len(TUNING_KEYS) + 1)
        self.table.setHorizontalHeaderLabels(TUNING_KEYS + ["מבנה"])
        self.table.setVerticalHeaderLabels(list(AXES))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setLayoutDirection(Qt.LeftToRight)
        self.table.setFixedHeight(self.table.verticalHeader().defaultSectionSize() * 5 + 8)
        bl.addWidget(self.table)
        self.prm_note = QLabel("טען קובץ PRM שנשמר ב-SC-Shell (Upload Controller Parameters), או הקלד ערכים.")
        self.prm_note.setWordWrap(True)
        self.prm_note.setStyleSheet("color:#5d6b7a;")
        bl.addWidget(self.prm_note)
        lay.addWidget(box)

        setup = QGroupBox("מה נבדק בהקלטה")
        form = QFormLayout(setup)
        self.axis = QComboBox()
        self.axis.addItems(list(AXES))
        self.axis.currentTextChanged.connect(self._axis_changed)
        self.mode = QComboBox()
        self.mode.addItems(["PIV", "PID"])
        self.test = QComboBox()
        for key, label in TESTS.items():
            self.test.addItem(label, key)
        self.test.setCurrentIndex(1)
        form.addRow("ציר:", self.axis)
        form.addRow("מבנה בקרה:", self.mode)
        form.addRow("סוג הבדיקה:", self.test)
        self.cols: dict[str, QComboBox] = {}
        for role, label in ROLE_LABELS:
            c = QComboBox()
            c.addItem(NONE)
            self.cols[role] = c
            form.addRow(f"עמודת {label}:", c)
        self.derive = QCheckBox("העמודות הן מיקום. גזור מהן מהירות (לצעד מהירות)")
        form.addRow(self.derive)
        self.rg = QSpinBox()
        self.rg.setRange(1, 10000)
        self.rg.setToolTip("RG של ההקלטה. משמש רק כשאין עמודת זמן: מרווח = RG × 61.035µs")
        form.addRow("RG (אם אין עמודת זמן):", self.rg)
        go = QPushButton("נתח והמלץ")
        go.setStyleSheet("QPushButton { background:#1f6fb3; color:white; font-weight:bold; padding:8px; border-radius:6px; }")
        go.clicked.connect(self.analyze)
        form.addRow(go)
        lay.addWidget(setup)
        lay.addStretch(1)
        return w

    def _right(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        pg.setConfigOptions(antialias=True, background="w", foreground="#1c2530")
        self.plot = pg.PlotWidget(title="תגובה")
        self.plot.addLegend()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "זמן [s]")
        self.plot2 = pg.PlotWidget(title="פקודת דרייבר / שגיאה")
        self.plot2.showGrid(x=True, y=True, alpha=0.25)
        self.plot2.setXLink(self.plot)
        for p in (self.plot, self.plot2):
            p.setLayoutDirection(Qt.LeftToRight)
        lay.addWidget(self.plot, 3)
        lay.addWidget(self.plot2, 2)
        res = QGroupBox("ממצאים והמלצה")
        rl = QVBoxLayout(res)
        self.result = QTextBrowser()
        self.result.setMinimumHeight(200)
        rl.addWidget(self.result)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("העתק פקודה לקונסולה")
        self.copy_btn.clicked.connect(self.copy_command)
        self.save_btn = QPushButton("שמור PRM עם השינוי...")
        self.save_btn.clicked.connect(self.save_prm)
        for b in (self.copy_btn, self.save_btn):
            b.setEnabled(False)
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)
        lay.addWidget(res, 2)
        self.result.setHtml("<p>1. פתח קובץ PRM.  2. פתח הקלטה (CSV/TXT).  3. בחר ציר, סוג בדיקה ועמודות.  "
                            "4. לחץ <b>נתח והמלץ</b>.</p>")
        return w

    # ---- files ----
    def _dir(self) -> str:
        return self.prefs.value("last_dir", str(Path.home()))

    def open_prm(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "פתיחת קובץ PRM", self._dir(), "קובצי פרמטרים (*.prm);;כל הקבצים (*)")
        if name:
            self.load_prm_file(name)

    def load_prm_file(self, name: str) -> None:
        try:
            self.prm = load_prm(name)
        except OSError as e:
            self.prm_note.setText(f"לא הצלחתי לקרוא: {e}")
            return
        self.prefs.setValue("last_dir", str(Path(name).parent))
        for r, axis in enumerate(AXES):
            for c, key in enumerate(TUNING_KEYS):
                v = self.prm.get(axis, key)
                self.table.setItem(r, c, QTableWidgetItem("" if v is None else str(v)))
            mode = self.prm.mode(axis)
            self.table.setItem(r, len(TUNING_KEYS), QTableWidgetItem(mode or "?"))
        found = self.prm.axes()
        self.prm_note.setText(f"{Path(name).name}: {len(self.prm.values)} ערכים, צירים {', '.join(found) or '—'}"
                              + (f". {len(self.prm.skipped)} שורות לא זוהו" if self.prm.skipped else ""))
        self._axis_changed(self.axis.currentText())
        self._show_files()

    def open_recording(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "פתיחת הקלטה", self._dir(),
                                              "הקלטות טקסט (*.csv *.txt *.tsv *.dat);;כל הקבצים (*)")
        if name:
            self.load_recording_file(name)

    def load_recording_file(self, name: str) -> None:
        try:
            self.rec = load_recording(name, self.rg.value() * SAMPLE_TIME_4M)
        except (OSError, ValueError) as e:
            self.rec = None
            self.result.setHtml(f"<p style='color:#c62f2f'><b>לא הצלחתי לקרוא את ההקלטה:</b> {html.escape(str(e))}</p>")
            return
        self.prefs.setValue("last_dir", str(Path(name).parent))
        for role, combo in self.cols.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(NONE)
            combo.addItems(self.rec.names)
            idx = self.rec.time_column if role == "time" else self.rec.guess(role)
            combo.setCurrentIndex(0 if idx is None else idx + 1)
            combo.blockSignals(False)
        roles = {r: self.rec.guess(r) for r in ("reference", "error", "velocity", "tacho")}
        if roles["tacho"] is not None and roles["reference"] is None and roles["error"] is None:
            self.test.setCurrentIndex(self.test.findData("tacho"))  # a sensor-vs-encoder recording
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in self.rec.notes)
        self.result.setHtml(f"<p>נטענו {len(self.rec.time)} דגימות, {len(self.rec.names)} עמודות, "
                            f"{self.rec.time[-1]:.3f} שניות.</p><ul>{notes}</ul>"
                            "<p>בדוק שהעמודות שנבחרו נכונות ולחץ <b>נתח והמלץ</b>.</p>")
        self._show_files()
        self._plot_raw()

    def _show_files(self) -> None:
        parts = []
        if self.prm.path:
            parts.append(f"PRM: {self.prm.path.name}")
        if self.rec and self.rec.path:
            parts.append(f"הקלטה: {self.rec.path.name}")
        self.files.setText("   ·   ".join(parts) or "לא נטענו קבצים")

    def _axis_changed(self, axis: str) -> None:
        mode = self.prm.mode(axis)
        if mode:
            self.mode.setCurrentText(mode)

    # ---- analysis ----
    def _col(self, role: str) -> np.ndarray | None:
        i = self.cols[role].currentIndex() - 1
        return None if i < 0 or self.rec is None else self.rec.data[:, i].astype(float)

    def _gains(self) -> dict[str, int | None]:
        r = AXES.index(self.axis.currentText())
        out = {}
        for c, key in enumerate(TUNING_KEYS):
            item = self.table.item(r, c)
            try:
                out[key] = int(item.text()) if item and item.text().strip() else None
            except ValueError:
                out[key] = None
        return out

    def _time(self) -> np.ndarray:
        t = self._col("time")
        if t is None:
            return np.arange(len(self.rec.time)) * self.rg.value() * SAMPLE_TIME_4M
        t = t - t[0]
        return t / 1000.0 if t[-1] > 1000 else t

    def analyze(self) -> None:
        if self.rec is None:
            self.result.setHtml("<p style='color:#c62f2f'>טען קודם הקלטה.</p>")
            return
        t = self._time()
        ref, act, pe, cmd = (self._col(r) for r in ("reference", "actual", "error", "command"))
        test = self.test.currentData()
        an = Analysis()
        gains = self._gains()
        vel, tacho = self._col("velocity"), self._col("tacho")
        if test == "tacho":
            if vel is None and act is not None:
                vel = np.gradient(act, t) / 1000.0
                an.warnings.append("אין עמודת מהירות. המהירות חושבה מהמיקום (ביחידות לכל ms).")
            if vel is not None and tacho is not None:
                an.tacho = tacho_metrics(vel, tacho)
        elif test == "velocity_step":
            if self.derive.isChecked():
                dt = np.gradient(t)
                dt[dt == 0] = 1e-6
                ref = None if ref is None else np.gradient(ref) / dt
                act = None if act is None else np.convolve(np.gradient(act) / dt, np.ones(5) / 5, mode="same")
            if act is not None:
                an.step = step_metrics(t, act, ref)
        elif test == "position_step":
            if act is not None:
                an.step = step_metrics(t, act, ref)
        else:
            if pe is None and ref is not None and act is not None:
                pe = ref - act
                an.warnings.append("אין עמודת שגיאת מיקום. השגיאה חושבה כרצוי פחות בפועל.")
            if pe is not None:
                moving = None
                if ref is not None:
                    moving = np.abs(np.gradient(ref)) > 1e-9
                an.move = move_metrics(t, pe, moving)
        if cmd is not None:
            an.command = command_metrics(cmd, gains.get("TL") or None)
        self.advice = advise(self.mode.currentText(), test, gains, an)
        self._plot(t, ref, act, pe, cmd, test, vel, tacho)
        self._show_advice()

    def _plot_raw(self) -> None:
        self.analyze() if self.rec is not None and self._col("actual") is not None else None

    def _plot(self, t, ref, act, pe, cmd, test, vel=None, tacho=None) -> None:
        self.plot.clear()
        self.plot2.clear()
        self.plot.addLegend()
        if test == "tacho" and vel is not None and tacho is not None:
            self.plot.plot(t, vel, pen=pg.mkPen("#1f6fb3", width=1.5), name="מהירות מהאנקודר")
            self.plot.plot(t, tacho, pen=pg.mkPen("#e08a00", width=1), name="חיישן מהירות")
            k, c = np.polyfit(vel, tacho, 1)
            self.plot2.plot(t, tacho - (k * vel + c), pen=pg.mkPen("#c62f2f", width=1))
            self.plot2.setTitle("ההפרש בין החיישן לאנקודר (אחרי התאמה)")
            return
        if test == "move" and pe is not None:
            self.plot.plot(t, pe, pen=pg.mkPen("#c62f2f", width=1.5), name="שגיאת מיקום")
        else:
            if ref is not None:
                self.plot.plot(t, ref, pen=pg.mkPen("#1f6fb3", width=1.5), name="רצוי")
            if act is not None:
                self.plot.plot(t, act, pen=pg.mkPen("#e08a00", width=1.5), name="בפועל")
        if cmd is not None:
            self.plot2.plot(t, cmd, pen=pg.mkPen("#5d6b7a", width=1))
            self.plot2.setTitle("פקודת דרייבר")
        elif pe is not None:
            self.plot2.plot(t, pe, pen=pg.mkPen("#c62f2f", width=1))
            self.plot2.setTitle("שגיאת מיקום")

    def _show_advice(self) -> None:
        a = self.advice
        axis = self.axis.currentText()
        parts = []
        for w in a.warnings:
            parts.append(f"<p style='color:#b86e00'>⚠ {html.escape(w)}</p>")
        parts.append("<ul>" + "".join(f"<li>{html.escape(f)}</li>" for f in a.findings) + "</ul>")
        if a.status == "stop":
            parts.append("<p style='color:#c62f2f'><b>אין המלצה לשינוי מההקלטה הזו.</b></p>")
        elif a.primary:
            p = a.primary
            new = "?" if p.new is None else str(p.new)
            old = "?" if p.old is None else str(p.old)
            parts.append(
                f"<div style='background:#e8f4ec; border:1px solid #1f9d55; padding:8px;'>"
                f"<p style='font-size:13pt'><b>השינוי המומלץ עכשיו: {axis}{p.param} מ-{old} ל-{new} "
                f"({'+' if p.percent > 0 else ''}{p.percent}%)</b></p>"
                f"<p>{html.escape(p.reason)}</p>"
                f"<p>בקונסולה של תוכנת הבקר: <code>{LRM}{axis}{p.param}={new}</code>, ואז להקליט שוב באותה בדיקה.</p></div>")
            if len(a.changes) > 1:
                parts.append("<p><b>אחר כך, אם עדיין צריך:</b></p><ul>" + "".join(
                    f"<li>{c.param}: {'+' if c.percent > 0 else ''}{c.percent}%: {html.escape(c.reason)}</li>"
                    for c in a.changes[1:]) + "</ul>")
        else:
            parts.append("<p style='color:#1f9d55'><b>לא נדרש שינוי.</b></p>")
        self.result.setHtml("".join(parts))
        ok = a.primary is not None and a.primary.new is not None and a.status != "stop"
        self.copy_btn.setEnabled(ok)
        self.save_btn.setEnabled(ok)

    def _command(self) -> str:
        p = self.advice.primary
        return f"{self.axis.currentText()}{p.param}={p.new}"

    def copy_command(self) -> None:
        QApplication.clipboard().setText(self._command())
        self.statusBar().showMessage(f"הועתק: {self._command()}", 4000)

    def save_prm(self) -> None:
        p, axis = self.advice.primary, self.axis.currentText()
        default = str(Path(self._dir()) / f"tune_{axis}{p.param}.prm")
        name, _ = QFileDialog.getSaveFileName(self, "שמירת PRM עם השינוי", default, "קובצי פרמטרים (*.prm)")
        if not name:
            return
        path = Path(name) if name.lower().endswith(".prm") else Path(name + ".prm")
        note = f"{axis}{p.param}: {p.old} -> {p.new} ({p.percent:+d}%)"
        path.write_bytes(prm_text({(axis, p.param): p.new}, note).encode("ascii"))
        self.statusBar().showMessage(f"נשמר: {path}", 6000)


def light_palette() -> QPalette:
    """Fixed light colours, so Windows dark mode does not turn the text white on light panels."""
    pal = QPalette()
    for role, color in {
        QPalette.Window: "#e9edf1", QPalette.WindowText: "#1c2530", QPalette.Base: "#ffffff",
        QPalette.AlternateBase: "#f2f4f7", QPalette.Text: "#1c2530", QPalette.Button: "#f7f9fb",
        QPalette.ButtonText: "#1c2530", QPalette.Highlight: "#2f6fb3", QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: "#ffffff", QPalette.ToolTipText: "#1c2530", QPalette.PlaceholderText: "#7a8794",
    }.items():
        pal.setColor(role, QColor(color))
    return pal


def main() -> None:
    app = QApplication(sys.argv)
    if hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setLayoutDirection(Qt.RightToLeft)
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    sys.exit(app.exec())
