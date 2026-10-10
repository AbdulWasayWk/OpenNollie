"""Ports & LEDs page: names, LED counts, identify, LED-count test."""

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QTableWidget,
                               QVBoxLayout, QWidget)

from .. import calibration, client, layouts
from .. import protocol as p
from .caseview import CASE_LAYOUT
from .common import Device, error, hbox

RGB = tuple[int, int, int]


class LiveSession(QObject):
    """Drive LEDs from the GUI: pauses the daemon's live effect meanwhile, re-sends the frame so the
    device doesn't fall back (it does ~3 s after the last frame).

    A QObject with a Qt parent so it (and its timer) lives as long as the widget using it; a parentless
    timer whose Python owner got garbage-collected crashed the app (Identify)."""

    def __init__(self, device: Device, interval_ms: int = 1000, parent: QObject | None = None):
        super().__init__(parent)
        self.device = device
        self.frame: dict[int, list[RGB]] = {}
        self.timer = QTimer(self)
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
        self.live = LiveSession(device, 120, self)
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


class LedOrderDialog(QDialog):
    """Choose the order effects travel over a port's LEDs: this port's LEDs, or a preset file
    (SignalRGB component / Nollie vmap). Previews as a running light on the device."""

    applied = Signal()

    def __init__(self, device: Device, port: int, leds: int, zleds: int, current: list[int] | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"LED order: port {port}")
        self.device, self.port = device, port
        self.layout_ = layouts.strip(max(1, leds), zleds)
        self.live = LiveSession(device, 150, self)
        self.step = 0
        self.anim = QTimer(self)
        self.anim.setInterval(150)
        self.anim.timeout.connect(self._tick)

        form = QFormLayout(self)
        self.source = QLabel()
        pick, plain = QPushButton("Load preset file…"), QPushButton("This port's LEDs")
        pick.clicked.connect(self._pick)
        plain.clicked.connect(lambda: self._set_layout(layouts.strip(max(1, leds), zleds)))
        form.addRow("Layout", self.source)
        form.addRow("", hbox(plain, pick))
        self.how = QComboBox()
        self.how.addItems(layouts.ORDERS)
        self.reverse = QCheckBox("Reverse")
        self.start = QSpinBox()
        self.start.setPrefix("start at LED ")
        form.addRow("Order", hbox(self.how, self.reverse, self.start))
        self.result = QLabel()
        self.result.setWordWrap(True)
        form.addRow("", self.result)
        self.name_it = QCheckBox("Name the port after the preset")
        form.addRow("", self.name_it)
        self.preview = QCheckBox("Preview on the device (a white light runs in this order; blue = first LED)")
        self.preview.toggled.connect(self._toggle_preview)
        form.addRow("", self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for sig in (self.how.currentIndexChanged, self.reverse.toggled, self.start.valueChanged):
            sig.connect(self._update)
        if current:      # start from what's stored: infer reverse / start for the plain wiring
            desc = layouts.describe(current, leds)
            self.reverse.setChecked(desc.startswith("reversed"))
            if "starts at LED" in desc:
                self.start.setValue(int(desc.rsplit(" ", 1)[1]))
        self._set_layout(self.layout_, keep=True)

    def _set_layout(self, layout: layouts.Layout, keep: bool = False) -> None:
        self.layout_ = layout
        self.source.setText(f"<b>{layout.name}</b>: {layout.leds} LEDs" + (f" + {layout.zleds} dark" if layout.zleds else ""))
        self.how.setEnabled(bool(layout.coords))
        if not layout.coords:
            self.how.setCurrentIndex(0)
        self.start.setRange(1, layout.leds)
        if not keep:
            self.start.setValue(1)
            self.reverse.setChecked(False)
        self.name_it.setVisible(layout.kind != "strip")
        self.name_it.setChecked(layout.kind != "strip")
        self._update()

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "LED layout preset", "", "Layouts (*.json)")
        if path:
            try:
                self._set_layout(layouts.load(path))
            except Exception as e:
                error(self, "Layout", e)

    def order(self) -> list[int]:
        return layouts.order(self.layout_, self.how.currentText(), self.reverse.isChecked(), self.start.value() - 1)

    def _update(self, *_):
        o = self.order()
        lit = [x + 1 for x in o[: self.layout_.leds]]
        text = ", ".join(map(str, lit)) if len(lit) <= 24 else ", ".join(map(str, lit[:12])) + ", …"
        self.result.setText(f"Effects travel over the LEDs (as wired) in this order: {text}  "
                            f"<i>({layouts.describe(o, self.layout_.leds)})</i>")

    def _toggle_preview(self, on: bool) -> None:
        if on:
            self.live.start()
            self.anim.start()
        else:
            self.anim.stop()
            self.live.stop()

    def _tick(self) -> None:
        o, n = self.order(), self.layout_.leds
        frame = [(0, 0, 0)] * len(o)
        frame[o[0]] = (0, 0, 60)
        for k, level in ((0, 255), (1, 60), (2, 12)):
            frame[o[(self.step - k) % n]] = (level,) * 3
        self.step += 1
        self.live.show({self.port: frame})

    def _apply(self) -> None:
        o, L = self.order(), self.layout_
        name = L.name[: p.NAME_MAX_CHARS] if self.name_it.isVisible() and self.name_it.isChecked() else None
        self.device.run(lambda d: layouts.apply(d, self.port, o, L.leds, L.zleds, name),
                        lambda _: (self.applied.emit(), self.accept()), lambda e: error(self, "LED order", e))

    def done(self, r):
        self.preview.setChecked(False)
        super().done(r)


