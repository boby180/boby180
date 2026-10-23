"""Main window: file list, toolbar, video controls, keyboard, slideshow."""
import os
import sys

os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")   # plays AVI, WMV, FLV, MKV and more

from PySide6.QtCore import QEventLoop, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QImage, QImageReader, QKeyEvent, QMovie, QPalette, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QSizePolicy, QSlider, QSpinBox, QSplitter, QStyle, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
)

from .media import ANIMATED_EXT, IMAGE_EXT, VIDEO_EXT, Playlist, collect_files, ext, fmt_size, fmt_time
from .version import __version__
from .view import MediaView

ACCENT = "#f2b63d"
SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]

HELP = """
<h3>קיצורי מקשים</h3>
<table cellspacing="4">
<tr><td><b>→ / ←</b></td><td>הקובץ הבא / הקודם (גם PgDn / PgUp)</td></tr>
<tr><td><b>רווח</b></td><td>ניגון / השהיה של סרטון, או עצירה / המשך של מצגת התמונות</td></tr>
<tr><td><b>+ / −</b></td><td>הגדלה / הקטנה (גם גלגלת העכבר, במקום שהעכבר מצביע)</td></tr>
<tr><td><b>0</b></td><td>חזרה לגודל החלון</td></tr>
<tr><td><b>1</b></td><td>גודל אמיתי, פיקסל מול פיקסל</td></tr>
<tr><td><b>Shift + חצים</b></td><td>הזזת התמונה כשהיא מוגדלת (או גרירה עם העכבר)</td></tr>
<tr><td><b>לחיצה כפולה</b></td><td>הגדלה פי 2.5 במקום הלחיצה / חזרה לגודל החלון</td></tr>
<tr><td><b>Ctrl + → / ←</b></td><td>5 שניות קדימה / אחורה</td></tr>
<tr><td><b>J / L</b></td><td>10 שניות אחורה / קדימה</td></tr>
<tr><td><b>↑ / ↓</b></td><td>עוצמת קול</td></tr>
<tr><td><b>[ / ]</b></td><td>האטה / האצה</td></tr>
<tr><td><b>M</b></td><td>השתקה</td></tr>
<tr><td><b>A / S / R</b></td><td>ניגון רצוף / סדר אקראי / חזרה בסוף הרשימה</td></tr>
<tr><td><b>F או F11</b></td><td>מסך מלא (Esc ליציאה)</td></tr>
<tr><td><b>P</b></td><td>הצגת / הסתרת רשימת הקבצים</td></tr>
<tr><td><b>O</b></td><td>פתיחת קבצים</td></tr>
<tr><td><b>Home / End</b></td><td>הקובץ הראשון / האחרון</td></tr>
</table>
<p>המקשים עובדים גם כשהמקלדת על עברית.<br>
אפשר לגרור קבצים או תיקייה לחלון, או לבחור "פתח באמצעות" על קובץ.</p>
"""


def load_image(path):
    """Return (pixmap, movie, error). movie is set for animated GIF/WEBP."""
    if ext(path) in ANIMATED_EXT:
        mv = QMovie(path)
        if mv.isValid() and mv.frameCount() != 1:
            return None, mv, None
    r = QImageReader(path)
    r.setAutoTransform(True)    # turn phone photos the right way up
    img = r.read()
    if img.isNull():
        img = _load_with_pillow(path)
    if img is None or img.isNull():
        return None, None, r.errorString()
    return QPixmap.fromImage(img), None, None


def _load_with_pillow(path):
    """HEIC/AVIF and other formats Qt can't read."""
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGBA")
            data = im.tobytes()
            return QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
    except Exception:
        return None


class _ThumbSignal(QObject):
    done = Signal(str, QImage)


