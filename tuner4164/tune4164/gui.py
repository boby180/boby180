"""PySide6 screen of the 4164 tuning assistant."""

from __future__ import annotations

import hashlib
import html
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, QStandardPaths, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QDialog, QDialogButtonBox, QFrame, QInputDialog, QScrollArea, QMainWindow, QMessageBox, QPushButton, QTabWidget, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
    QWidget,
)

from .advise import SET_NAMES, TESTS, Advice, advise
from .loopguard import best_run, guard
from .history import (History, advice_fields, compare, extract_run, fingerprint, load_run, make_entry, other_unit,
                      run_document, step_back)
from .analysis import Analysis, command_metrics, move_metrics, moving_from_velocity, step_metrics, tacho_metrics
from .prm import AXES, TUNING_KEYS, PrmFile, load_prm, parse_prm, prm_text
from .recording import SAMPLE_TIME_4M, Recording, load_recording
from .version import __version__

NONE = "—"
LRM = "‎"
SETS_NOTE = ("KP/KI/KD בבקר הם שלוש קבוצות: [1] בזמן תנועה, [2] אחרי סוף התנועה עד הכניסה למטרה, "
             "[3] בעמידה (אחרי TR/TT). ההמלצה משנה רק את הקבוצות של השלב שבו נמצאה הבעיה.")
ROLE_LABELS = [("time", "זמן"), ("reference", "רצוי (Desired)"), ("actual", "בפועל (Position/Velocity)"),
               ("error", "שגיאת מיקום (PE)"), ("command", "פקודת דרייבר"),
               ("velocity", "מהירות מהאנקודר"), ("velocity_ref", "מהירות רצויה"),
               ("tacho", "חיישן מהירות / כניסה אנלוגית")]
GUIDE_SHORT = (
    "<b>איזו הקלטה לעשות ב-SC-Shell כדי לקבל המלצות ל-KP / KI / KD:</b> "
    "בתיקיית Data Recording בוחרים לאותו ציר: "
    "<b>RV[1] = Position Error</b>, <b>RV[2] = Desired Position</b>, <b>RV[3] = Position</b>, "
    "<b>RV[4] = Driver Command</b>, <b>RV[5] = Analog Input</b> (חיישן המהירות, נבדק ראשון). "
    "מקליטים <b>תנועה רגילה</b> ו<b>תנועה קטנה (0.5°-1°)</b>, וממשיכים להקליט <b>כשנייה אחרי שהציר עוצר</b>. "
    "שומרים כקובץ SDV ופותחים כאן."
)
GUIDE_FULL = """
<h3>איזו הקלטה לעשות</h3>
<p>כדי לשפוט את הכיול צריך לראות <b>מה הבקר ביקש</b> מול <b>מה הציר עשה</b>. הקלטה של מהירות מהאנקודר וחיישן
מהירות בלבד (למשל XVel ו-XAnlgInp) בודקת רק את חיישן המהירות, לא את KP / KI / KD.</p>
<h4>1. ב-SC-Shell, תיקיית Data Recording, לאותו ציר:</h4>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>וקטור</th><th>לבחור</th><th>למה</th></tr>
<tr><td>RV[1]</td><td><b>Position Error</b></td><td>הכי חשוב: כמה הציר מפגר ואיך הוא מתייצב</td></tr>
<tr><td>RV[2]</td><td><b>Desired Position</b></td><td>מה הבקר ביקש, מתי התנועה מתחילה ונגמרת</td></tr>
<tr><td>RV[3]</td><td><b>Position</b></td><td>מה הציר עשה בפועל</td></tr>
<tr><td>RV[4]</td><td><b>Driver Command</b></td><td>הפקודה למנוע: רוויה ורעש (למשל KD גבוה מדי)</td></tr>
<tr><td>RV[5]</td><td><b>Analog Input</b></td><td>חיישן המהירות (Tacho). נבדק ראשון: לולאת המהירות עובדת לפיו</td></tr>
</table>
<h4>2. אילו תנועות להקליט</h4>
<ul>
<li><b>תנועה רגילה לנקודה</b> במהירות ובתאוצה הרגילות של המכונה. בתוכנה: "תנועה רגילה לנקודה (PTP)".</li>
<li><b>תנועה קטנה</b> של 0.5° עד 1°. כאן רואים הכי טוב חריגה ותנודות. בתוכנה: "צעד מיקום קטן".</li>
<li>להמשיך להקליט <b>כחצי שנייה עד שנייה אחרי שהציר עוצר</b>, כדי לראות אם הוא רועד או מתייצב.</li>
</ul>
<h4>3. אורך ההקלטה (RL ו-RG)</h4>
<p>משך ההקלטה = RL × RG × 61µs. בוחרים RG כך שההקלטה תכסה את התנועה ועוד שנייה, ולא הרבה יותר.
לדוגמה: RL=8000 ו-RG=4 נותנים 2 שניות, נקודה כל 0.24ms.</p>
<h4>4. בדיקה מתקדמת (לפי מדריך היצרן, רק למי שמכיר)</h4>
<p><b>צעד מהירות עם KP=0</b> לכיול KD ו-KI של לולאת המהירות. בזמן הבדיקה היצרן מגדיל את ER, כלומר ההגנה
מפני שגיאה גדולה כבויה. מקליטים מהירות רצויה ובפועל ופקודת דרייבר.</p>
<h4>5. מה לשלוח</h4>
<p>את קובץ ה-SDV ואת קובץ ה-PRM של המכונה (Upload Controller Parameters).</p>
"""
SAFETY = ("ההמלצות מבוססות על ההקלטה בלבד, ולא מחליפות שיקול דעת. משנים פרמטר אחד בכל פעם, "
          "בודקים במהירות נמוכה עם יד על לחצן החירום, ושומרים בבקר (SV) רק כשמרוצים.")


HIST_COLS = ["זמן", "הקלטה", "מה השתנה מהקודמת", "התייצבות", "זמן תקוע", "שיא אחרי עצירה", "פיגור", "תוצאה",
             "ההמלצה שניתנה"]


def _fmt_metric(v, unit: str) -> str:
    if v is None:
        return "—"
    if v == "inf" or v == float("inf"):
        return "לא התייצב" if unit == "s" else "∞"
    if unit == "s":
        return f"{v * 1000:.0f}ms"
    if unit == "%":
        return f"{v:.2f}%"
    return f"{v:.0f}" if abs(v) >= 10 or float(v).is_integer() else f"{v:.2f}"


def json_key(e: dict) -> str:
    return f"{e.get('unit')}|{e.get('axis')}|{e.get('rec_key')}"


def default_history_path() -> Path:
    """One fixed file for every version (on Windows: AppData\\Local\\Tuner4164\\history.json).
    Version 0.4.0 kept it in a folder named after the EXE (with the version in it): bring that over."""
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    path = Path(base or Path.home()) / "Tuner4164" / "history.json"
    if not path.exists():
        old = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if old:
            found = sorted(Path(old).parent.glob("Tuner4164*/history.json"), key=lambda f: f.stat().st_mtime)
            if found:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(found[-1].read_bytes())
                except OSError:
                    pass
    return path


