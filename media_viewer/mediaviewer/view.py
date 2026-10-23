"""The picture area: shows a photo or a video and zooms/pans both the same way."""
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QTransform
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QLabel

MAX_SCALE = 40.0   # 4000% of real pixels


class MediaView(QGraphicsView):
    zoomChanged = Signal(int)   # percent of real size
    clicked = Signal()          # single click (not a drag, not a double click)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.video = QGraphicsVideoItem()
        self.video.setAspectRatioMode(Qt.KeepAspectRatio)
        self.scene().addItem(self.pix)
        self.scene().addItem(self.video)
        self.video.nativeSizeChanged.connect(self._native_size)

        self.mode = None
        self.base = 1.0    # scale that fits the picture in the window
        self.zoom = 1.0    # extra zoom on top of that, 1 = fitted

        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor("#0e1013"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignCenter)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setMouseTracking(True)

        self.message = QLabel(self)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setStyleSheet(
            "background:#171a1f;border:1px solid #2c323c;border-radius:10px;padding:18px;font-size:15px;color:#e8e6e1")
        self.message.hide()

        self._press = None
        self._click = QTimer(self, singleShot=True, interval=230)
        self._click.timeout.connect(self.clicked)

    # ---------- content ----------
    def show_pixmap(self, pm):
        self.mode = "image"
        self.video.hide()
        self.pix.setPixmap(pm)
        self.pix.show()
        self.setSceneRect(self.pix.boundingRect())
        self.hide_message()
        self.fit()

    def update_frame(self, pm):
        """Next frame of an animated GIF: same size, keep the zoom."""
        self.pix.setPixmap(pm)

    def show_video(self):
        self.mode = "video"
        self.pix.hide()
        self.pix.setPixmap(QPixmap())
        self.video.show()
        self.hide_message()
        size = self.video.nativeSize()
        if not size.isEmpty():
            self._native_size(size)

    def _native_size(self, size):
        if size.isEmpty():
            return
        self.video.setSize(size)
        if self.mode == "video":
            self.setSceneRect(QRectF(QPointF(0, 0), size))
            self.fit()

    def clear(self):
        self.mode = None
        self.pix.hide()
        self.pix.setPixmap(QPixmap())
        self.video.hide()

    def show_message(self, text):
        self.message.setText(text)
        self.message.setMaximumWidth(max(260, min(520, self.width() - 40)))
        self.message.adjustSize()
        self._place_message()
        self.message.show()

    def hide_message(self):
        self.message.hide()

    def _place_message(self):
        m = self.message
        m.move((self.width() - m.width()) // 2, (self.height() - m.height()) // 2)

    # ---------- zoom ----------
    def real_scale(self):
        return self.base * self.zoom

    def fit(self):
        r = self.sceneRect()
        vp = self.viewport()
        if r.isEmpty() or vp.width() < 2:
            return
        s = min(vp.width() / r.width(), vp.height() / r.height())
        if self.mode == "image":
            s = min(s, 1.0)    # small photos stay at their real size
        self.base, self.zoom = s, 1.0
        self.setTransform(QTransform.fromScale(s, s))
        self.centerOn(r.center())
        self._after_zoom()

    def zoom_to(self, z, under_mouse=False):
        if self.sceneRect().isEmpty():
            return
        z = max(1.0, min(max(1.0, MAX_SCALE / self.base), z))
        if z <= 1.0001:
            self.fit()
            return
        self.setTransformationAnchor(
            QGraphicsView.AnchorUnderMouse if under_mouse else QGraphicsView.AnchorViewCenter)
        f = z / self.zoom
        self.scale(f, f)
        self.zoom = z
        self._after_zoom()

    def zoom_by(self, factor, under_mouse=False):
        self.zoom_to(self.zoom * factor, under_mouse)

    def actual_size(self):
        self.zoom_to(1.0 / self.base if self.base else 1.0)

    def pan(self, dx, dy):
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + dx)
        v.setValue(v.value() + dy)

    def _after_zoom(self):
        self.setDragMode(QGraphicsView.ScrollHandDrag if self.zoom > 1.001 else QGraphicsView.NoDrag)
        self.zoomChanged.emit(round(self.real_scale() * 100))

    # ---------- events ----------
    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if d:
            self.zoom_by(1.0015 ** d, under_mouse=True)
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if e.button() == Qt.LeftButton and self._press is not None:
            moved = (e.position() - self._press).manhattanLength()
            self._press = None
            if moved < 5:
                self._click.start()

    def mouseDoubleClickEvent(self, e):
        self._click.stop()
        if e.button() != Qt.LeftButton:
            return
        if self.zoom > 1.001:
            self.fit()
        else:
            self.zoom_to(2.5, under_mouse=True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.zoom <= 1.001:
            self.fit()
        if self.message.isVisible():
            self._place_message()
