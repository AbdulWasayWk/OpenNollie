"""Ports & LEDs page: names, LED counts, identify, LED-count test."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QVBoxLayout, QWidget)

from .. import client
from .. import protocol as p
from .caseview import CASE_LAYOUT
from .common import Device, error

RGB = tuple[int, int, int]


class LiveSession:
    """Drive LEDs from the GUI: pauses the daemon's live effect meanwhile, re-sends the frame so the
    device doesn't fall back (it does ~3 s after the last frame)."""

    def __init__(self, device: Device, interval_ms: int = 1000):
        self.device = device
        self.frame: dict[int, list[RGB]] = {}
        self.timer = QTimer()
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._send)
        self._paused_daemon = False

    def start(self) -> None:
        def begin(d):
            if isinstance(d.transport, client.DaemonTransport):
                d.transport.call("live_pause")
                self._paused_daemon = True
        self.device.run(begin)
        self.timer.start()

    def show(self, port_colors: dict[int, list[RGB]]) -> None:
        """port -> colors; every other port is blanked."""
        self.frame = {ch: [] for ch in range(p.NUM_CHANNELS)}
        for port, colors in port_colors.items():
            self.frame[p.PORT_TO_CH[port - 1]] = colors
        self._send()

    def _send(self) -> None:
        frame = dict(self.frame)
        self.device.run(lambda d: d.send_frame(frame), err=lambda e: None)

    def stop(self) -> None:
        self.timer.stop()
        def end(d):
            if self._paused_daemon and isinstance(d.transport, client.DaemonTransport):
                d.transport.call("live_resume")
        self.device.run(end)


class LedTestDialog(QDialog):
    """Find a port's LED count: LEDs 1..N-1 green, N blue, N+1 red; or a running light."""

    stored = Signal(int)

    def __init__(self, device: Device, port: int, start: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"LED test: port {port}")
        self.port = port
        self.live = LiveSession(device, 120)
        self.step = 0
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Green = LEDs 1…N−1, <b style='color:#3080ff'>blue</b> = LED N, <b style='color:#e03030'>red</b> = LED N+1.<br>"
            "Raise N until the <b>blue</b> LED is lit and <b>no red</b> one is: then N is the LED count.<br>"
            "<i>Devices with their own controller (e.g. a GPU in ARGB-sync mode) stretch the values over their LEDs;"
            " then N is the largest value that still shows blue.</i>"))
        self.n = QSpinBox()
        self.n.setRange(1, p.LEDS_PER_CHANNEL - 1)
        self.n.setValue(start)
        self.n.valueChanged.connect(self._draw)
        self.chase = QCheckBox("Running light (shows the order around the ring)")
        self.chase.toggled.connect(self._draw)
        row = QHBoxLayout()
        row.addWidget(QLabel("N ="))
        row.addWidget(self.n)
        row.addWidget(self.chase)
        row.addStretch(1)
        lay.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton("Store N as LED count", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Close)
        buttons.accepted.connect(self._store)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.anim = QTimer(self)
        self.anim.setInterval(120)
        self.anim.timeout.connect(self._tick)
        self.live.start()
        self._draw()

    def _draw(self) -> None:
        n = self.n.value()
        if self.chase.isChecked():
            self.anim.start()
            return
        self.anim.stop()
        self.live.show({self.port: [(0, 60, 0)] * (n - 1) + [(0, 0, 255), (255, 0, 0)]})

    def _tick(self) -> None:
        n = self.n.value()
        frame = [(0, 0, 0)] * n
        for k in range(3):
            frame[(self.step - k) % n] = (255 >> k,) * 3
        self.step += 1
        self.live.show({self.port: frame})

    def _store(self) -> None:
        self.stored.emit(self.n.value())
        self.accept()

    def done(self, r):
        self.anim.stop()
        self.live.stop()
        super().done(r)


class PortsPage(QWidget):
    title = "Ports && LEDs"
    icon = "preferences-desktop-display"
    labels_changed = Signal(dict)

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Names and LED counts are stored on the controller. Built-in effects and profiles use the "
                             "LED counts; the names label ports everywhere in this app."))
        self.table = QTableWidget(p.NUM_CHANNELS, 5)
        self.table.setHorizontalHeaderLabels(["Port", "Name", "LEDs", "", ""])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.names: dict[int, QLineEdit] = {}
        self.counts: dict[int, QSpinBox] = {}
        for row, port in enumerate(range(1, p.NUM_CHANNELS + 1)):
            item = QLabel(f"  {port}")
            self.table.setCellWidget(row, 0, item)
            self.names[port] = QLineEdit()
            self.names[port].setMaxLength(p.NAME_MAX_CHARS)
            self.names[port].setPlaceholderText(CASE_LAYOUT.get(port, ("", 0, 0, "not connected?"))[3])
            self.table.setCellWidget(row, 1, self.names[port])
            self.counts[port] = QSpinBox()
            self.counts[port].setRange(0, p.LEDS_PER_CHANNEL)
            self.table.setCellWidget(row, 2, self.counts[port])
            ident = QPushButton("Identify")
            ident.clicked.connect(lambda _=False, pt=port: self._identify(pt))
            self.table.setCellWidget(row, 3, ident)
            test = QPushButton("LED test…")
            test.clicked.connect(lambda _=False, pt=port: self._test(pt))
            self.table.setCellWidget(row, 4, test)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, slot in (("Reload", self.refresh), ("Apply to controller", self._apply)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self._loaded = False

    def showEvent(self, e):
        if not self._loaded:
            self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        def load(d):
            return d.led_counts(), [d.vmap_name(ch) for ch in range(p.NUM_CHANNELS)]
        def show(r):
            counts, names = r
            for port in self.names:
                ch = p.PORT_TO_CH[port - 1]
                self.counts[port].setValue(counts[ch])
                self.names[port].setText(names[ch])
            self._loaded = True
            self.labels_changed.emit(self.labels())
        self.device.run(load, show)

    def labels(self) -> dict[int, str]:
        return {pt: w.text().strip() for pt, w in self.names.items() if w.text().strip()}

    def led_counts(self) -> dict[int, int]:
        return {pt: w.value() for pt, w in self.counts.items()}

    def _apply(self) -> None:
        counts = [0] * p.NUM_CHANNELS
        names = {}
        for port in self.names:
            ch = p.PORT_TO_CH[port - 1]
            counts[ch] = self.counts[port].value()
            names[ch] = self.names[port].text().strip()
        def write(d):
            d.set_led_counts(counts)
            old = [d.vmap_name(ch) for ch in range(p.NUM_CHANNELS)]
            for ch, name in names.items():
                if name != old[ch]:
                    d.set_vmap_name(ch, name)
            if d.led_counts() != counts:
                raise IOError("the controller did not accept the LED counts (LED + zLED must be ≤ 256)")
        self.device.run(write, lambda _: (self.device.status.emit("Ports saved to the controller"),
                                          self.labels_changed.emit(self.labels())),
                        lambda e: error(self, "Ports", e))

    def _identify(self, port: int) -> None:
        session = LiveSession(self.device)
        session.start()
        n = max(1, self.counts[port].value())
        session.show({port: [(255, 255, 255)] * n})
        QTimer.singleShot(3000, session.stop)
        self.device.status.emit(f"Port {port} is lit white for 3 s")

    def _test(self, port: int) -> None:
        dlg = LedTestDialog(self.device, port, max(1, min(self.counts[port].value(), 64)), self)
        dlg.stored.connect(lambda n: (self.counts[port].setValue(n), self._apply()))
        dlg.exec()