class MainWindow(QMainWindow):
    def __init__(self, history_path: str | Path | None = None):
        super().__init__()
        self.setWindowTitle(f"כיול 4164  ·  גרסה {__version__}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.prefs = QSettings("Tuner4164", "Tuner4164")
        self.prm = PrmFile()
        self.rec: Recording | None = None
        self.advice: Advice | None = None
        self.comparison = None
        if history_path is None:
            history_path = default_history_path()
        self.history = History(history_path)

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

        guide = QHBoxLayout()
        self.guide = QLabel(GUIDE_SHORT)
        self.guide.setWordWrap(True)
        self.guide.setStyleSheet("background:#e6f0fa; border:2px solid #1f6fb3; border-radius:6px; padding:8px;"
                                 " color:#0f2f52; font-size:11pt;")
        guide_btn = QPushButton("הסבר מלא:\nאיזו הקלטה לעשות")
        guide_btn.setStyleSheet("QPushButton { background:#1f6fb3; color:white; font-weight:bold; padding:8px 14px;"
                                " border-radius:6px; }")
        guide_btn.clicked.connect(self.show_guide)
        guide.addWidget(self.guide, 1)
        guide.addWidget(guide_btn)
        outer.addLayout(guide)

        split = QSplitter(Qt.Horizontal)
        outer.addWidget(split, 1)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setSizes([520, 880])
        self._fill_history()
        self._axis_changed(self.axis.currentText())   # the per-rotator/axis offset checkbox

    # ---- building ----
    def _left(self) -> QWidget:
        outer = QWidget()
        olay = QVBoxLayout(outer)
        olay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        w = QWidget()
        lay = QVBoxLayout(w)
        scroll.setWidget(w)
        olay.addWidget(scroll, 1)
        box = QGroupBox("פרמטרי כיול (מה-PRM, אפשר לערוך)")
        bl = QVBoxLayout(box)
        self.table = QTableWidget(len(AXES), len(TUNING_KEYS) + 1)
        self.table.setHorizontalHeaderLabels(TUNING_KEYS + ["מבנה"])
        for c, key in enumerate(TUNING_KEYS):
            m = re.search(r"\[(\d)\]$", key)
            if key[:2] in ("KP", "KI", "KD") and m:
                self.table.horizontalHeaderItem(c).setToolTip(SET_NAMES[int(m.group(1))])
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
        self.test.setCurrentIndex(0)  # full test
        self.unit = QComboBox()
        self.unit.addItems(self.history.units())
        last = self.prefs.value("unit", "")
        if last in self.history.units():
            self.unit.setCurrentText(last)
        self.unit.currentTextChanged.connect(self._unit_changed)
        new_unit = QPushButton("מסובב חדש...")
        new_unit.clicked.connect(self.new_unit)
        urow = QHBoxLayout()
        urow.addWidget(self.unit, 1)
        urow.addWidget(new_unit)
        form.addRow("מסובב / בקר:", urow)
        form.addRow("ציר:", self.axis)
        form.addRow("מבנה בקרה:", self.mode)
        form.addRow("סוג הבדיקה:", self.test)
        self.offset_ok = QCheckBox("אופסט הטאכו אופס ידנית (AI=0 ב-SC-Shell): לא לבדוק אותו מההקלטה")
        self.offset_ok.setToolTip("נשמר לכל מסובב וציר. מסמנים כשבדקתם ב-SC-Shell ש-AI מראה 0 כשהציר עומד.")
        self.offset_ok.toggled.connect(self._offset_ok_toggled)
        form.addRow(self.offset_ok)
        lay.addWidget(setup)

        cols_box = QGroupBox("עמודות בהקלטה")
        cl = QVBoxLayout(cols_box)
        self.cols_toggle = QPushButton()
        self.cols_toggle.setFlat(True)
        self.cols_toggle.setStyleSheet("text-align:right; color:#1f6fb3; font-weight:bold;")
        self.cols_toggle.clicked.connect(lambda: self._show_cols(not self.cols_panel.isVisible()))
        self.cols_summary = QLabel("עדיין לא נטענה הקלטה.")
        self.cols_summary.setWordWrap(True)
        self.cols_summary.setStyleSheet("color:#5d6b7a;")
        cl.addWidget(self.cols_toggle)
        cl.addWidget(self.cols_summary)
        self.cols_panel = QWidget()
        cform = QFormLayout(self.cols_panel)
        cform.setContentsMargins(0, 0, 0, 0)
        self.cols: dict[str, QComboBox] = {}
        for role, label in ROLE_LABELS:
            c = QComboBox()
            c.addItem(NONE)
            c.currentIndexChanged.connect(self._update_cols_summary)
            self.cols[role] = c
            cform.addRow(f"{label}:", c)
        self.derive = QCheckBox("העמודות הן מיקום. גזור מהן מהירות (לצעד מהירות)")
        cform.addRow(self.derive)
        self.rg = QSpinBox()
        self.rg.setRange(1, 10000)
        self.rg.setToolTip("RG של ההקלטה. משמש רק כשאין עמודת זמן: מרווח = RG × 61.035µs")
        cform.addRow("RG (אם אין עמודת זמן):", self.rg)
        cl.addWidget(self.cols_panel)
        lay.addWidget(cols_box)
        lay.addStretch(1)
        self._show_cols(self.prefs.value("show_cols", "0") == "1")

        go = QPushButton("נתח והמלץ")
        go.setMinimumHeight(46)
        go.setStyleSheet("QPushButton { background:#1f6fb3; color:white; font-weight:bold; font-size:13pt;"
                         " padding:8px; border-radius:6px; } QPushButton:hover { background:#2a7fcc; }")
        go.clicked.connect(lambda: self.analyze())
        self.go_btn = go
        olay.addWidget(go)   # outside the scroll area: always visible
        return outer

    def _show_cols(self, show: bool) -> None:
        self.cols_panel.setVisible(show)
        self.cols_summary.setVisible(not show)
        self.cols_toggle.setText(("▲ הסתר את בחירת העמודות" if show else "▼ הצג ושנה את בחירת העמודות"))
        self.prefs.setValue("show_cols", "1" if show else "0")

    def _update_cols_summary(self) -> None:
        if not hasattr(self, "cols_summary"):
            return
        chosen = [f"{label.split(' (')[0]}: {c.currentText()}" for (role, label), c in
                  zip(ROLE_LABELS, self.cols.values()) if c.currentIndex() > 0]
        self.cols_summary.setText("נבחרו אוטומטית: " + " · ".join(chosen) if chosen else "לא נבחרו עמודות.")

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
        analysis = QWidget()
        al = QVBoxLayout(analysis)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.plot, 3)
        al.addWidget(self.plot2, 2)
        self.all_plots = pg.GraphicsLayoutWidget()
        self.all_plots.setLayoutDirection(Qt.LeftToRight)
        self.tabs = QTabWidget()
        self.tabs.addTab(analysis, "ניתוח")
        self.tabs.addTab(self.all_plots, "כל הערוצים בהקלטה")
        hist = QWidget()
        hl = QVBoxLayout(hist)
        self.hist_label = QLabel()
        self.hist_label.setWordWrap(True)
        self.hist_table = QTableWidget(0, len(HIST_COLS))
        self.hist_table.setHorizontalHeaderLabels(HIST_COLS)
        self.hist_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.hist_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.hist_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.hist_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.hist_table.itemSelectionChanged.connect(self._hist_selected)
        self._hist_rows: list[dict] = []
        new_cal = QPushButton("מחק את הזיכרון של הציר הזה במסובב הזה")
        new_cal.clicked.connect(self.new_calibration)
        hl.addWidget(self.hist_label)
        hl.addWidget(self.hist_table, 1)
        hrow = QHBoxLayout()
        run_row = QHBoxLayout()
        self.run_btns = []
        for text, slot in (("שחזר ריצה", self.restore_selected),
                           ("שמור את ה-PRM שלה...", self.save_selected_prm),
                           ("השווה PRM לנוכחי", self.compare_selected_prm),
                           ("ייצא JSON...", self.export_selected)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setEnabled(False)
            run_row.addWidget(b)
            self.run_btns.append(b)
        self.pair_btn = QPushButton("השווה 2 ריצות מסומנות")
        self.pair_btn.setToolTip("מסמנים שתי שורות (Ctrl + לחיצה) ולוחצים: מדדים, שינויים ב-PRM וגרפים זה על זה")
        self.pair_btn.setEnabled(False)
        self.pair_btn.clicked.connect(self.compare_selected_pair)
        run_row.addWidget(self.pair_btn)
        imp = QPushButton("ייבא JSON...")
        imp.clicked.connect(self.import_run)
        run_row.addWidget(imp)
        run_row.addStretch(1)
        hl.addLayout(run_row)
        hrow.addWidget(new_cal)
        hrow.addStretch(1)
        hl.addLayout(hrow)
        self.tabs.addTab(hist, "היסטוריית כיול")
        lay.addWidget(self.tabs, 3)
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
        self.prev_btn = QPushButton("השוואה גרפית להקלטה הקודמת")
        self.prev_btn.clicked.connect(self.compare_with_previous)
        for b in (self.copy_btn, self.save_btn, self.prev_btn):
            b.setEnabled(False)
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)
        lay.addWidget(res, 2)
        self.result.setHtml("<p>1. פתח קובץ PRM.  2. פתח הקלטה (SDV, או CSV/TXT).  3. בחר ציר, סוג בדיקה ועמודות.  "
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
        self._show_files()
        had_advice = self.advice is not None
        axis = self.axis.currentText()
        saved = self.history.get(self.unit.currentText(), axis, self._rec_key()) if self.rec is not None else None
        foreign = saved is not None and other_unit(saved.get("fingerprint"), fingerprint(self.prm, axis))
        self._axis_changed(axis)
        if foreign:
            self.result.setHtml("<p><b>נטען PRM שנראה של בקר או מסובב אחר מההקלטה שפתוחה.</b> פתח את ההקלטה של הבקר "
                                "הזה ולחץ <b>נתח והמלץ</b>. אם זה מסובב חדש, בחר \"מסובב חדש...\" לפני כן.</p>")
        elif had_advice and self.rec is not None:
            # refresh the screen (the analysis may have run before the gains were known), but never rewrite
            # a saved run with a PRM it was not recorded with
            self.analyze(remember=not (saved and saved.get("gains")))
        self._show_files()

    def open_recording(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "פתיחת הקלטה", self._dir(),
                                              "הקלטות SC-Shell (*.sdv *.SDV);;"
                                              "כל ההקלטות (*.sdv *.SDV *.csv *.txt *.tsv *.dat);;"
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
        self._update_cols_summary()
        if self.rec.guess("error") is None and (self.rec.guess("reference") is None or self.rec.guess("actual") is None):
            self._show_cols(True)   # the program could not find the position error: let the user choose
        roles = {r: self.rec.guess(r) for r in ("reference", "error", "velocity", "tacho")}
        if roles["tacho"] is not None and roles["reference"] is None and roles["error"] is None:
            self.test.setCurrentIndex(self.test.findData("tacho"))  # a sensor-vs-encoder recording
        else:
            self.test.setCurrentIndex(self.test.findData("full"))
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in self.rec.notes)
        self.result.setHtml(f"<p>נטענו {len(self.rec.time)} דגימות, {len(self.rec.names)} עמודות, "
                            f"{self.rec.time[-1]:.3f} שניות.</p><ul>{notes}</ul>"
                            "<p>בדוק שהעמודות שנבחרו נכונות ולחץ <b>נתח והמלץ</b>.</p>")
        self._show_files()
        self._plot_all()
        self._plot_raw()

    def _show_files(self) -> None:
        parts = []
        if self.prm.path:
            parts.append(f"PRM: {self.prm.path.name}")
        if self.rec and self.rec.path:
            parts.append(f"הקלטה: {self.rec.path.name}")
        self.files.setText("   ·   ".join(parts) or "לא נטענו קבצים")

    def _offset_key(self) -> str:
        return f"offset_ok/{self.unit.currentText()}/{self.axis.currentText()}"

    def _offset_ok_toggled(self, on: bool) -> None:
        if hasattr(self, "unit"):
            self.prefs.setValue(self._offset_key(), "1" if on else "0")

    def _axis_changed(self, axis: str) -> None:
        if hasattr(self, "offset_ok") and hasattr(self, "unit"):
            self.offset_ok.blockSignals(True)
            self.offset_ok.setChecked(self.prefs.value(self._offset_key(), "0") == "1")
            self.offset_ok.blockSignals(False)
        mode = self.prm.mode(axis)
        if mode:
            self.mode.setCurrentText(mode)
        if not hasattr(self, "hist_table"):
            return  # still building the window
        self._fill_history()
        if self.advice is not None:
            self.advice = None
            self.comparison = None
            self.copy_btn.setEnabled(False)
            self.save_btn.setEnabled(False)
            self.prev_btn.setEnabled(False)
            self.result.setHtml(f"<p>נבחר {html.escape(self.unit.currentText())}, ציר {axis}. בדוק שהעמודות שייכות לציר הזה ולחץ <b>נתח והמלץ</b>.</p>")

    # ---- analysis ----
    def _col(self, role: str) -> np.ndarray | None:
        i = self.cols[role].currentIndex() - 1
        return None if i < 0 or self.rec is None else self.rec.data[:, i].astype(float)

    def _gains(self) -> dict[str, int | None]:
        r = AXES.index(self.axis.currentText())
        out = {}
        for c, key in enumerate(TUNING_KEYS):
            item = self.table.item(r, c)
            text = item.text().strip() if item else ""
            try:
                out[key] = int(round(float(text))) if text else None
            except ValueError:
                out[key] = None
            if out[key] is None:  # fall back to the loaded PRM if the cell is empty or unreadable
                out[key] = self.prm.get(self.axis.currentText(), key)
        return out

    def _time(self) -> np.ndarray:
        t = self._col("time")
        if t is None:
            return np.arange(len(self.rec.time)) * self.rg.value() * SAMPLE_TIME_4M
        t = t - t[0]
        name = self.cols["time"].currentText().lower()
        return t / 1000.0 if "ms" in name else t

    def analyze(self, remember: bool = True) -> None:
        if self.rec is None:
            self.result.setHtml("<p style='color:#c62f2f'>טען קודם הקלטה.</p>")
            return
        t = self._time()
        ref, act, pe, cmd = (self._col(r) for r in ("reference", "actual", "error", "command"))
        test = self.test.currentData()
        an = Analysis()
        gains = self._gains()
        vel, tacho, vref = self._col("velocity"), self._col("tacho"), self._col("velocity_ref")
        if test in ("full", "move"):
            if pe is None and ref is not None and act is not None:
                pe = ref - act
                an.warnings.append("אין עמודת שגיאת מיקום. השגיאה חושבה כרצוי פחות בפועל.")
            moving = None
            if vref is not None:
                moving = np.abs(vref) > 1e-6 * (np.max(np.abs(vref)) + 1)
            elif ref is not None:
                moving = np.abs(np.gradient(ref)) > 1e-9
            elif vel is not None or act is not None:
                moving = moving_from_velocity(t, vel if vel is not None else np.gradient(act, t))
                an.warnings.append("אין בהקלטה מיקום רצוי: זמני התנועה והעצירה זוהו לפי מהירות הציר בפועל. "
                                   "עדיף להוסיף להקלטה את Desired Position.")
            if pe is not None:
                an.move = move_metrics(t, pe, moving, vref=vref, pos=act, cmd=cmd)
        if test == "full" and vel is not None and tacho is not None:
            an.tacho = tacho_metrics(vel, tacho, t=t)
        if test == "tacho":
            if vel is None and act is not None:
                vel = np.gradient(act, t)
                an.warnings.append("אין עמודת מהירות. המהירות חושבה מהמיקום (ספירות לשנייה).")
            if vel is not None and tacho is not None:
                an.tacho = tacho_metrics(vel, tacho, t=t)
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
        if an.tacho is not None and self.offset_ok.isChecked():
            an.tacho.offset_manual = True
            an.tacho.still_offset = None
        if cmd is not None:
            an.command = command_metrics(cmd, gains.get("TL") or None)
        self.advice = advise(self.mode.currentText(), test, gains, an)
        if remember:
            self._remember(test, gains, an, vref, vel)
        else:
            self.comparison = None
            self.advice.warnings.insert(0, "הניתוח רוענן עם ה-PRM שנטען עכשיו, אבל לא נשמר בזיכרון: ההקלטה הזו כבר "
                                           "שמורה עם ה-PRM שהיה בזמן שלה.")
        self._plot(t, ref, act, pe, cmd, test, vel, tacho)
        self._show_advice()

    # ---- calibration memory ----
    def _rec_key(self) -> str:
        p = self.rec.path
        if p is None:
            return "?"
        try:
            st = p.stat()
            return f"{p.name}|{st.st_size}|{int(st.st_mtime)}"
        except OSError:
            return p.name

    def _remember(self, test, gains, an, vref, vel) -> None:
        axis = self.axis.currentText()
        speed = vref if vref is not None else vel
        top = float(np.nanmax(np.abs(speed))) if speed is not None and len(speed) else None
        entry = make_entry(axis, test, gains, an, self.advice, self._rec_key(),
                           self.rec.path.name if self.rec.path else "?",
                           self.prm.path.name if self.prm.path else "", top)
        unit = self.unit.currentText()
        entry["unit"] = unit
        entry["fingerprint"] = fingerprint(self.prm, axis)
        prev = self.history.previous(unit, axis, entry["rec_key"])
        # the same recording saved before (a PRM loaded after it) or the previous recording of this rotator
        ref = self.history.get(unit, axis, entry["rec_key"]) or prev
        diff = other_unit(ref.get("fingerprint"), entry["fingerprint"]) if ref else []
        if diff:
            choice = self._ask_other_unit(unit, diff)
            if choice == "new":
                if self.new_unit():
                    return self._remember(test, gains, an, vref, vel)   # saved under the new rotator
                choice = "skip"
            if choice == "skip":
                self.comparison = None
                self.advice.warnings.insert(0, f"ההקלטה לא נשמרה בזיכרון של \"{unit}\" ולא הושוותה, כי ה-PRM נראה "
                                               f"של בקר אחר (שונים: {', '.join(diff)}).")
                return
        self.comparison = compare(prev, entry) if prev else None
        if self.comparison:
            if diff:
                self.comparison.notes.insert(0, f"נשמר ל-\"{unit}\" למרות שה-PRM שונה מההקלטה הקודמת "
                                                f"({', '.join(diff)}).")
            sensor_fix = any(k in ("AS", "AG", "AF") for c in self.advice.changes for k, _, _ in c.items())
            back = step_back(self.comparison, gains) if not diff and not sensor_fix else None
            if back is not None:
                undone = {k for k, _, _ in back.items()}
                self.advice.changes = [back] + [c for c in self.advice.changes
                                                if not undone & {k for k, _, _ in c.items()}]
                self.advice.status = "change"
            entry["verdict"] = self.comparison.verdict
        # loop guard: look at the whole sequence of this rotator and axis, not only the previous run
        seq = self.history.for_axis(unit, axis)
        keys = [e.get("rec_key") for e in seq]
        seq = seq[:keys.index(entry["rec_key"])] if entry["rec_key"] in keys else seq
        # only runs made with the same speed-sensor scale on the same controller can be compared
        seq = [r for r in seq if all(r.get("gains", {}).get(k) == entry["gains"].get(k) for k in ("AG", "AF"))
               and not other_unit(r.get("fingerprint"), entry["fingerprint"])]
        if seq:
            for note in reversed(guard(self.advice, entry, seq)):
                self.advice.warnings.insert(0, note)
        entry.update(advice_fields(self.advice))
        self._archive(entry)
        self.history.put(entry)
        self._current_entry = entry
        self._fill_history()

    def _fill_history(self) -> None:
        axis, unit = self.axis.currentText(), self.unit.currentText()
        rows = self.history.for_axis(unit, axis)
        self.hist_label.setText(f"<b>{html.escape(unit)}, ציר {axis}</b>: {len(rows)} הקלטות בזיכרון. כל ניתוח נשמר כאן אוטומטית, וההקלטה הבאה "
                                f"של אותו מסובב ואותו ציר מושווית לקודמת. לכל מסובב ולכל ציר יש רצף נפרד, ואף אחד לא דורס אחר. "
                                f"כשעוברים לבקר או מסובב אחר, בוחרים אותו (או \"מסובב חדש...\") בצד ימין.<br>"
                                f"<span style='color:#5d6b7a'>קובץ הזיכרון: {html.escape(str(self.history.path))}</span>")
        self.hist_table.setRowCount(len(rows))
        prev = None
        self._hist_rows = rows
        best = best_run(rows)
        for r, e in enumerate(rows):
            m = e.get("metrics", {})
            changed = ""
            if prev:
                pg = prev.get("gains", {})
                changed = ", ".join(f"{k}: {pg[k]}→{v}" for k, v in e.get("gains", {}).items() if k in pg and pg[k] != v)
            adv = e.get("advice") or []
            cells = [("★ " if e is best else "") + e.get("time", ""), e.get("recording", ""), changed or ("—" if prev else "התחלה"),
                     _fmt_metric(m.get("settle"), "s"), _fmt_metric(m.get("stuck_time"), "s"),
                     _fmt_metric(m.get("end_peak"), ""), _fmt_metric(m.get("following"), ""),
                     {"better": "השתפר ✔", "worse": "החמיר ✘", "mixed": "מעורב", "same": "בלי שינוי"}.get(e.get("verdict"), ""),
                     ", ".join(f"{a['param']}={a['new']}" for a in adv) or "—"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0 and e is best:
                    item.setToolTip("הריצה הטובה ביותר עד עכשיו בציר הזה")
                    item.setForeground(QColor("#1f9d55"))
                if c == 8 and e.get("advice_all"):
                    item.setToolTip("\n".join(
                        f"{i + 1}. " + ", ".join(f"{x['param']} {x['old']}→{x['new']}" for x in a["items"])
                        + f" ({a['pct']:+d}%)" for i, a in enumerate(e["advice_all"])))
                if c == 7 and e.get("verdict") in ("better", "worse"):
                    item.setForeground(QColor("#1f9d55" if e["verdict"] == "better" else "#c62f2f"))
                self.hist_table.setItem(r, c, item)
            prev = e
        self.hist_table.scrollToBottom()

    def _ask_other_unit(self, unit: str, diff: list[str]) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("בקר או מסובב אחר?")
        box.setText(f"ה-PRM שטעון נראה של בקר או מסובב אחר מההקלטה הקודמת של \"{unit}\".\n"
                    f"ערכים שונים: {', '.join(diff)}.\n\nכדי לא לערבב את ההיסטוריה של \"{unit}\":")
        new = box.addButton("מסובב חדש...", QMessageBox.AcceptRole)
        skip = box.addButton("לא לשמור בזיכרון", QMessageBox.RejectRole)
        keep = box.addButton(f"זה אותו מסובב, לשמור ל-\"{unit}\"", QMessageBox.DestructiveRole)
        box.setDefaultButton(new)
        box.exec()
        return {new: "new", skip: "skip", keep: "keep"}.get(box.clickedButton(), "skip")

    # ---- run files: PRM + recording + analysis, to compare and restore ----
    def _archive(self, entry: dict) -> None:
        prm_text = rec_bytes = mtime = None
        if self.prm.path:
            try:
                data = self.prm.path.read_bytes()
                for enc in ("utf-8", "cp1255", "latin-1"):
                    try:
                        prm_text = data.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
            except OSError:
                pass
        if self.rec and self.rec.path:
            try:
                rec_bytes = self.rec.path.read_bytes()
                mtime = self.rec.path.stat().st_mtime
            except OSError:
                pass
        doc = run_document(entry, self.prm.path.name if self.prm.path else "", prm_text,
                           entry.get("recording", ""), rec_bytes, mtime)
        self.history.save_run(entry, doc)

    def _hist_selected(self) -> None:
        e = self._selected_entry()
        p = self.history.run_path(e) if e else None
        for b in self.run_btns:
            b.setEnabled(bool(p and p.exists()))
        self.pair_btn.setEnabled(len(self._selected_entries()) == 2)

    def _selected_entries(self) -> list[dict]:
        rows = sorted({i.row() for i in self.hist_table.selectionModel().selectedRows()})
        return [self._hist_rows[r] for r in rows if 0 <= r < len(self._hist_rows)]

    def _selected_entry(self) -> dict | None:
        sel = self._selected_entries()
        return sel[0] if len(sel) == 1 else None

    def compare_selected_pair(self) -> None:
        sel = self._selected_entries()
        if len(sel) == 2:
            self.compare_runs(sel[0], sel[1])

    def compare_with_previous(self) -> None:
        cur = getattr(self, "_current_entry", None)
        if self.comparison is not None and cur is not None:
            self.compare_runs(self.comparison.prev, cur)

    def _run_curves(self, entry: dict) -> dict | None:
        """Position error (and driver command) of a saved run, with the time of its stop."""
        p = self.history.run_path(entry)
        if not p or not p.exists():
            return None
        try:
            doc = load_run(p)
            tmp = Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation) or tempfile.gettempdir())
            _, rec_path = extract_run(doc, tmp / "Tuner4164_compare" / hashlib.sha1(json_key(entry).encode()).hexdigest()[:10])
            rec = load_recording(rec_path, self.rg.value() * SAMPLE_TIME_4M)
        except (OSError, ValueError, TypeError):
            return None
        col = lambda role: None if (i := rec.guess(role)) is None else rec.data[:, i].astype(float)  # noqa: E731
        t = rec.time
        pe, ref, act, vel, vref, cmd = (col(r) for r in ("error", "reference", "actual", "velocity", "velocity_ref", "command"))
        if pe is None and ref is not None and act is not None:
            pe = ref - act
        if pe is None:
            return None
        if vref is not None:
            moving = np.abs(vref) > 1e-6 * (np.max(np.abs(vref)) + 1)
        elif ref is not None:
            moving = np.abs(np.gradient(ref)) > 1e-9
        elif vel is not None or act is not None:
            moving = moving_from_velocity(t, vel if vel is not None else np.gradient(act, t))
        else:
            moving = None
        mv = move_metrics(t, pe, moving, vref=vref, pos=act, cmd=cmd)
        return {"t": t, "pe": pe, "cmd": cmd, "stop": mv.stop_time if mv.has_stop else None}

    def compare_runs(self, a: dict, b: dict) -> None:
        """a = the earlier run, b = the later one."""
        comp = compare(a, b)
        dlg = QDialog(self)
        dlg.setWindowTitle("השוואת ריצות")
        dlg.resize(1150, 860)
        lay = QVBoxLayout(dlg)
        txt = QTextBrowser()
        title = (f"{html.escape(b.get('recording', ''))} ({html.escape(b.get('time', ''))}) לעומת "
                 f"{html.escape(a.get('recording', ''))} ({html.escape(a.get('time', ''))})")
        txt.setHtml(self._comparison_html(comp, title))
        lay.addWidget(txt, 2)
        ca, cb = self._run_curves(a), self._run_curves(b)
        self._last_pair = (comp, ca, cb)
        if ca and cb:
            colors = [("#2f6fb3", f"לפני: {a.get('recording', '')}"), ("#e07b00", f"אחרי: {b.get('recording', '')}")]
            full = pg.PlotWidget(title="שגיאת מיקום לאורך כל ההקלטה")
            zoom = pg.PlotWidget(title="סביב העצירה (שתי הריצות מיושרות לרגע העצירה)")
            cmdp = pg.PlotWidget(title="פקודת הדרייבר סביב העצירה")
            for w in (full, zoom, cmdp):
                w.setLayoutDirection(Qt.LeftToRight)
                w.showGrid(x=True, y=True, alpha=0.25)
                w.addLegend()
            zoom.setLabel("bottom", "זמן מהעצירה [s]")
            cmdp.setXLink(zoom)
            for cur, (color, name) in zip((ca, cb), colors):
                pen = pg.mkPen(color, width=1.5)
                full.plot(cur["t"], cur["pe"], pen=pen, name=name)
                if cur["stop"] is not None:
                    m = (cur["t"] >= cur["stop"] - 0.5) & (cur["t"] <= cur["stop"] + 4)
                    zoom.plot(cur["t"][m] - cur["stop"], cur["pe"][m], pen=pen, name=name)
                    if cur["cmd"] is not None:
                        cmdp.plot(cur["t"][m] - cur["stop"], cur["cmd"][m], pen=pen, name=name)
            lay.addWidget(zoom, 3)
            lay.addWidget(cmdp, 2)
            lay.addWidget(full, 2)
        else:
            lay.addWidget(QLabel("לאחת הריצות (או לשתיהן) אין קובץ ריצה שמור, ולכן מוצגים רק המדדים, בלי גרף."))
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("סגור")
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def _selected_doc(self) -> dict | None:
        e = self._selected_entry()
        p = self.history.run_path(e) if e else None
        if not p or not p.exists():
            QMessageBox.information(self, "אין קובץ ריצה", "לשורה הזו אין קובץ ריצה שמור (נותחה לפני גרסה 0.6.0).")
            return None
        try:
            return load_run(p)
        except (OSError, ValueError) as ex:
            QMessageBox.warning(self, "שגיאה", f"לא הצלחתי לקרוא את קובץ הריצה: {ex}")
            return None

    def restore_selected(self) -> None:
        doc = self._selected_doc()
        if doc:
            self._open_doc(doc)

    def _open_doc(self, doc: dict) -> None:
        e = doc.get("entry", {})
        tmp = Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation) or tempfile.gettempdir())
        key = hashlib.sha1(json_key(e).encode("utf-8")).hexdigest()[:10]
        prm_path, rec_path = extract_run(doc, tmp / "Tuner4164_restore" / key)
        unit, axis = e.get("unit"), e.get("axis")
        note = ""
        if unit and self.unit.findText(unit) >= 0:
            self.unit.blockSignals(True)
            self.unit.setCurrentText(unit)
            self.unit.blockSignals(False)
            self.prefs.setValue("unit", unit)
        elif unit:
            note = f" המסובב \"{html.escape(unit)}\" לא קיים כאן; אם תנתח, זה יישמר תחת \"{html.escape(self.unit.currentText())}\"."
        self.advice = None
        self.comparison = None
        if axis in AXES:
            self.axis.setCurrentText(axis)
        if prm_path:
            self.load_prm_file(str(prm_path))
            r = AXES.index(self.axis.currentText())
            for c, k in enumerate(TUNING_KEYS):        # the values used then, even if typed into the table
                if k in e.get("gains", {}):
                    self.table.setItem(r, c, QTableWidgetItem(str(e["gains"][k])))
        if rec_path:
            self.load_recording_file(str(rec_path))
        i = self.test.findData(e.get("test"))
        if i >= 0:
            self.test.setCurrentIndex(i)
        self._fill_history()
        self.tabs.setCurrentIndex(0)
        self.result.setHtml(f"<p><b>שוחזרה הריצה {html.escape(e.get('recording', ''))} מ-{html.escape(e.get('time', ''))}</b> "
                            f"(ה-PRM וההקלטה המקוריים). לחץ <b>נתח והמלץ</b> כדי לראות אותה שוב.{note}</p>")

    def save_selected_prm(self) -> None:
        doc = self._selected_doc()
        if not doc or doc.get("prm", {}).get("text") is None:
            return
        e = doc["entry"]
        default = str(Path(self._dir()) / f"{e.get('unit', '')}_{e.get('axis', '')}_{Path(e.get('recording', 'run')).stem}.prm")
        name, _ = QFileDialog.getSaveFileName(self, "שמירת ה-PRM של הריצה", default, "קובצי פרמטרים (*.prm)")
        if name:
            Path(name).write_bytes(doc["prm"]["text"].encode("cp1255", "replace"))
            self.statusBar().showMessage(f"נשמר: {name}. אפשר להוריד אותו לבקר (Download Controller Parameters).", 8000)

    def compare_selected_prm(self) -> None:
        doc = self._selected_doc()
        if not doc:
            return
        if not self.prm.values:
            QMessageBox.information(self, "השוואת PRM", "טען קודם קובץ PRM להשוואה.")
            return
        old = parse_prm(doc.get("prm", {}).get("text") or "").values
        now = self.prm.values
        keys = sorted(set(old) | set(now), key=lambda k: (k[0] != self.axis.currentText(), k))
        diff = [(f"{a}{k}", old.get((a, k)), now.get((a, k))) for a, k in keys if old.get((a, k)) != now.get((a, k))]
        dlg = QDialog(self)
        dlg.setWindowTitle("השוואת PRM")
        dlg.resize(560, 520)
        lay = QVBoxLayout(dlg)
        e = doc["entry"]
        lay.addWidget(QLabel(f"{len(diff)} ערכים שונים בין הריצה {e.get('recording', '')} ({e.get('time', '')}) "
                             f"לבין {self.prm.path.name if self.prm.path else 'ה-PRM הטעון'}:"))
        tbl = QTableWidget(len(diff), 3)
        tbl.setHorizontalHeaderLabels(["פרמטר", "בריצה הנבחרת", "ב-PRM הטעון"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, (k, a, b) in enumerate(diff):
            for c, v in enumerate((k, "—" if a is None else str(a), "—" if b is None else str(b))):
                tbl.setItem(r, c, QTableWidgetItem(v))
        lay.addWidget(tbl)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("סגור")
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        self._last_prm_diff = diff
        dlg.exec()

    def export_selected(self) -> None:
        e = self._selected_entry()
        p = self.history.run_path(e) if e else None
        if not p or not p.exists():
            return
        default = str(Path(self._dir()) / f"{e.get('unit', '')}_{e.get('axis', '')}_{Path(e.get('recording', 'run')).stem}.json")
        name, _ = QFileDialog.getSaveFileName(self, "ייצוא קובץ ריצה", default, "קובץ ריצה (*.json)")
        if name:
            shutil.copyfile(p, name)
            self.statusBar().showMessage(f"נשמר: {name}", 6000)

    def import_run(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "ייבוא קובץ ריצה", self._dir(), "קובץ ריצה (*.json)")
        if not name:
            return
        try:
            self._open_doc(load_run(name))
        except (OSError, ValueError) as ex:
            QMessageBox.warning(self, "שגיאה", f"לא הצלחתי לקרוא את קובץ הריצה: {ex}")

    def new_unit(self) -> bool:
        name, ok = QInputDialog.getText(self, "מסובב חדש", "שם המסובב או הבקר (למשל מספר סידורי):")
        name = name.strip()
        if not ok or not name:
            return False
        self.history.add_unit(name)
        if self.unit.findText(name) < 0:
            self.unit.addItem(name)
        self.unit.blockSignals(True)           # keep the analysis on screen; only the history follows
        self.unit.setCurrentText(name)
        self.unit.blockSignals(False)
        self.prefs.setValue("unit", name)
        self._fill_history()
        return True

    def _unit_changed(self, unit: str) -> None:
        self.prefs.setValue("unit", unit)
        self._axis_changed(self.axis.currentText())

    def new_calibration(self) -> None:
        axis, unit = self.axis.currentText(), self.unit.currentText()
        if QMessageBox.question(self, "מחיקת זיכרון", f"למחוק את הזיכרון של ציר {axis} ב-\"{unit}\" בלבד?\n"
                                f"גם קובצי הריצות השמורים שלו יימחקו. מסובבים וצירים אחרים לא יימחקו.") != QMessageBox.Yes:
            return
        self.history.clear(unit, axis)
        self.comparison = None
        self._fill_history()

    def _comparison_html(self, c=None, title: str | None = None) -> str:
        main = c is None and title is None
        c = self.comparison if main else c
        if c is None:
            return ("<p style='color:#5d6b7a'>זו ההקלטה הראשונה של הציר הזה בזיכרון. ההקלטה הבאה תושווה אליה "
                    "(ראה לשונית \"היסטוריית כיול\").</p>")
        color = {"better": "#1f9d55", "worse": "#c62f2f", "mixed": "#b86e00", "same": "#5d6b7a"}[c.verdict]
        out = [f"<div style='border:2px solid {color}; padding:8px; margin-bottom:6px;'>"
               f"<p style='font-size:13pt; color:{color}'><b>"
               + (title or f"לעומת ההקלטה הקודמת ({html.escape(c.prev.get('recording', ''))}, "
                           f"{html.escape(c.prev.get('time', ''))})")
               + f": {c.verdict_text}</b></p>"]
        if c.changed:
            out.append(f"<p>מה השתנה ב-PRM {'מאז' if main else 'בין שתי הריצות'}: " + ", ".join(
                f"<b>{LRM}{k}{LRM}</b> {LRM}{a}{LRM}→{LRM}{b}{LRM}" for k, a, b in c.changed) + "</p>")
        elif main:
            out.append("<p style='color:#b86e00'>לא השתנו פרמטרי כיול מאז ההקלטה הקודמת. אם שינית ערכים בבקר, "
                       "טען את קובץ ה-PRM העדכני.</p>")
        else:
            out.append("<p>פרמטרי הכיול זהים בשתי הריצות.</p>")
        if c.applied is True:
            out.append("<p>ההמלצה הקודמת יושמה ✔</p>")
        elif c.applied is False:
            out.append("<p style='color:#b86e00'>ההמלצה הקודמת לא יושמה בדיוק: " + ", ".join(
                f"{LRM}{k}{LRM} הומלץ {LRM}{want}{LRM} ועכשיו {LRM}{now}{LRM}" for k, want, now in c.not_applied) + "</p>")
        if c.rows:
            out.append("<table cellpadding='3' style='border-collapse:collapse'><tr><th>מדד</th><th>קודם</th>"
                       "<th>עכשיו</th><th></th></tr>")
            for label, unit, b, a, sign in c.rows:
                mark = {1: "<span style='color:#1f9d55'>✔ השתפר</span>", -1: "<span style='color:#c62f2f'>✘ החמיר</span>",
                        0: "<span style='color:#5d6b7a'>=</span>"}[sign]
                out.append(f"<tr><td>{label}</td><td>{LRM}{_fmt_metric(b, unit)}{LRM}</td>"
                           f"<td>{LRM}{_fmt_metric(a, unit)}{LRM}</td><td>{mark}</td></tr>")
            out.append("</table>")
        for n in c.notes:
            out.append(f"<p style='color:#b86e00'>⚠ {html.escape(n)}</p>")
        if main:
            seq = self.history.for_axis(self.unit.currentText(), self.axis.currentText())
            best = best_run(seq)
            if best is not None:
                vals = ", ".join(f"{k}={v}" for k, v in best.get("gains", {}).items() if k[:3] in ("KP[", "KI[", "KD["))
                out.append(f"<p>★ הריצה הטובה ביותר עד עכשיו: <b>{html.escape(best.get('recording', ''))}</b> "
                           f"({html.escape(best.get('time', ''))}) {LRM}{html.escape(vals)}{LRM}</p>")
        if main and c.verdict == "better":
            out.append("<p>השינוי האחרון עזר. ממשיכים לפי ההמלצה למטה.</p>")
        elif main and c.verdict == "same" and c.changed:
            out.append("<p>השינוי האחרון כמעט לא השפיע על התנועה.</p>")
        out.append("</div>")
        return "".join(out)

    def _plot_all(self) -> None:
        """Every recorded vector in its own strip, like the Data Viewer."""
        self.all_plots.clear()
        first = p = None
        rows = [i for i in range(len(self.rec.names)) if i != self.rec.time_column]
        for row, i in enumerate(rows):
            p = self.all_plots.addPlot(row=row, col=0)
            p.plot(self.rec.time, self.rec.data[:, i], pen=pg.mkPen(pg.intColor(row, len(rows) + 1), width=1))
            p.setLabel("left", self.rec.names[i])
            p.showGrid(x=True, y=True, alpha=0.2)
            if first is None:
                first = p
            else:
                p.setXLink(first)
        if p is not None:
            p.setLabel("bottom", "זמן [s]")

    def _plot_raw(self) -> None:
        self.analyze() if self.rec is not None and self._col("actual") is not None else None

    def _plot(self, t, ref, act, pe, cmd, test, vel=None, tacho=None) -> None:
        self.plot.clear()
        self.plot2.clear()
        self.plot.addLegend()
        if test == "full":
            if vel is not None:
                self.plot.plot(t, vel, pen=pg.mkPen("#1f6fb3", width=1.5), name="מהירות מהאנקודר")
            if tacho is not None:
                self.plot.plot(t, tacho, pen=pg.mkPen("#e08a00", width=1), name="חיישן מהירות")
            if vel is None and act is not None:
                self.plot.plot(t, act, pen=pg.mkPen("#e08a00", width=1.5), name="מיקום")
            self.plot.setTitle("שלב 1: חיישן מהירות מול אנקודר")
            if pe is not None:
                self.plot2.plot(t, pe, pen=pg.mkPen("#c62f2f", width=1))
                self.plot2.setTitle("שלב 2: שגיאת מיקום")
            return
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

    def show_guide(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("איזו הקלטה לעשות")
        dlg.setLayoutDirection(Qt.RightToLeft)
        lay = QVBoxLayout(dlg)
        text = QTextBrowser()
        text.setHtml(GUIDE_FULL)
        lay.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.resize(760, 640)
        dlg.exec()

    def _missing_for_tuning(self) -> list[str]:
        """Vectors the recording lacks for KP/KI/KD advice (what was asked, what happened, the command)."""
        if self.rec is None:
            return []
        missing = []
        if self.rec.guess("error") is None and self.rec.guess("reference") is None:
            missing.append("Position Error או Desired Position (מה הבקר ביקש)")
        if self.rec.guess("actual") is None and self.rec.guess("error") is None:
            missing.append("Position (מה הציר עשה)")
        if self.rec.guess("command") is None:
            missing.append("Driver Command (הפקודה למנוע)")
        return missing

    def _show_advice(self) -> None:
        a = self.advice
        axis = self.axis.currentText()
        parts = [self._comparison_html()]
        missing = self._missing_for_tuning()
        if missing:
            parts.append("<div style='background:#e6f0fa; border:2px solid #1f6fb3; padding:8px;'>"
                         "<p style='font-size:12pt'><b>כדי לקבל המלצות ל-KP / KI / KD חסרים בהקלטה הזו:</b></p><ul>"
                         + "".join(f"<li>{html.escape(m)}</li>" for m in missing)
                         + "</ul><p>ראה את ההסבר בכחול למעלה, או לחץ \"הסבר מלא: איזו הקלטה לעשות\".</p></div>")
        for w in a.warnings:
            if w.startswith("חיישן המהירות (הטאכו) לא מכויל"):
                parts.append("<div style='background:#fdecec; border:2px solid #c62f2f; padding:8px;'>"
                             f"<p style='color:#a11d1d; font-size:12pt'><b>⚠ {html.escape(w)}</b></p></div>")
            else:
                parts.append(f"<p style='color:#b86e00'>⚠ {html.escape(w)}</p>")
        parts.append("<ul>" + "".join(f"<li>{html.escape(f)}</li>" for f in a.findings) + "</ul>")
        if a.status == "stop":
            parts.append("<p style='color:#c62f2f'><b>אין המלצה לשינוי מההקלטה הזו.</b></p>")
        elif a.primary:
            p = a.primary
            if not p.complete:
                unknown = ", ".join(f"{axis}{k}" for k, o, _ in p.items() if o is None)
                parts.append(f"<p style='color:#c62f2f'><b>הערך הנוכחי של {unknown} לא ידוע, ולכן אין מספר חדש. "
                             f"טען את קובץ ה-PRM של הבקר (או הקלד את הערך בטבלה בשורה {axis}) ולחץ שוב נתח והמלץ.</b></p>")
            parts.append(
                f"<div style='background:#e8f4ec; border:1px solid #1f9d55; padding:8px;'>"
                f"<p style='font-size:13pt'><b>השינוי המומלץ עכשיו"
                + (f" ({LRM}{'+' if p.percent > 0 else ''}{p.percent}%{LRM})" if p.show_percent else "")
                + ":</b></p>"
                f"<ul style='font-size:13pt'>{self._items_html(p, axis)}</ul>"
                f"<p>{html.escape(p.reason)}</p>"
                + (f"<p>בקונסולה של תוכנת הבקר: <code>{LRM}{html.escape(self._command())}{LRM}</code>, "
                   f"ואז להקליט שוב באותה בדיקה.</p>" if p.complete else "")
                + f"<p style='color:#555'>{html.escape(SETS_NOTE)}</p></div>")
            if len(a.changes) > 1:
                parts.append("<p><b>אחר כך, אם עדיין צריך:</b></p><ul>" + "".join(
                    (f"<li>{'+' if c.percent > 0 else ''}{c.percent}%: " if c.show_percent else "<li>")
                    + f"{html.escape(c.reason)}"
                    f"<ul>{self._items_html(c, axis)}</ul></li>"
                    for c in a.changes[1:]) + "</ul>")
        else:
            parts.append("<p style='color:#1f9d55'><b>לא נדרש שינוי.</b></p>")
        self.result.setHtml("".join(parts))
        ok = a.primary is not None and a.primary.complete and a.status != "stop"
        self.copy_btn.setEnabled(ok)
        self.save_btn.setEnabled(ok)
        self.prev_btn.setEnabled(self.comparison is not None)

    @staticmethod
    def _items_html(c, axis: str) -> str:
        out = []
        for k, old, new in c.items():
            what = "ערך נוכחי לא ידוע" if old is None else f"מ-{LRM}{old}{LRM} ל-{LRM}{new}{LRM}"
            m = re.search(r"\[(\d)\]$", k)
            when = f" ({SET_NAMES[int(m.group(1))]})" if m and int(m.group(1)) in SET_NAMES else ""
            out.append(f"<li><b>{LRM}{axis}{k}{LRM}</b>{when}: {what}</li>")  # LRM keeps "-19126" intact
        return "".join(out)

    def _command(self) -> str:
        axis = self.axis.currentText()
        return ";".join(f"{axis}{k}={new}" for k, _, new in self.advice.primary.items())

    def copy_command(self) -> None:
        QApplication.clipboard().setText(self._command())
        self.statusBar().showMessage(f"הועתק: {self._command()}", 4000)

    def save_prm(self) -> None:
        p, axis = self.advice.primary, self.axis.currentText()
        default = str(Path(self._dir()) / f"tune_{axis}{p.param.replace('[', '').replace(']', '')}.prm")
        name, _ = QFileDialog.getSaveFileName(self, "שמירת PRM עם השינוי", default, "קובצי פרמטרים (*.prm)")
        if not name:
            return
        path = Path(name) if name.lower().endswith(".prm") else Path(name + ".prm")
        note = ", ".join(f"{axis}{k}: {o} -> {n}" for k, o, n in p.items()) + (f" ({p.percent:+d}%)" if p.show_percent else "")
        path.write_bytes(prm_text({(axis, k): n for k, _, n in p.items()}, note).encode("ascii"))
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
