"""A schematic of the PC case showing every port's LEDs in their current colors."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

RGB = tuple[int, int, int]

# Where each port's device sits (side view through the glass), from CLAUDE.md's port map.
# kind: "fan" = ring of LEDs, "bar" = straight strip. x, y = center as a fraction of the view.
CASE_LAYOUT: dict[int, tuple[str, float, float, str]] = {
    5: ("fan", 0.34, 0.12, "Top"),
    4: ("fan", 0.09, 0.34, "Rear"),
    12: ("fan", 0.33, 0.36, "CPU rear"),
    13: ("fan", 0.50, 0.36, "CPU front"),
    9: ("bar", 0.42, 0.60, "GPU"),
    16: ("fan", 0.86, 0.20, "Side top"),
    14: ("fan", 0.86, 0.47, "Side mid"),
    15: ("fan", 0.86, 0.74, "Side bottom"),
    6: ("fan", 0.24, 0.87, "Bottom rear"),
    7: ("fan", 0.44, 0.87, "Bottom mid"),
    8: ("fan", 0.64, 0.87, "Bottom front"),
}


class CaseView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame: dict[int, list[RGB]] = {}
        self.labels: dict[int, str] = {}
        self.setMinimumSize(420, 300)

    def set_frame(self, frame: dict[int, list[RGB]]) -> None:
        """port -> LED colors."""
        self.frame = frame
        self.update()

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(6, 6, -6, -6)
        qp.setPen(QPen(QColor(90, 90, 100), 2))
        qp.setBrush(QColor(18, 18, 22))
        qp.drawRoundedRect(r, 12, 12)
        unit = min(r.width(), r.height())
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, unit / 55))
        qp.setFont(font)

        layout = dict(CASE_LAYOUT)
        extra = [pt for pt in sorted(self.frame) if pt not in layout]
        for i, pt in enumerate(extra):            # ports not in the case layout: a row at the bottom
            layout[pt] = ("fan", 0.08 + 0.12 * i, 0.97, f"Port {pt}")

        for port, (kind, fx, fy, label) in layout.items():
            colors = self.frame.get(port) or []
            c = QPointF(r.x() + fx * r.width(), r.y() + fy * r.height())
            if kind == "fan":
                self._fan(qp, c, unit * 0.075, colors)
                text_y = c.y() + unit * 0.075 + 4
            else:
                self._bar(qp, c, r.width() * 0.46, unit * 0.045, colors)
                text_y = c.y() + unit * 0.03 + 4
            qp.setPen(QColor(170, 170, 180))
            name = self.labels.get(port) or label
            width = unit * 0.2 if kind == "fan" else r.width() * 0.4
            text = qp.fontMetrics().elidedText(f"{port} · {name}", Qt.ElideRight, int(width))
            qp.drawText(QRectF(c.x() - width / 2, text_y, width, 18), Qt.AlignHCenter | Qt.AlignTop, text)

    @staticmethod
    def _fan(qp: QPainter, c: QPointF, radius: float, colors: list[RGB]) -> None:
        qp.setPen(QPen(QColor(60, 60, 70), 1.5))
        qp.setBrush(QColor(30, 30, 36))
        qp.drawEllipse(c, radius, radius)
        n = len(colors) or 8
        dot = max(2.0, min(radius * 0.15, radius * 2.0 / n))
        for i in range(n):
            a = -math.pi / 2 + 2 * math.pi * i / n
            p = QPointF(c.x() + math.cos(a) * radius * 0.72, c.y() + math.sin(a) * radius * 0.72)
            col = QColor(*colors[i]) if colors else QColor(45, 45, 52)
            if colors:                                  # soft glow
                glow = QColor(col)
                glow.setAlpha(70)
                qp.setPen(Qt.NoPen)
                qp.setBrush(glow)
                qp.drawEllipse(p, dot * 1.6, dot * 1.6)
            qp.setPen(Qt.NoPen)
            qp.setBrush(QBrush(col))
            qp.drawEllipse(p, dot, dot)

    @staticmethod
    def _bar(qp: QPainter, c: QPointF, width: float, height: float, colors: list[RGB]) -> None:
        rect = QRectF(c.x() - width / 2, c.y() - height / 2, width, height)
        qp.setPen(QPen(QColor(60, 60, 70), 1.5))
        qp.setBrush(QColor(30, 30, 36))
        qp.drawRoundedRect(rect, 6, 6)
        n = len(colors) or 6
        seg = (width - 12) / n
        for i in range(n):
            col = QColor(*colors[i]) if colors else QColor(45, 45, 52)
            qp.setPen(Qt.NoPen)
            qp.setBrush(col)
            qp.drawRoundedRect(QRectF(rect.x() + 6 + i * seg + 1, rect.y() + height * 0.3, seg - 2, height * 0.4), 3, 3)
