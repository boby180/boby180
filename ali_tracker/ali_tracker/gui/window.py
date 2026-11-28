"""החלון הראשי: טבלת החבילות, פרטי החבילה הנבחרת וסנכרון עם Gmail."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..config import Config
from ..money import fmt, ils_text, to_ils
from ..secret import unprotect
from ..status import DELIVERED, LABEL, Advice, advice, days_ago, is_late
from ..store import Row, Store
from .settings import SettingsDialog
from .images import ImageLoader
from .rates import RateFetcher
from ..rates import date_he
from .workers import SyncJob, SyncSignals

COLUMNS = ["מספר מעקב", "מה בפנים", "מחיר", "סטטוס", "מה לעשות", "עדכון אחרון", "ימים מההזמנה"]
TODO_COL = 4
MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר",
          "אוקטובר", "נובמבר", "דצמבר"]
CANCELLED = ("cancel", "refund", "closed")
RATE_EVERY_HOURS = 6        # בנק ישראל מפרסם שער יציג פעם ביום עסקים
# הראשון הוא ברירת המחדל: בלי החבילות שכבר התקבלו
FILTERS = [("לא התקבלו", "open"), ("דורשות פעולה", "urgent"), ("הכול", "all")]
URGENT_COLOR = QColor("#e67e22")


def _ago(d: datetime | None) -> str:
    return "" if d is None else days_ago((datetime.now(timezone.utc) - d).days)


def seller_message(row: Row, now: datetime | None = None) -> str:
    """הודעה מוכנה למוכר (באנגלית) על חבילה שעבר זמן המשלוח שלה."""
    now = now or datetime.now(timezone.utc)
    age = (now - (row.ordered_at or row.first_seen)).days
    orders = ", ".join(row.order_ids) or row.key
    track = f" (tracking number {row.tracking})" if row.tracking else ""
    return (f"Hello,\n"
            f"My order {orders}{track} was placed {age} days ago and I still have not received it. "
            f"The expected delivery time has passed.\n"
            f"Could you please check what is happening with the shipment and let me know "
            f"when it will arrive? If it cannot arrive soon, I would like a refund.\n"
            f"Thank you.")


def ltr(text: str) -> str:
    """טקסט באנגלית בתוך ממשק מימין לשמאל: שהשלוש נקודות יישארו בסוף המילים."""
    return text if any("\u0590" <= ch <= "\u05ff" for ch in text) else "\u202a" + text + "\u202c"


def summary(row: Row) -> str:
    if not row.items:
        return "פרטי המוצר יופיעו כשיגיע מייל עליו"
    first = row.items[0].name
    return first if len(row.items) == 1 else f"{first} ועוד {len(row.items) - 1}"


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.pool = QThreadPool(self)
        self.syncing = False
        self.rows: list[Row] = []
        self.sync_signals = SyncSignals(self)
        self.sync_signals.progress.connect(self._sync_progress)
        self.sync_signals.done.connect(self._sync_done)
        self.sync_signals.failed.connect(self._sync_failed)
        self.sync_quiet = False
        self.images = ImageLoader(cfg.image_dir, self)
        self.rates = RateFetcher(self)
        self.rates.done.connect(self._rate_done)
        self.rates.failed.connect(self._rate_failed)

        self.setWindowTitle("Ali Tracker – החבילות שלי מאלי אקספרס")
        self.resize(1150, 760)

        # ---- סרגל עליון
        self.sync_btn = QPushButton("סנכרן עם Gmail")
        self.sync_btn.clicked.connect(self.sync)
        settings_btn = QPushButton("הגדרות")
        settings_btn.clicked.connect(self.open_settings)
        self.filter = QComboBox()
        for label, key in FILTERS:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש לפי מוצר, מספר מעקב או מספר הזמנה…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(self.sync_btn)
        top.addWidget(settings_btn)
        top.addSpacing(12)
        top.addWidget(QLabel("הצג:"))
        top.addWidget(self.filter)
        top.addWidget(self.search, 1)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.RichText)
        self.banner.setOpenExternalLinks(False)
        self.banner.linkActivated.connect(self._banner_link)
        self.banner.setStyleSheet("padding: 6px 10px; border-radius: 6px;"
                                  "background: rgba(230,126,34,0.18);")
        self.banner.hide()

        # ---- טבלה
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        head = self.table.horizontalHeader()
        for c, mode in enumerate([QHeaderView.ResizeToContents, QHeaderView.Stretch,
                                  QHeaderView.ResizeToContents,
                                  QHeaderView.ResizeToContents, QHeaderView.Stretch,
                                  QHeaderView.ResizeToContents, QHeaderView.ResizeToContents]):
            head.setSectionResizeMode(c, mode)
        self.table.itemSelectionChanged.connect(self.show_details)

        # ---- פרטים
        self.details = QWidget()
        self.details_layout = QVBoxLayout(self.details)
        self.details_layout.setAlignment(Qt.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.details)
        scroll.setFrameShape(QFrame.NoFrame)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(scroll)
        split.setSizes([430, 300])

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(top)
        root.addWidget(self.banner)
        root.addWidget(split, 1)
        self.setCentralWidget(central)
        self.status = self.statusBar()
        self.rate_label = QLabel()
        self.rate_label.setStyleSheet("padding: 0 8px;")
        self.status.addPermanentWidget(self.rate_label)
        self.month_label = QLabel()
        self.month_label.setStyleSheet("font-weight: bold; padding: 0 8px;")
        self.status.addPermanentWidget(self.month_label)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.sync(quiet=True))
        self.rate_timer = QTimer(self)
        self.rate_timer.timeout.connect(self.check_rate)
        self.rate_timer.start(60 * 60_000)
        QTimer.singleShot(800, self.check_rate)
        self._schedule()
        self.refresh()
        if self.configured():
            QTimer.singleShot(300, lambda: self.sync(quiet=True))
        else:
            QTimer.singleShot(300, self._first_run)

    # ---------------------------------------------------------------- הגדרות / סנכרון
    def configured(self) -> bool:
        return bool(self.cfg.gmail and unprotect(self.cfg.app_password))

    def _first_run(self) -> None:
        self.show_message("ברוכים הבאים! כדי להתחיל צריך לחבר את Gmail: לחץ «הגדרות».")
        self.open_settings()

    def _schedule(self) -> None:
        self.timer.stop()
        if self.cfg.auto_sync_minutes > 0:
            self.timer.start(self.cfg.auto_sync_minutes * 60_000)

    def open_settings(self) -> None:
        old = (self.cfg.gmail, self.cfg.days_back)
        if SettingsDialog(self.cfg, self).exec():
            self._schedule()
            self.refresh()
            self.check_rate(force=True)
            if (self.cfg.gmail, self.cfg.days_back) != old:      # חשבון אחר או תקופה ארוכה יותר
                self.store.set_meta("last_uid", "0")
            if self.configured():
                self.sync()

    def sync(self, quiet: bool = False) -> None:
        if self.syncing:
            return
        if not self.configured():
            if not quiet:
                self.open_settings()
            return
        self.syncing = True
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("מסנכרן…")
        self.sync_quiet = quiet
        self.status.showMessage(f"מתחבר ל-Gmail ({self.cfg.gmail})…")
        self.sync_job = SyncJob(self.cfg.gmail, unprotect(self.cfg.app_password), self.cfg.days_back,
                                int(self.store.get_meta("last_uid", "0") or 0),
                                self.store.get_meta("uidvalidity"), self.sync_signals)
        self.sync_job.setAutoDelete(False)
        self.pool.start(self.sync_job)

    def _sync_progress(self, n: int, total: int) -> None:
        self.status.showMessage(f"קורא מיילים של אלי אקספרס מ-Gmail… {n} מתוך {total}")

    def _sync_done(self, events, max_uid: int, validity: str) -> None:
        for ev in events:
            self.store.apply(ev)
        self.store.set_meta("last_uid", str(max_uid))
        self.store.set_meta("uidvalidity", validity)
        self.store.set_meta("last_sync", datetime.now(timezone.utc).isoformat())
        self._sync_finished()
        self.refresh()
        self.status.showMessage(f"הסנכרון הסתיים: נקראו {len(events)} מיילים חדשים. " +
                                self.status.currentMessage(), 15000)
        if not events and not self.rows and not self.sync_quiet:
            QMessageBox.information(
                self, "לא נמצאו מיילים",
                f"ההתחברות ל-Gmail הצליחה, אבל לא נמצאו מיילים מ-aliexpress.com "
                f"ב-{self.cfg.days_back} הימים האחרונים בתיבה {self.cfg.gmail}.")

    def _sync_failed(self, msg: str) -> None:
        quiet = self.sync_quiet
        self._sync_finished()
        self.status.showMessage("הסנכרון נכשל", 10000)
        if not quiet:
            QMessageBox.warning(self, "הסנכרון נכשל", msg)
        else:
            self.show_message(f"הסנכרון האחרון נכשל: {msg.splitlines()[0]}")

    def _sync_finished(self) -> None:
        self.syncing = False
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("סנכרן עם Gmail")

    def show_message(self, html: str) -> None:
        self.banner.setText(html)
        self.banner.setVisible(bool(html))

    # ---------------------------------------------------------------- טבלה
    def visible_rows(self) -> list[Row]:
        mode = self.filter.currentData()
        q = self.search.text().strip().lower()
        now = datetime.now(timezone.utc)
        out = []
        for r in self.rows:
            if mode == "urgent" and not self.advice_for(r, now).urgent:
                continue
            if mode == "open" and (r.done or r.status == DELIVERED):
                continue
            if q:
                hay = " ".join([r.tracking, *r.order_ids, *(i.name + " " + i.sku for i in r.items)]).lower()
                if q not in hay:
                    continue
            out.append(r)
        return out

    def refresh(self) -> None:
        keep = self._selected_key()
        self.rows = self.store.rows()
        rows = self.visible_rows()
        now = datetime.now(timezone.utc)
        bold = QFont()
        bold.setBold(True)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            a = self.advice_for(r, now)
            days = (now - (r.ordered_at or r.first_seen)).days
            values = [r.tracking or "עדיין אין", summary(r), self.row_price(r),
                      "התקבל ✓" if r.done else LABEL.get(r.status, r.status_text or "לא ידוע"),
                      a.text, _ago(r.last_update), str(days)]
            for c, text in enumerate(values):
                item = QTableWidgetItem(ltr(text) if c in (0, 1) else text)
                item.setData(Qt.UserRole, r.key)
                if c == 1:
                    item.setToolTip("\n".join(
                        f"{x.name} | {x.sku} {x.qty}" + (f" | {self.price(x.price)}" if x.price else "")
                        for x in r.items))
                if c == TODO_COL and a.urgent:
                    item.setForeground(URGENT_COLOR)
                    item.setFont(bold)
                self.table.setItem(i, c, item)
            if r.key == keep:
                self.select_row(i)
        urgent = sum(self.advice_for(r, now).urgent for r in self.rows)
        active = sum(not (r.done or r.status == DELIVERED) for r in self.rows)
        last = self.store.get_meta("last_sync")
        when = _ago(datetime.fromisoformat(last)) if last else "עוד לא"
        self.status.showMessage(f"{active} עוד לא התקבלו · {urgent} דורשות פעולה · סנכרון אחרון: {when}")
        self._update_banner()
        self._update_month()
        self._update_rate_label()
        if self.table.rowCount() and not self.table.selectedItems():
            self.select_row(0)
        if not rows:
            self.show_details()

    # ---------------------------------------------------------------- מחירים
    def price(self, text: str) -> str:
        return ils_text(text, self.cfg.usd_rate, self.cfg.eur_rate)

    def row_price(self, r: Row) -> str:
        """הסכום בשקלים של ההזמנות שבחבילה."""
        got = [to_ils(t, self.cfg.usd_rate, self.cfg.eur_rate) for t in r.totals.values()]
        if not got or None in got:
            return " + ".join(r.totals.values())
        return fmt(sum(v for v, _ in got), any(c for _, c in got))

    def month_total(self, now: datetime | None = None) -> tuple[float, int, bool]:
        """סכום ההזמנות מתחילת החודש (בלי הזמנות שבוטלו): (שקלים, מספר הזמנות, האם יש המרה)."""
        local = (now or datetime.now(timezone.utc)).astimezone()
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total, count, converted = 0.0, 0, False
        for _oid, amount, status_text in self.store.orders_since(start):
            if any(w in status_text.lower() for w in CANCELLED):
                continue
            count += 1
            got = to_ils(amount, self.cfg.usd_rate, self.cfg.eur_rate)
            if got:
                total += got[0]
                converted |= got[1]
        return total, count, converted

    def check_rate(self, force: bool = False) -> None:
        """בודק את השער היציג בבנק ישראל, אם עברו כמה שעות מהבדיקה הקודמת."""
        if not self.cfg.auto_rate:
            self._update_rate_label()
            return
        last = self.cfg.rate_checked
        if not force and last:
            try:
                hours = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
            except ValueError:
                hours = RATE_EVERY_HOURS
            if hours < RATE_EVERY_HOURS:
                self._update_rate_label()
                return
        self.rates.fetch()

    def _rate_done(self, got: dict) -> None:
        usd, day = got["USD"]
        self.cfg.usd_rate = round(usd, 4)
        if "EUR" in got:
            self.cfg.eur_rate = round(got["EUR"][0], 4)
        self.cfg.rate_date = day
        self.cfg.rate_checked = datetime.now(timezone.utc).isoformat()
        self.cfg.save()
        self.refresh()

    def _rate_failed(self, msg: str) -> None:
        self._update_rate_label(f"לא הצלחתי לבדוק את השער בבנק ישראל ({msg}). משתמש בשער השמור.")

    def _update_rate_label(self, error: str = "") -> None:
        rate = f"$1 = ₪{self.cfg.usd_rate:g}"
        if self.cfg.auto_rate and self.cfg.rate_date:
            self.rate_label.setText(f"{rate} (בנק ישראל, {date_he(self.cfg.rate_date)})")
        else:
            self.rate_label.setText(f"{rate} (ידני)" if not self.cfg.auto_rate else rate)
        if error:
            tip = error
        elif self.cfg.auto_rate:
            tip = (f"שער יציג של בנק ישראל. אירו: ₪{self.cfg.eur_rate:g}. "
                   "מתעדכן אוטומטית; אפשר לכבות בהגדרות.")
        else:
            tip = "השער שהזנת בהגדרות."
        self.rate_label.setToolTip(tip)
        if error:
            self.rate_label.setText(self.rate_label.text() + " ⚠")

    def _update_month(self) -> None:
        total, count, converted = self.month_total()
        month = MONTHS[datetime.now().astimezone().month - 1]
        self.month_label.setText(f"הזמנות ב{month}: {fmt(total, converted)} · {count} הזמנות")

    def _update_banner(self) -> None:
        seller = self.store.get_meta("seller_msg_at")
        seen = self.store.get_meta("seller_msg_seen")
        if seller and seller > seen:
            self.show_message("📩 יש הודעות חדשות ממוכרים. "
                              "<a href='https://www.aliexpress.com/p/message/index.html'>לפתוח באלי אקספרס</a>"
                              " &nbsp;·&nbsp; <a href='app:seen'>סמן כנקרא</a>")
        elif self.banner.text().startswith("📩"):
            self.show_message("")

    def _banner_link(self, link: str) -> None:
        if link.startswith("http"):
            QDesktopServices.openUrl(QUrl(link))
        if self.banner.text().startswith("📩"):
            self.store.set_meta("seller_msg_seen", self.store.get_meta("seller_msg_at"))
            self.show_message("")

    def advice_for(self, r: Row, now: datetime | None = None) -> Advice:
        return advice(r.status, r.last_update, r.done, now, ordered_at=r.ordered_at,
                      late_days=self.cfg.late_days, messaged_at=r.messaged_at)

    def select_row(self, i: int) -> None:
        """QTableWidget.selectRow לא בוחר כלום כשהממשק מימין לשמאל, לכן בוחרים דרך setCurrentCell."""
        self.table.setCurrentCell(i, 0)

    def _selected_key(self) -> str | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _selected(self) -> Row | None:
        key = self._selected_key()
        return next((r for r in self.rows if r.key == key), None)

    # ---------------------------------------------------------------- פרטים
    def show_details(self) -> None:
        lay = self.details_layout
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.hide()                 # מיד, כדי שהפרטים הישנים לא יופיעו מתחת לחדשים
                w.deleteLater()
        r = self._selected()
        if r is None:
            empty = QLabel("אין חבילות להצגה. לחץ «סנכרן עם Gmail»." if not self.rows
                           else "בחר חבילה מהטבלה.")
            lay.addWidget(empty)
            return
        a = self.advice_for(r)
        title = QLabel(f"<b style='font-size:14pt'>{r.tracking or 'הזמנה ' + r.key}</b> &nbsp; "
                       f"{LABEL.get(r.status, '')} "
                       f"<span style='color:gray'>({r.status_text})</span>" if r.status_text else
                       f"<b style='font-size:14pt'>{r.tracking or 'הזמנה ' + r.key}</b>")
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(title)
        todo = QLabel(("⚠ " if a.urgent else "") + "מה לעשות: " + a.text)
        todo.setStyleSheet(f"font-size: 12pt; {'color:#e67e22; font-weight:bold;' if a.urgent else ''}")
        lay.addWidget(todo)

        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 6, 0, 6)
        for n, it in enumerate(r.items):
            pic = QLabel()
            pic.setFixedSize(QSize(72, 72))
            pic.setAlignment(Qt.AlignCenter)
            pic.setStyleSheet("border: 1px solid rgba(128,128,128,0.4);")
            self._load_image(it.image, pic)
            price = f" &nbsp; <b>{self.price(it.price)}</b>" if it.price else ""
            text = QLabel(f"<b>{it.name}</b><br>{it.sku} {it.qty}{price}")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(pic, n, 0)
            grid.addWidget(text, n, 1)
        grid.setColumnStretch(1, 1)
        lay.addWidget(grid_w)

        orders = " · ".join(f"הזמנה {o}" + (f" ({self.price(r.totals[o])})" if o in r.totals else "")
                            for o in r.order_ids)
        if orders:
            info = QLabel(orders)
            info.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(info)

        btns = QHBoxLayout()
        if r.tracking:
            oid = r.order_ids[0] if r.order_ids else ""
            self._link_button(btns, "מעקב באלי אקספרס",
                              f"https://www.aliexpress.com/p/tracking/index.html?tradeOrderId={oid}"
                              f"&trackingNumber={r.tracking}")
            self._link_button(btns, "מעקב ב-17TRACK", f"https://t.17track.net/he#nums={r.tracking}")
        for oid in r.order_ids[:3]:
            self._link_button(btns, f"פתח הזמנה …{oid[-5:]}",
                              f"https://www.aliexpress.com/p/order/detail.html?orderId={oid}")
        if is_late(r.status, r.done, r.ordered_at, self.cfg.late_days):
            msg = QPushButton("✉ הודעה למוכר" if r.messaged_at is None else "✉ הודעה נוספת למוכר")
            msg.setToolTip("מעתיק הודעה מוכנה באנגלית ופותח את ההזמנה באלי אקספרס")
            msg.clicked.connect(lambda: self.message_seller(r))
            btns.addWidget(msg)
        done = QPushButton("בטל «קיבלתי»" if r.done else "✓ קיבלתי ובדקתי")
        done.clicked.connect(lambda: self._toggle_done(r))
        btns.addStretch()
        btns.addWidget(done)
        box = QWidget()
        box.setLayout(btns)
        lay.addWidget(box)

    def _link_button(self, layout: QHBoxLayout, text: str, url: str) -> None:
        b = QPushButton(text)
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(b)

    def message_seller(self, r: Row) -> None:
        """אלי אקספרס לא מאפשרת לתוכנה לשלוח הודעה בעצמה, אז מכינים הכול ונשאר רק להדביק."""
        QGuiApplication.clipboard().setText(seller_message(r))
        if r.order_ids:
            QDesktopServices.openUrl(QUrl(
                f"https://www.aliexpress.com/p/order/detail.html?orderId={r.order_ids[0]}"))
        box = QMessageBox(self)
        box.setWindowTitle("הודעה למוכר")
        box.setText("ההודעה הועתקה ✓\n\nבדף ההזמנה שנפתח לוחצים «Contact seller», "
                    "מדביקים (Ctrl+V) ושולחים.\n\nשלחת את ההודעה?")
        sent = box.addButton("שלחתי", QMessageBox.YesRole)
        box.addButton("עוד לא", QMessageBox.NoRole)
        box.exec()
        if box.clickedButton() is sent:
            self.store.set_messaged(r, datetime.now(timezone.utc))
            self.refresh()
            self.show_details()

    def _toggle_done(self, r: Row) -> None:
        self.store.set_done(r, not r.done)
        self.refresh()
        self.show_details()

    def _load_image(self, url: str, label: QLabel) -> None:
        if not url:
            label.setText("—")
            return
        label.setText("…")
        self.images.get(url, lambda path: self._set_pixmap(label, path))

    @staticmethod
    def _set_pixmap(label: QLabel, path: str) -> None:
        pm = QPixmap(path) if path else QPixmap()
        if pm.isNull():
            label.setText("אין\nתמונה")
        else:
            label.setPixmap(pm.scaled(70, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.rate_timer.stop()
        self.store.close()
        super().closeEvent(event)
