"""Shared GUI plumbing: background device access and small widgets."""

import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QToolButton, QWidget)

from .. import client
from .. import protocol as p
from ..device import Nollie16

RGB = tuple[int, int, int]


class Device(QObject):
    """Runs device calls on one background thread (so they never interleave or block the UI) and
    delivers results on the UI thread. Connects lazily and reconnects after errors."""

    status = Signal(str)                       # human-readable connection status
    _done = Signal(object, object, object, object)     # ok callback, error callback, value, error

    def __init__(self):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nollie")
        self._dev: Nollie16 | None = None
        self.via_daemon = False
        self._done.connect(self._deliver)

    def _get(self) -> Nollie16:
        if self._dev is None:
            self._dev = client.connect()
            self.via_daemon = isinstance(self._dev.transport, client.DaemonTransport)
            self.status.emit(f"Connected to {self._dev.serial}" + (" via the OpenNollie daemon" if self.via_daemon else " over USB"))
        return self._dev

    def dev_sync(self) -> Nollie16:
        """For code already running on the device thread."""
        return self._get()

    def run(self, fn: Callable[[Nollie16], object], ok: Callable | None = None, err: Callable | None = None) -> None:
        def job():
            try:
                return fn(self._get()), None
            except Exception as e:                    # report to the UI, reconnect next time
                traceback.print_exc()
                if isinstance(e, OSError | client.DaemonError) or "not found" in str(e):
                    self._drop()
                return None, e
        self._pool.submit(job).add_done_callback(lambda f: self._done.emit(ok, err, *f.result()))

    def _deliver(self, ok, err, value, error) -> None:
        if error is not None:
            if err:
                err(error)
            else:
                self.status.emit(f"Error: {error}")
        elif ok:
            ok(value)

    def _drop(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
            self.status.emit("Disconnected (will reconnect on the next action)")

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def hexcolor(c: RGB) -> str:
    return "#%02x%02x%02x" % tuple(c)


class ColorButton(QPushButton):
    """A swatch that opens a color picker."""

    changed = Signal(tuple)

    def __init__(self, color: RGB = (255, 255, 255), parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("colorSwatch")
        self.clicked.connect(self._pick)
        self.set_color(color)

    def set_color(self, color: RGB) -> None:
        self._color = tuple(int(x) for x in color)
        border = "#888" if sum(self._color) > 600 else "#555"
        # Scoped to this button by ID: a bare declaration would cascade into child widgets, including the
        # color dialog, painting all its fields in the swatch color.
        self.setStyleSheet(
            f"QPushButton#colorSwatch {{ background: {hexcolor(self._color)}; border: 1px solid {border};"
            f" border-radius: 4px; }}"
            f"QPushButton#colorSwatch:hover {{ border: 2px solid palette(highlight); }}")
        self.setToolTip(hexcolor(self._color))

    def color(self) -> RGB:
        return self._color

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(*self._color), self.window(), "Pick a color")
        if c.isValid():
            self.set_color((c.red(), c.green(), c.blue()))
            self.changed.emit(self._color)


class PortPicker(QWidget):
    """Toggle buttons for ports 1-16. Ports listed in `disabled` (used elsewhere) can't be picked.
    Remembers the order ports were picked in (effects travel in that order)."""

    changed = Signal(list)

    def __init__(self, labels: dict[int, str] | None = None, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet("QToolButton:checked { background: palette(highlight); color: palette(highlighted-text);"
                           " border: 1px solid palette(highlight); border-radius: 3px; }")
        self._order: list[int] = []
        self.buttons: dict[int, QToolButton] = {}
        for port in range(1, p.NUM_CHANNELS + 1):
            b = QToolButton()
            b.setText(str(port))
            b.setCheckable(True)
            b.setFixedSize(30, 26)
            b.toggled.connect(lambda on, pt=port: self._toggled(pt, on))
            grid.addWidget(b, (port - 1) // 8, (port - 1) % 8)
            self.buttons[port] = b
        self.set_labels(labels or {})

    def set_labels(self, labels: dict[int, str]) -> None:
        for port, b in self.buttons.items():
            b.setToolTip(f"Port {port}" + (f": {labels[port]}" if labels.get(port) else ""))

    def _toggled(self, port: int, on: bool) -> None:
        if on and port not in self._order:
            self._order.append(port)
        elif not on and port in self._order:
            self._order.remove(port)
        self.changed.emit(list(self._order))

    def ports(self) -> list[int]:
        return list(self._order)

    def set_ports(self, ports: list[int], disabled: set[int] = frozenset()) -> None:
        self._order = []
        for port, b in self.buttons.items():
            b.blockSignals(True)
            b.setChecked(port in ports)
            b.setEnabled(port in ports or port not in disabled)
            b.blockSignals(False)
        self._order = [pt for pt in ports if pt in self.buttons]


def info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def error(parent, title: str, exc: Exception | str) -> None:
    QMessageBox.warning(parent, title, str(exc))


def confirm(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.Yes


def hbox(*widgets, stretch: bool = True) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if isinstance(x, str):
            x = QLabel(x)
        lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w