class _ThumbJob(QRunnable):
    def __init__(self, path, sig):
        super().__init__()
        self.path, self.sig = path, sig

    def run(self):
        r = QImageReader(self.path)
        r.setAutoTransform(True)
        size = r.size()
        if size.isValid():
            size.scale(112, 76, Qt.KeepAspectRatio)
            r.setScaledSize(size)
        img = r.read()
        if img.isNull():
            img = _load_with_pillow(self.path) or QImage()
            if not img.isNull():
                img = img.scaled(112, 76, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.sig.done.emit(self.path, img)


def _letter(e: QKeyEvent):
    """The letter key pressed, by position, so it works in a Hebrew layout too."""
    vk = e.nativeVirtualKey()
    if sys.platform == "win32" and 0x41 <= vk <= 0x5A:
        return chr(vk)
    k = e.key()
    if Qt.Key_A <= k <= Qt.Key_Z:
        return chr(k)
    if 0x61 <= vk <= 0x7A:
        return chr(vk).upper()
    return None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("צופה מדיה")
        self.setAcceptDrops(True)
        self.resize(1280, 800)
        self.cfg = QSettings("boby180", "MediaViewer")
        self.pl = Playlist()
        self.movie = None
        self.slide_paused = False
        self._seeking = False
        self._thumbs = {}         # path -> QIcon
        self._thumb_asked = set()
        self._thumb_sig = _ThumbSignal()
        self._thumb_sig.done.connect(self._thumb_done)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(2, min(4, os.cpu_count() or 2)))

        self.view = MediaView()
        self.view.zoomChanged.connect(lambda p: self.zoom_btn.setText(f"{p}%"))
        self.view.clicked.connect(self.toggle_play)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.view.video)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._media_error)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(self._duration)
        self.player.playbackStateChanged.connect(self._play_state)
        self.view.video.nativeSizeChanged.connect(lambda s: self._info())

        self.slide = QTimer(self, singleShot=True)
        self.slide.timeout.connect(lambda: self.next(auto=True))
        self.skip = QTimer(self, singleShot=True, interval=1500)
        self.skip.timeout.connect(lambda: self.next(auto=True))
        self.idle = QTimer(self, singleShot=True, interval=2500)
        self.idle.timeout.connect(self._go_idle)

        self._build_toolbar()
        self._build_list()
        self._build_controls()

        stage = QWidget()
        lay = QVBoxLayout(stage)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.controls)
        self.split = QSplitter()
        self.split.addWidget(self.list_panel)
        self.split.addWidget(stage)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([300, 980])
        self.setCentralWidget(self.split)

        self.status_pos = QLabel()
        self.status_name = QLabel()
        self.status_info = QLabel()
        for w in (self.status_pos, self.status_name, self.status_info):
            self.statusBar().addWidget(w)
        self.statusBar().addPermanentWidget(QLabel(f"v{__version__}"))

        self._restore()
        self._show_welcome()
        QApplication.instance().installEventFilter(self)

    # ---------- building the window ----------
    def _act(self, text, tip, slot, checkable=False, icon=None):
        a = QAction(text, self)
        a.setToolTip(tip)
        a.setCheckable(checkable)
        if icon is not None:
            a.setIcon(self.style().standardIcon(icon))
        a.triggered.connect(slot)
        return a

    def _build_toolbar(self):
        tb = QToolBar("כלים")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        self.toolbar = tb
        tb.addAction(self._act("פתיחת קבצים", "פתיחת קבצים (O)", self.open_files, icon=QStyle.SP_DialogOpenButton))
        tb.addAction(self._act("פתיחת תיקייה", "פתיחת תיקייה שלמה, כולל תתי-תיקיות", self.open_folder, icon=QStyle.SP_DirOpenIcon))
        tb.addSeparator()
        self.a_auto = self._act("ניגון רצוף", "מעבר אוטומטי לקובץ הבא (A)", self._toggled, True, QStyle.SP_MediaSeekForward)
        self.a_shuffle = self._act("אקראי", "סדר אקראי (S)", self._toggled, True)
        self.a_loop = self._act("חזרה", "חזרה להתחלה בסוף הרשימה (R)", self._toggled, True)
        for a in (self.a_auto, self.a_shuffle, self.a_loop):
            tb.addAction(a)
        tb.addWidget(QLabel("  תמונה כל "))
        self.sec = QSpinBox()
        self.sec.setRange(1, 600)
        self.sec.setSuffix(" שנ׳")
        self.sec.setToolTip("כמה שניות להציג כל תמונה בניגון רצוף")
        self.sec.valueChanged.connect(self._sec_changed)
        tb.addWidget(self.sec)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.a_list = self._act("רשימה", "הצגת / הסתרת רשימת הקבצים (P)", self._list_toggled, True, QStyle.SP_FileDialogListView)
        tb.addAction(self.a_list)
        tb.addAction(self._act("מסך מלא", "מסך מלא (F)", self.toggle_full, icon=QStyle.SP_TitleBarMaxButton))
        tb.addAction(self._act("עזרה", "קיצורי מקשים", self.show_help, icon=QStyle.SP_MessageBoxQuestion))

    def _build_list(self):
        self.list_panel = QWidget()
        v = QVBoxLayout(self.list_panel)
        v.setContentsMargins(8, 8, 8, 8)
        h = QHBoxLayout()
        self.filter = QComboBox()
        for text, key in (("הכול", "all"), ("תמונות בלבד", "image"), ("סרטונים בלבד", "video")):
            self.filter.addItem(text, key)
        self.sort = QComboBox()
        for text, key in (("לפי שם", "name"), ("לפי תאריך", "date"), ("לפי גודל", "size")):
            self.sort.addItem(text, key)
        self.filter.currentIndexChanged.connect(self._resort)
        self.sort.currentIndexChanged.connect(self._resort)
        h.addWidget(self.filter)
        h.addWidget(self.sort)
        v.addLayout(h)
        self.count = QLabel()
        self.count.setStyleSheet("color:#8f96a1")
        v.addWidget(self.count)
        self.list = QListWidget()
        self.list.setIconSize(QSize(56, 38))
        self.list.setUniformItemSizes(True)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setAcceptDrops(False)
        self.list.itemClicked.connect(lambda it: self.show_pos(self.list.row(it)))
        self.list.verticalScrollBar().valueChanged.connect(lambda _: self._thumb_timer.start())
        v.addWidget(self.list, 1)
        self._thumb_timer = QTimer(self, singleShot=True, interval=60)
        self._thumb_timer.timeout.connect(self._load_visible_thumbs)
        self._video_icon = self.style().standardIcon(QStyle.SP_MediaPlay)
        self._image_icon = self.style().standardIcon(QStyle.SP_FileIcon)

    def _build_controls(self):
        self.controls = QWidget()
        self.controls.setObjectName("controls")
        self.controls.setLayoutDirection(Qt.LeftToRight)   # timelines run left to right
        h = QHBoxLayout(self.controls)
        h.setContentsMargins(10, 6, 10, 6)

        def btn(icon=None, text=None, tip="", slot=None):
            b = QToolButton()
            if icon is not None:
                b.setIcon(self.style().standardIcon(icon))
            if text:
                b.setText(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            return b

        h.addWidget(btn(QStyle.SP_MediaSkipBackward, tip="הקודם (←)", slot=self.prev))
        self.play_btn = btn(QStyle.SP_MediaPlay, tip="ניגון / השהיה (רווח)", slot=self.toggle_play)
        h.addWidget(self.play_btn)
        h.addWidget(btn(QStyle.SP_MediaSkipForward, tip="הבא (→)", slot=lambda: self.next()))
        self.t_cur = QLabel("0:00")
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setFocusPolicy(Qt.NoFocus)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.t_dur = QLabel("0:00")
        h.addWidget(self.t_cur)
        h.addWidget(self.seek, 1)
        h.addWidget(self.t_dur)
        self.mute_btn = btn(QStyle.SP_MediaVolume, tip="השתקה (M)", slot=self.toggle_mute)
        h.addWidget(self.mute_btn)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(90)
        self.vol.setFocusPolicy(Qt.NoFocus)
        self.vol.valueChanged.connect(self._vol_changed)
        h.addWidget(self.vol)
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"×{s:g}", s)
        self.speed.setCurrentIndex(SPEEDS.index(1.0))
        self.speed.setToolTip("מהירות ניגון ([ ו-])")
        self.speed.setFocusPolicy(Qt.NoFocus)
        self.speed.currentIndexChanged.connect(lambda _: self.player.setPlaybackRate(self.speed.currentData()))
        h.addWidget(self.speed)
        self.video_widgets = [self.t_cur, self.seek, self.t_dur, self.mute_btn, self.vol, self.speed]
        h.addSpacing(16)
        h.addWidget(btn(text="−", tip="הקטנה (-)", slot=lambda: self.view.zoom_by(1 / 1.25)))
        self.zoom_btn = btn(text="100%", tip="חזרה לגודל החלון (0)", slot=self.view.fit)
        self.zoom_btn.setMinimumWidth(56)
        h.addWidget(self.zoom_btn)
        h.addWidget(btn(text="+", tip="הגדלה (+ או גלגלת העכבר)", slot=lambda: self.view.zoom_by(1.25)))
        h.addWidget(btn(text="1:1", tip="גודל אמיתי (1)", slot=self.view.actual_size))

    # ---------- settings ----------
    def _restore(self):
        c = self.cfg
        b = lambda k, d: str(c.value(k, d)).lower() in ("true", "1")
        self.a_auto.setChecked(b("autoNext", True))
        self.a_shuffle.setChecked(b("shuffle", False))
        self.a_loop.setChecked(b("loop", False))
        self.a_list.setChecked(b("list", True))
        self.list_panel.setVisible(self.a_list.isChecked())
        self.sec.setValue(int(c.value("slideSec", 5)))
        self.vol.setValue(int(c.value("volume", 80)))
        self.audio.setMuted(b("muted", False))
        self._mute_icon()
        self.filter.setCurrentIndex(max(0, self.filter.findData(c.value("filter", "all"))))
        self.sort.setCurrentIndex(max(0, self.sort.findData(c.value("sort", "name"))))
        geo = c.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self._toggled()

    def closeEvent(self, e):
        c = self.cfg
        c.setValue("autoNext", self.a_auto.isChecked())
        c.setValue("shuffle", self.a_shuffle.isChecked())
        c.setValue("loop", self.a_loop.isChecked())
        c.setValue("list", self.a_list.isChecked())
        c.setValue("slideSec", self.sec.value())
        c.setValue("volume", self.vol.value())
        c.setValue("muted", self.audio.isMuted())
        c.setValue("filter", self.filter.currentData())
        c.setValue("sort", self.sort.currentData())
        if not self.isFullScreen():
            c.setValue("geometry", self.saveGeometry())
        self.player.stop()
        super().closeEvent(e)

    # ---------- opening ----------
    def _file_filter(self):
        pat = lambda exts: " ".join(f"*.{e}" for e in sorted(exts))
        return (f"תמונות וסרטונים ({pat(IMAGE_EXT | VIDEO_EXT)});;תמונות ({pat(IMAGE_EXT)});;"
                f"סרטונים ({pat(VIDEO_EXT)});;כל הקבצים (*)")

    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "פתיחת קבצים", self.cfg.value("lastDir", ""), self._file_filter())
        if files:
            self.cfg.setValue("lastDir", os.path.dirname(files[0]))
            self.open_paths(files)

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "פתיחת תיקייה", self.cfg.value("lastDir", ""))
        if d:
            self.cfg.setValue("lastDir", d)
            self.open_paths([d])

    def open_paths(self, paths):
        start = None
        if len(paths) == 1 and os.path.isfile(paths[0]):
            # one file (double-click / "Open with"): show its whole folder, starting at it
            start = os.path.abspath(paths[0])
            paths = [os.path.dirname(start)]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            files = collect_files(paths)
        finally:
            QApplication.restoreOverrideCursor()
        if start and os.path.normcase(start) not in {os.path.normcase(f) for f in files}:
            files.insert(0, start)
        if not files:
            QMessageBox.information(self, "צופה מדיה", "לא נמצאו תמונות או סרטונים במה שנבחר.")
            return
        self.pl.set_paths(files)
        self._thumbs.clear()
        self._thumb_asked.clear()
        self._fill_list()
        pos = 0
        if start:
            for p, i in enumerate(self.pl.view):
                if os.path.normcase(self.pl.items[i].path) == os.path.normcase(start):
                    pos = p
                    break
        self.slide_paused = False
        self.show_pos(pos)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_paths(paths)

    # ---------- list ----------
    def _fill_list(self):
        self.list.clear()
        for i in self.pl.view:
            it = self.pl.items[i]
            li = QListWidgetItem(self._thumbs.get(it.path) or (self._video_icon if it.kind == "video" else self._image_icon),
                                 self.pl.label(it))
            li.setToolTip(f"{it.path}\n{fmt_size(it.size)}")
            if it.bad:
                li.setForeground(QBrush(QColor("#ef6f5e")))
            self.list.addItem(li)
        imgs, vids = self.pl.counts()
        self.count.setText(f"{imgs} תמונות · {vids} סרטונים")
        self._thumb_timer.start()

    def _resort(self):
        self.pl.filter = self.filter.currentData()
        self.pl.sort = self.sort.currentData()
        if not self.pl.items:
            return
        before = self.pl.current()
        self.pl.rebuild()
        self._fill_list()
        if self.pl.current() is not before:
            self.show_pos(self.pl.pos)
        else:
            self._select_row()

    def _select_row(self):
        self.list.blockSignals(True)
        self.list.setCurrentRow(self.pl.pos)
        self.list.blockSignals(False)
        li = self.list.item(self.pl.pos)
        if li:
            self.list.scrollToItem(li)

    def _load_visible_thumbs(self):
        if not self.list.count():
            return
        vp = self.list.viewport().rect()
        first = self.list.indexAt(vp.topLeft()).row()
        last = self.list.indexAt(vp.bottomLeft()).row()
        first = max(0, first)
        last = self.list.count() - 1 if last < 0 else last
        for row in range(first, min(last + 3, self.list.count())):
            it = self.pl.items[self.pl.view[row]]
            if it.kind == "image" and it.path not in self._thumb_asked:
                self._thumb_asked.add(it.path)
                self._pool.start(_ThumbJob(it.path, self._thumb_sig))

    def _thumb_done(self, path, img):
        if img.isNull():
            return
        icon = QIcon(QPixmap.fromImage(img))
        self._thumbs[path] = icon
        for row, i in enumerate(self.pl.view):
            if self.pl.items[i].path == path:
                li = self.list.item(row)
                if li:
                    li.setIcon(icon)
                break

    # ---------- showing ----------
    def _show_welcome(self):
        self.view.clear()
        self.view.show_message(
            "<div style='font-size:20px;color:%s'>צופה מדיה</div>"
            "<p>פתח קבצים או תיקייה מהסרגל למעלה,<br>או גרור אותם לחלון הזה.</p>"
            "<p style='color:#8f96a1'>גלגלת העכבר מגדילה. לחץ על \"עזרה\" לכל המקשים.</p>" % ACCENT)
        self._video_controls(False)

    def show_pos(self, pos, record=True):
        item = self.pl.go(pos, record)
        if item is None:
            return
        self.slide.stop()
        self.skip.stop()
        self._stop_movie()
        self._select_row()
        self.status_pos.setText(f"{self.pl.pos + 1} / {len(self.pl)}")
        self.status_name.setText(self.pl.label(item))
        self.status_info.setText(fmt_size(item.size))

        if item.kind == "video":
            self.view.show_video()
            self._video_controls(True)
            self.player.setSource(QUrl.fromLocalFile(item.path))
            self.player.setPlaybackRate(self.speed.currentData())
            self.player.play()
            return

        self.player.stop()
        self.player.setSource(QUrl())
        self._video_controls(False)
        pm, movie, err = load_image(item.path)
        if movie is not None:
            self.movie = movie
            movie.frameChanged.connect(self._movie_frame)
            movie.start()
            self.view.show_pixmap(movie.currentPixmap())
        elif pm is not None:
            self.view.show_pixmap(pm)
        else:
            self._failed(item, f"לא הצלחתי לפתוח את התמונה ({ext(item.path).upper()}).")
            return
        self._info()
        self._start_slide()

    def _movie_frame(self, _):
        if self.movie:
            self.view.update_frame(self.movie.currentPixmap())

    def _stop_movie(self):
        if self.movie:
            self.movie.stop()
            self.movie.frameChanged.disconnect(self._movie_frame)
            self.movie = None

    def _info(self):
        item = self.pl.current()
        if not item:
            return
        if item.kind == "video":
            s = self.view.video.nativeSize()
            dims = f"{int(s.width())}×{int(s.height())}" if not s.isEmpty() else ""
        else:
            r = self.view.sceneRect()
            dims = f"{int(r.width())}×{int(r.height())}"
        self.status_info.setText(" · ".join(x for x in (dims, fmt_size(item.size)) if x))

    def _failed(self, item, why):
        item.bad = True
        li = self.list.item(self.pl.pos)
        if li:
            li.setForeground(QBrush(QColor("#ef6f5e")))
        self.player.stop()
        self.view.clear()
        more = " עובר לקובץ הבא…" if self.a_auto.isChecked() and len(self.pl) > 1 else ""
        self.view.show_message(f"<b>{item.name}</b><p>{why}{more}</p>")
        if more:
            self.skip.start()

    def _video_controls(self, on):
        for w in self.video_widgets:
            w.setEnabled(on)
        if not on:
            self.t_cur.setText("0:00")
            self.t_dur.setText("0:00")
            self.seek.setValue(0)

    def next(self, auto=False):
        p = self.pl.next_pos(auto)
        if p is None:
            self.slide.stop()    # end of the list
            return
        self.show_pos(p)

    def prev(self):
        p = self.pl.prev_pos()
        if p is not None:
            self.show_pos(p, record=not self.pl.shuffle)

    # ---------- slideshow ----------
    def _start_slide(self):
        self.slide.stop()
        item = self.pl.current()
        if item and item.kind == "image" and self.a_auto.isChecked() and not self.slide_paused:
            self.slide.start(self.sec.value() * 1000)

    def _sec_changed(self):
        if self.slide.isActive():
            self._start_slide()

    def _toggled(self):
        self.pl.shuffle = self.a_shuffle.isChecked()
        self.pl.loop = self.a_loop.isChecked()
        if self.a_auto.isChecked():
            self.slide_paused = False
            self._start_slide()
        else:
            self.slide.stop()

    def _list_toggled(self):
        self.list_panel.setVisible(self.a_list.isChecked() and not self.isFullScreen())

    # ---------- video ----------
    def toggle_play(self):
        item = self.pl.current()
        if not item:
            return
        if item.kind == "image":
            if self.slide.isActive():
                self.slide.stop()
                self.slide_paused = True
                self.statusBar().showMessage("המצגת נעצרה (רווח להמשך)", 2000)
            else:
                self.slide_paused = False
                if not self.a_auto.isChecked():
                    self.a_auto.setChecked(True)
                self._start_slide()
                self.statusBar().showMessage("המצגת ממשיכה", 2000)
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _media_status(self, st):
        if st == QMediaPlayer.EndOfMedia and self.a_auto.isChecked():
            self.next(auto=True)
        elif st == QMediaPlayer.InvalidMedia:
            item = self.pl.current()
            if item and item.kind == "video" and not item.bad:
                self._failed(item, "לא הצלחתי לנגן את הסרטון. ייתכן שהקובץ פגום.")

    def _media_error(self, err, msg):
        item = self.pl.current()
        if err != QMediaPlayer.NoError and item and item.kind == "video" and not item.bad:
            self._failed(item, f"לא הצלחתי לנגן את הסרטון.<br><span style='color:#8f96a1'>{msg}</span>")

    def _position(self, ms):
        self.t_cur.setText(fmt_time(ms))
        if not self._seeking:
            self.seek.blockSignals(True)
            self.seek.setValue(ms)
            self.seek.blockSignals(False)

    def _duration(self, ms):
        self.seek.setRange(0, max(0, ms))
        self.t_dur.setText(fmt_time(ms))

    def _seek_released(self):
        self.player.setPosition(self.seek.value())
        self._seeking = False

    def _play_state(self, st):
        icon = QStyle.SP_MediaPause if st == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.play_btn.setIcon(self.style().standardIcon(icon))

    def seek_by(self, ms):
        item = self.pl.current()
        if item and item.kind == "video":
            self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + ms)))

    def _vol_changed(self, v):
        self.audio.setVolume(v / 100)
        if v and self.audio.isMuted():
            self.audio.setMuted(False)
        self._mute_icon()

    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())
        if not self.audio.isMuted() and self.vol.value() == 0:
            self.vol.setValue(50)
        self._mute_icon()

    def _mute_icon(self):
        muted = self.audio.isMuted() or self.vol.value() == 0
        self.mute_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaVolumeMuted if muted else QStyle.SP_MediaVolume))

    def step_speed(self, d):
        self.speed.setCurrentIndex(max(0, min(len(SPEEDS) - 1, self.speed.currentIndex() + d)))
        self.statusBar().showMessage(f"מהירות {self.speed.currentText()}", 1500)

    # ---------- full screen ----------
    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
            self.toolbar.show()
            self.statusBar().show()
            self.list_panel.setVisible(self.a_list.isChecked())
            self.controls.show()
            self.idle.stop()
            self.view.viewport().unsetCursor()
        else:
            self.toolbar.hide()
            self.statusBar().hide()
            self.list_panel.hide()
            self.showFullScreen()
            self.idle.start()

    def _go_idle(self):
        if self.isFullScreen():
            self.controls.hide()
            self.view.viewport().setCursor(Qt.BlankCursor)

    def _wake(self):
        if self.isFullScreen():
            if not self.controls.isVisible():
                self.controls.show()
                self.view.viewport().unsetCursor()
            self.idle.start()

    def show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("עזרה")
        box.setTextFormat(Qt.RichText)
        box.setText(HELP)
        box.exec()

    # ---------- keyboard ----------
    def eventFilter(self, obj, e):
        t = e.type()
        if t == e.Type.MouseMove:
            self._wake()
            return False
        if t != e.Type.KeyPress or not self.isActiveWindow() or QApplication.activeModalWidget() \
                or QApplication.activePopupWidget():
            return False
        fw = QApplication.focusWidget()
        if fw is not None and (fw is self.sec or self.sec.isAncestorOf(fw)):
            # typing the number of seconds: only Enter / Esc leave the box
            if e.key() not in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
                return False
            self.sec.clearFocus()
            return True
        if self._key(e):
            self._wake()
            return True
        return False

    def _key(self, e):
        k, mod = e.key(), e.modifiers()
        shift, ctrl = bool(mod & Qt.ShiftModifier), bool(mod & Qt.ControlModifier)
        ch = _letter(e)
        vk = e.nativeVirtualKey() if sys.platform == "win32" else 0
        if shift and k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            step = 80
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0)
            self.view.pan(dx, dy)
        elif ctrl and k == Qt.Key_Right:
            self.seek_by(5000)
        elif ctrl and k == Qt.Key_Left:
            self.seek_by(-5000)
        elif ctrl:
            return False
        elif k in (Qt.Key_Right, Qt.Key_PageDown):
            self.next()
        elif k in (Qt.Key_Left, Qt.Key_PageUp):
            self.prev()
        elif k == Qt.Key_Home:
            self.show_pos(0)
        elif k == Qt.Key_End:
            self.show_pos(len(self.pl) - 1)
        elif k == Qt.Key_Space or ch == "K":
            self.toggle_play()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.view.zoom_by(1.25)
        elif k in (Qt.Key_Minus, Qt.Key_Underscore):
            self.view.zoom_by(1 / 1.25)
        elif k == Qt.Key_0:
            self.view.fit()
        elif k == Qt.Key_1:
            self.view.actual_size()
        elif k == Qt.Key_Up:
            self.vol.setValue(self.vol.value() + 5)
        elif k == Qt.Key_Down:
            self.vol.setValue(self.vol.value() - 5)
        elif k == Qt.Key_BracketLeft or vk == 0xDB:
            self.step_speed(-1)
        elif k == Qt.Key_BracketRight or vk == 0xDD:
            self.step_speed(1)
        elif k == Qt.Key_F11 or ch == "F":
            self.toggle_full()
        elif k == Qt.Key_Escape:
            if not self.isFullScreen():
                return False
            self.toggle_full()
        elif ch == "J":
            self.seek_by(-10000)
        elif ch == "L":
            self.seek_by(10000)
        elif ch == "M":
            self.toggle_mute()
        elif ch == "A":
            self.a_auto.trigger()
        elif ch == "S":
            self.a_shuffle.trigger()
        elif ch == "R":
            self.a_loop.trigger()
        elif ch == "P":
            self.a_list.trigger()
        elif ch == "O":
            self.open_files()
        elif k == Qt.Key_F1 or k == Qt.Key_Question:
            self.show_help()
        else:
            return False
        return True


