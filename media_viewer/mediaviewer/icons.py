"""Light icons drawn in code, so they show up on the dark background
(Windows' built-in media icons are black)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

FG = "#e8e6e1"
DISABLED = "#5b616b"
SIZE = 64


def _shape(name):
    p = QPainterPath()
    if name == "play":
        p.moveTo(20, 12); p.lineTo(52, 32); p.lineTo(20, 52); p.closeSubpath()
    elif name == "pause":
        p.addRoundedRect(QRectF(16, 12, 11, 40), 2, 2)
        p.addRoundedRect(QRectF(37, 12, 11, 40), 2, 2)
    elif name == "next":
        p.moveTo(12, 12); p.lineTo(40, 32); p.lineTo(12, 52); p.closeSubpath()
        p.addRoundedRect(QRectF(42, 12, 8, 40), 2, 2)
    elif name == "prev":
        p.moveTo(52, 12); p.lineTo(24, 32); p.lineTo(52, 52); p.closeSubpath()
        p.addRoundedRect(QRectF(14, 12, 8, 40), 2, 2)
    elif name in ("volume", "muted"):
        p.moveTo(8, 24); p.lineTo(20, 24); p.lineTo(34, 12); p.lineTo(34, 52); p.lineTo(20, 40)
        p.lineTo(8, 40); p.closeSubpath()
    elif name == "video":
        p.addRoundedRect(QRectF(6, 14, 52, 36), 6, 6)
    return p


def _paint(name, color):
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.transparent)
    g = QPainter(pm)
    g.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    g.fillPath(_shape(name), c)
    pen = QPen(c, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    g.setPen(pen)
    if name == "volume":
        g.drawArc(QRectF(30, 20, 16, 24), -60 * 16, 120 * 16)
        g.drawArc(QRectF(28, 10, 28, 44), -60 * 16, 120 * 16)
    elif name == "muted":
        g.drawLine(QPointF(42, 24), QPointF(56, 40))
        g.drawLine(QPointF(56, 24), QPointF(42, 40))
    elif name == "full":
        for x, y, dx, dy in ((10, 10, 1, 1), (54, 10, -1, 1), (10, 54, 1, -1), (54, 54, -1, -1)):
            g.drawPolyline([QPointF(x, y + 14 * dy), QPointF(x, y), QPointF(x + 14 * dx, y)])
    elif name == "video":
        play = QPainterPath()
        play.moveTo(26, 22); play.lineTo(42, 32); play.lineTo(26, 42); play.closeSubpath()
        g.fillPath(play, QColor("#0e1013"))
    g.end()
    return pm


def icon(name, color=FG):
    ic = QIcon()
    ic.addPixmap(_paint(name, color), QIcon.Normal)
    ic.addPixmap(_paint(name, DISABLED), QIcon.Disabled)
    return ic