class ColorBalanceDialog(QDialog):
    """Match a port's colors to the others: a test color shows on every port while you adjust this
    port's red/green/blue levels."""

    TEST_COLORS = [("White", (255, 255, 255)), ("Cyan", (0, 255, 255)), ("Purple", (128, 0, 255)),
                   ("Orange", (255, 128, 0)), ("Yellow", (255, 255, 0)), ("Pink", (255, 64, 160)),
                   ("Green", (0, 255, 0)), ("Blue", (0, 0, 255)), ("Red", (255, 0, 0))]

    def __init__(self, device: Device, port: int, label: str, counts: dict[int, int], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Color balance: port {port}" + (f" ({label})" if label else ""))
        self.port, self.counts = port, counts
        self.gains = calibration.load()
        self.live = LiveSession(device, 200, self)
        form = QFormLayout(self)
        form.addRow(QLabel(
            "The test color shows on <b>every</b> port. Lower this port's red / green / blue until it looks like "
            "the others.<br><i>Example: if white looks yellowish here, lower red and green a little.</i>"))
        self.test = QComboBox()
        for name, rgb in self.TEST_COLORS:
            self.test.addItem(name, rgb)
        self.test.currentIndexChanged.connect(self._show)
        form.addRow("Test color", self.test)
        g = self.gains.get(port, calibration.NEUTRAL)
        self.sliders = []
        for i, name in enumerate(("Red", "Green", "Blue")):
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, 100)
            sl.setValue(round(g[i] * 100))
            value = QLabel()
            sl.valueChanged.connect(lambda v, lab=value: (lab.setText(f"{v} %"), self._show()))
            value.setText(f"{sl.value()} %")
            value.setMinimumWidth(44)
            form.addRow(name, hbox(sl, value, stretch=False))
            self.sliders.append(sl)
        form.addRow(QLabel("<small>Applies to live effects right away and to effects saved on the controller the "
                           "next time you save them.</small>"))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Reset | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Reset).setText("Reset to 100 %")
        buttons.button(QDialogButtonBox.Reset).clicked.connect(lambda: [sl.setValue(100) for sl in self.sliders])
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.live.start()
        self._show()

    def gain(self) -> tuple[float, float, float]:
        return tuple(sl.value() / 100 for sl in self.sliders)

    def _show(self, *_) -> None:
        rgb = self.test.currentData()
        frame = {}
        for port, n in self.counts.items():
            if n:
                g = self.gain() if port == self.port else self.gains.get(port)
                frame[port] = [calibration.apply(rgb, g)] * n
        self.live.show(frame)

    def _save(self) -> None:
        gains = calibration.load()
        gains[self.port] = self.gain()
        calibration.save(gains)
        self.accept()

    def done(self, r):
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
        self.table = QTableWidget(p.NUM_CHANNELS, 7)
        self.table.setHorizontalHeaderLabels(["Port", "Name", "LEDs", "", "", "LED order", "Color balance"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.names: dict[int, QLineEdit] = {}
        self.counts: dict[int, QSpinBox] = {}
        self.order_buttons: dict[int, QPushButton] = {}
        self.balance_buttons: dict[int, QPushButton] = {}
        self._zleds = [0] * p.NUM_CHANNELS
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
            order = QPushButton("as wired")
            order.clicked.connect(lambda _=False, pt=port: self._order(pt))
            self.table.setCellWidget(row, 5, order)
            self.order_buttons[port] = order
            balance = QPushButton()
            balance.clicked.connect(lambda _=False, pt=port: self._balance(pt))
            self.table.setCellWidget(row, 6, balance)
            self.balance_buttons[port] = balance
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
        self._orders: dict[int, list[int]] = {}

    def showEvent(self, e):
        if not self._loaded:
            self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        def load(d):
            return (d.led_counts(), [d.vmap_name(ch) for ch in range(p.NUM_CHANNELS)], layouts.read_ports(d)[1],
                    d.zled_counts())
        def show(r):
            counts, names, self._orders, self._zleds = r
            for port in self.names:
                ch = p.PORT_TO_CH[port - 1]
                self.counts[port].setValue(counts[ch])
                self.names[port].setText(names[ch])
                order = self._orders.get(port)
                dark = f" + {self._zleds[ch]} dark" if self._zleds[ch] else ""
                self.order_buttons[port].setText((layouts.describe(order, counts[ch]) if order else "as wired") + dark)
            self._loaded = True
            self._show_balance()
            self.labels_changed.emit(self.labels())
        self.device.run(load, show)

    def labels(self) -> dict[int, str]:
        return {pt: w.text().strip() for pt, w in self.names.items() if w.text().strip()}

    def led_counts(self) -> dict[int, int]:
        return {pt: w.value() for pt, w in self.counts.items()}

    def orders(self) -> dict[int, list[int]]:
        """LED order per port as stored on the controller (only ports that aren't plain wiring order)."""
        return dict(self._orders)

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
        session = LiveSession(self.device, parent=self)
        session.start()
        n = max(1, self.counts[port].value())
        session.show({port: [(255, 255, 255)] * n})
        QTimer.singleShot(3000, lambda: (session.stop(), QTimer.singleShot(1000, session.deleteLater)))
        self.device.status.emit(f"Port {port} is lit white for 3 s")

    def _show_balance(self) -> None:
        gains = calibration.load()
        for port, b in self.balance_buttons.items():
            g = gains.get(port)
            b.setText(f"R {g[0]:.0%}  G {g[1]:.0%}  B {g[2]:.0%}" if g else "neutral")

    def _balance(self, port: int) -> None:
        dlg = ColorBalanceDialog(self.device, port, self.names[port].text().strip(), self.led_counts(), self)
        if dlg.exec():
            self._show_balance()
            self.labels_changed.emit(self.labels())      # the studio re-renders with the new balance

    def _order(self, port: int) -> None:
        ch = p.PORT_TO_CH[port - 1]
        dlg = LedOrderDialog(self.device, port, self.counts[port].value(), self._zleds[ch], self._orders.get(port), self)
        dlg.applied.connect(self.refresh)
        dlg.exec()

    def _test(self, port: int) -> None:
        dlg = LedTestDialog(self.device, port, max(1, min(self.counts[port].value(), 64)), self)
        dlg.stored.connect(lambda n: (self.counts[port].setValue(n), self._apply()))
        dlg.exec()