def _dark_palette(app):
    app.setStyle("Fusion")
    p = QPalette()
    bg, panel, raise_, fg, muted = "#0e1013", "#171a1f", "#222730", "#e8e6e1", "#8f96a1"
    for role, c in ((QPalette.Window, panel), (QPalette.WindowText, fg), (QPalette.Base, panel),
                    (QPalette.AlternateBase, raise_), (QPalette.Text, fg), (QPalette.Button, raise_),
                    (QPalette.ButtonText, fg), (QPalette.ToolTipBase, raise_), (QPalette.ToolTipText, fg),
                    (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#1a1405"),
                    (QPalette.PlaceholderText, muted), (QPalette.Link, ACCENT)):
        p.setColor(role, QColor(c))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(muted))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(muted))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(muted))
    app.setPalette(p)
    app.setStyleSheet(
        f"QToolBar {{ border: 0; border-bottom: 1px solid #2c323c; padding: 4px; spacing: 4px; }}"
        f"QToolButton:checked {{ color: {ACCENT}; background: #222730; border: 1px solid {ACCENT}; border-radius: 4px; }}"
        f"QWidget#controls {{ background: #171a1f; border-top: 1px solid #2c323c; }}"
        f"QListWidget {{ border: 0; }} QListWidget::item {{ padding: 4px; }}"
        f"QListWidget::item:selected {{ background: #222730; color: {ACCENT}; }}"
        f"QSlider::groove:horizontal {{ height: 4px; background: #2c323c; border-radius: 2px; }}"
        f"QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}"
        f"QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; margin: -5px 0; border-radius: 6px; }}"
        f"QSplitter::handle {{ background: #2c323c; width: 1px; }}"
    )


