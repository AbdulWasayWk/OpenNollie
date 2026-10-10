"""A picture of the PC case: every fan / LED strip from the case layout, in its current colors.
In edit mode items can be selected and dragged (the Layout page)."""

import colorsys
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget

from ..caselayout import CaseLayout, Item

RGB = tuple[int, int, int]
FAN_RADIUS = 0.075          # × the shorter side of the picture, at size 1
STRIP_LENGTH = 0.30


class CaseView(QWidget):
    selected = Signal(int)            # index of the clicked item (-1: none)
    moved = Signal(int)               # an item was dragged (index)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = CaseLayout()
        self.frame: dict[int, list[RGB]] = {}       # port -> colors of the LEDs as wired
        self.labels: dict[int, str] = {}             # port -> name
        self.led_counts: dict[int, int] = {}
        self.editing = False
        self.current = -1
        self.show_order = False                      # editor: color LEDs by position (white = first)
        self._drag: tuple[int, QPointF] | None = None
        self.setMinimumSize(420, 300)

    # --- data ---------------------------------------------------------------------------------

    def set_layout(self, layout: CaseLayout) -> None:
        self.layout_ = layout
        self.current = min(self.current, len(layout.items) - 1)
        self.update()

    def set_frame(self, frame: dict[int, list[RGB]]) -> None:
        """port -> LED colors (as wired)."""
        self.frame = frame
        self.update()

    def _port_leds(self, port: int) -> int:
        return len(self.frame.get(port) or []) or self.led_counts.get(port) or 8

    def _item_colors(self, i: int, it: Item) -> list[RGB]:
        n = it.leds(self._port_leds(it.port))
        if self.show_order:
            hue = (it.port * 0.13) % 1.0
            cols = [tuple(round(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.85, 0.35 + 0.65 * k / max(1, n - 1)))
                    for k in range(n)]
            if cols:
                cols[0] = (255, 255, 255)
            return cols
        wire = self.frame.get(it.port)
        return it.colors(wire, self._port_leds(it.port)) if wire else [(45, 45, 52)] * n

    # --- geometry -----------------------------------------------------------------------------

    def _area(self) -> QRectF:
        return QRectF(self.rect()).adjusted(6, 6, -6, -6)

    def _center(self, it: Item) -> QPointF:
        r = self._area()
        return QPointF(r.x() + it.x * r.width(), r.y() + it.y * r.height())

    def _unit(self) -> float:
        r = self._area()
        return min(r.width(), r.height())

    def _hit(self, pos: QPointF) -> int:
        unit = self._unit()
        for i in range(len(self.layout_.items) - 1, -1, -1):
            it = self.layout_.items[i]
            c = self._center(it)
            d = pos - c
            if it.kind == "fan":
                if math.hypot(d.x(), d.y()) <= unit * FAN_RADIUS * it.size * 1.15:
                    return i
            else:
                a = math.radians(-it.angle)
                lx = d.x() * math.cos(a) - d.y() * math.sin(a)
                ly = d.x() * math.sin(a) + d.y() * math.cos(a)
                if abs(lx) <= unit * STRIP_LENGTH * it.size / 2 + 6 and abs(ly) <= unit * 0.04 + 6:
                    return i
        return -1

    # --- editing ------------------------------------------------------------------------------

    def mousePressEvent(self, e):
        if not self.editing or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        i = self._hit(e.position())
        self.current = i
        self.selected.emit(i)
        if i >= 0:
            self._drag = (i, e.position() - self._center(self.layout_.items[i]))
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag:
            i, offset = self._drag
            r = self._area()
            p = e.position() - offset
            it = self.layout_.items[i]
            it.x = min(1.0, max(0.0, round((p.x() - r.x()) / r.width(), 3)))
            it.y = min(1.0, max(0.0, round((p.y() - r.y()) / r.height(), 3)))
            self.moved.emit(i)
            self.update()
        elif self.editing:
            self.setCursor(Qt.OpenHandCursor if self._hit(e.position()) >= 0 else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        self._drag = None

    # --- drawing ------------------------------------------------------------------------------

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        r = self._area()
        qp.setPen(QPen(QColor(90, 90, 100), 2))
        qp.setBrush(QColor(18, 18, 22))
        qp.drawRoundedRect(r, 12, 12)
        unit = self._unit()
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, unit / 55))
        qp.setFont(font)
        if not self.layout_.items:
            qp.setPen(QColor(150, 150, 160))
            qp.drawText(r, Qt.AlignCenter, "No fans or strips yet: add them on the Layout page.")
            return
        for i, it in enumerate(self.layout_.items):
            c = self._center(it)
            colors = self._item_colors(i, it)
            if it.kind == "fan":
                radius = unit * FAN_RADIUS * it.size
                self._fan(qp, c, radius, colors)
                text_y, width = c.y() + radius + 4, max(radius * 2.6, unit * 0.2)
            else:
                length = unit * STRIP_LENGTH * it.size
                self._strip(qp, c, length, unit * 0.045, it.angle, colors)
                drop = abs(math.sin(math.radians(it.angle))) * length / 2 + unit * 0.03   # below the tilted strip
                text_y, width = c.y() + drop + 4, max(length * 0.8, unit * 0.2)
            if self.editing and i == self.current:
                self._selection(qp, c, it, unit)
            qp.setPen(QColor(170, 170, 180))
            name = it.label or self.labels.get(it.port) or f"Port {it.port}"
            span = f" · LEDs {it.first}–{it.first + len(colors) - 1}" if self.editing and it.first > 1 else ""
            fm = qp.fontMetrics()
            text = fm.elidedText(f"{it.port} · {name}{span}", Qt.ElideRight, int(width))
            qp.drawText(QRectF(c.x() - width / 2, text_y, width, fm.height() + 4), Qt.AlignHCenter | Qt.AlignTop, text)

    @staticmethod
    def _selection(qp: QPainter, c: QPointF, it: Item, unit: float) -> None:
        qp.save()
        qp.setPen(QPen(QColor(80, 160, 255), 2, Qt.DashLine))
        qp.setBrush(Qt.NoBrush)
        if it.kind == "fan":
            r = unit * FAN_RADIUS * it.size + 6
            qp.drawEllipse(c, r, r)
        else:
            qp.setTransform(QTransform().translate(c.x(), c.y()).rotate(it.angle), True)
            half = unit * STRIP_LENGTH * it.size / 2 + 6
            qp.drawRoundedRect(QRectF(-half, -unit * 0.0225 - 6, half * 2, unit * 0.045 + 12), 8, 8)
        qp.restore()

    @staticmethod
    def _fan(qp: QPainter, c: QPointF, radius: float, colors: list[RGB]) -> None:
        qp.setPen(QPen(QColor(60, 60, 70), 1.5))
        qp.setBrush(QColor(30, 30, 36))
        qp.drawEllipse(c, radius, radius)
        n = len(colors) or 8
        dot = max(2.0, min(radius * 0.15, radius * 2.0 / n))
        for i in range(n):
            a = -math.pi / 2 + 2 * math.pi * i / n
            pt = QPointF(c.x() + math.cos(a) * radius * 0.72, c.y() + math.sin(a) * radius * 0.72)
            col = QColor(*colors[i]) if colors else QColor(45, 45, 52)
            if colors:
                glow = QColor(col)
                glow.setAlpha(70)
                qp.setPen(Qt.NoPen)
                qp.setBrush(glow)
                qp.drawEllipse(pt, dot * 1.6, dot * 1.6)
            qp.setPen(Qt.NoPen)
            qp.setBrush(QBrush(col))
            qp.drawEllipse(pt, dot, dot)

    @staticmethod
    def _strip(qp: QPainter, c: QPointF, length: float, height: float, angle: float, colors: list[RGB]) -> None:
        qp.save()
        qp.setTransform(QTransform().translate(c.x(), c.y()).rotate(angle), True)
        rect = QRectF(-length / 2, -height / 2, length, height)
        qp.setPen(QPen(QColor(60, 60, 70), 1.5))
        qp.setBrush(QColor(30, 30, 36))
        qp.drawRoundedRect(rect, 6, 6)
        n = len(colors) or 6
        seg = (length - 12) / n
        for i in range(n):
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(*colors[i]) if colors else QColor(45, 45, 52))
            qp.drawRoundedRect(QRectF(rect.x() + 6 + i * seg + 1, -height * 0.2, max(1.0, seg - 2), height * 0.4), 3, 3)
        qp.restore()