def _selftest(win, paths, out):
    """Used by the build: open the given files and check that each one shows."""
    lines, ok = [], True
    win.open_paths(paths)
    for pos in range(len(win.pl)):
        win.show_pos(pos)
        item = win.pl.current()
        if item.kind == "video":
            loop = QEventLoop()
            got = []
            sink = win.view.video.videoSink()

            def frame(f):
                if f.isValid():
                    got.append(1)
                    loop.quit()
            sink.videoFrameChanged.connect(frame)
            QTimer.singleShot(15000, loop.quit)
            loop.exec()
            sink.videoFrameChanged.disconnect(frame)
            good = bool(got) and not item.bad
        else:
            good = win.view.mode == "image" and not win.view.pix.pixmap().isNull()
        if good:
            before = win.view.real_scale()
            win.view.zoom_by(3)
            good = win.view.real_scale() > before * 2
            win.view.fit()
        lines.append(f"{'OK  ' if good else 'FAIL'} {item.name}")
        ok &= good
    if not len(win.pl):
        lines.append("FAIL nothing opened")
        ok = False
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    QApplication.instance().exit(0 if ok else 1)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    QImageReader.setAllocationLimit(0)    # allow very large photos
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Media Viewer")
    app.setLayoutDirection(Qt.RightToLeft)
    _dark_palette(app)
    win = MainWindow()
    win.show()
    if "--selftest" in argv:
        i = argv.index("--selftest")
        out, paths = argv[i + 1], argv[i + 2:]
        QTimer.singleShot(300, lambda: _selftest(win, paths, out))
    elif argv:
        QTimer.singleShot(0, lambda: win.open_paths(argv))
    return app.exec()
