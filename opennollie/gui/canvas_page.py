"""Built-in effects page: the controller's own canvases (loop and boot)."""

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .. import protocol as p
from ..flash import Flash
from ..protocol import CanvasLayout, CanvasParams, Mode
from .common import ColorButton, Device, PortPicker, error, hbox

FPS_CHOICES = [(8, "120 fps"), (11, "90 fps"), (16, "60 fps"), (20, "50 fps"), (25, "40 fps"), (33, "30 fps"),
               (50, "20 fps"), (100, "10 fps")]


class CanvasCard(QFrame):
    """One canvas: its ports, firmware effect and settings."""

    remove = Signal(object)
    ports_changed = Signal()

    def __init__(self, index: int, boot: bool, params: CanvasParams, ports: list[int], labels: dict[int, str]):
        super().__init__()
        self.boot = boot
        self.setFrameShape(QFrame.StyledPanel)
        self.params = params
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.title = QLabel()
        head.addWidget(self.title)
        head.addStretch(1)
        rm = QPushButton("Remove")
        rm.clicked.connect(lambda: self.remove.emit(self))
        head.addWidget(rm)
        lay.addLayout(head)
        self.set_index(index)

        form = QFormLayout()
        self.picker = PortPicker(labels)
        self.picker.set_ports(ports)
        self.picker.changed.connect(lambda _: self.ports_changed.emit())
        form.addRow("Ports", self.picker)
        self.effect = QComboBox()
        self.effect.addItems(p.START_EFFECTS if boot else p.EFFECTS)
        self.effect.setCurrentIndex(min(params.effect, self.effect.count() - 1))
        form.addRow("Effect", self.effect)
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(params.brightness)
        form.addRow("Brightness", self.brightness)
        self.step, self.size, self.delay = QSpinBox(), QSpinBox(), QSpinBox()
        for w, v in ((self.step, params.step), (self.size, params.size), (self.delay, params.delay)):
            w.setRange(0, 255)
            w.setValue(v)
        form.addRow("Speed / size / delay", hbox(self.step, self.size, self.delay))
        self.colors = [ColorButton(params.color1), ColorButton(params.color2), ColorButton(params.color3)]
        form.addRow("Colors", hbox(*self.colors))
        self.vmap = QCheckBox("Follow each port's LED order (Ports && LEDs → LED order)")
        self.vmap.setChecked(bool(params.vmap_process))
        form.addRow("", self.vmap)
        lay.addLayout(form)

    def set_index(self, i: int) -> None:
        base = p.BOOT_CANVAS_BASE if self.boot else 0
        self.title.setText(f"<b>{'Boot' if self.boot else 'Loop'} canvas {i + 1}</b>  <small>(#{base + i})</small>")

    def value(self) -> tuple[list[int], CanvasParams]:
        params = replace(self.params, effect=self.effect.currentIndex(), brightness=self.brightness.value(),
                         step=self.step.value(), size=self.size.value(), delay=self.delay.value(),
                         vmap_process=int(self.vmap.isChecked()),
                         color1=self.colors[0].color(), color2=self.colors[1].color(), color3=self.colors[2].color())
        return self.picker.ports(), params


class CanvasSection(QGroupBox):
    def __init__(self, title: str, boot: bool, labels: dict[int, str]):
        super().__init__(title)
        self.boot = boot
        self.labels = labels
        self.cards: list[CanvasCard] = []
        lay = QVBoxLayout(self)
        self.fps = QComboBox()
        for ms, label in FPS_CHOICES:
            self.fps.addItem(label, ms)
        add = QPushButton("Add canvas")
        add.clicked.connect(lambda: self.add(CanvasParams(vmap_process=1), []))
        lay.addWidget(hbox("Frame rate", self.fps, add))
        self.box = QVBoxLayout()
        lay.addLayout(self.box)

    def set(self, layout: CanvasLayout, params: list[CanvasParams], frame_ms: int) -> None:
        for c in list(self.cards):
            self._remove(c)
        for chans, prm in zip(layout.canvases, params):
            self.add(prm, [p.CH_TO_PORT[ch] for ch in chans])
        i = self.fps.findData(frame_ms)
        if i < 0:
            self.fps.addItem(f"{1000 / max(frame_ms, 1):.0f} fps", frame_ms)
            i = self.fps.count() - 1
        self.fps.setCurrentIndex(i)

    def add(self, params: CanvasParams, ports: list[int]) -> None:
        limit = p.BOOT_CANVAS_MAX if self.boot else p.LOOP_CANVAS_MAX
        if len(self.cards) >= limit:
            error(self, "Canvases", f"At most {limit} {'boot' if self.boot else 'loop'} canvases.")
            return
        card = CanvasCard(len(self.cards), self.boot, params, ports, self.labels)
        card.remove.connect(self._remove)
        card.ports_changed.connect(self._sync_disabled)
        self.cards.append(card)
        self.box.addWidget(card)
        self._sync_disabled()

    def _remove(self, card: CanvasCard) -> None:
        self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        for i, c in enumerate(self.cards):
            c.set_index(i)
        self._sync_disabled()

    def _sync_disabled(self) -> None:
        """A port can be in only one canvas."""
        for card in self.cards:
            others = {pt for c in self.cards if c is not card for pt in c.picker.ports()}
            card.picker.set_ports(card.picker.ports(), others)

    def value(self) -> tuple[CanvasLayout, list[CanvasParams], int]:
        chans, params = [], []
        for card in self.cards:
            ports, prm = card.value()
            if ports:
                chans.append([p.PORT_TO_CH[pt - 1] for pt in ports])
                params.append(prm)
        return CanvasLayout(chans), params, self.fps.currentData()


class CanvasPage(QWidget):
    title = "Built-in effects"
    icon = "preferences-desktop-color"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        self.labels: dict[int, str] = {}
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "The controller's own effects. Each <b>canvas</b> is a group of ports running one effect; a port can be in "
            "one canvas. <b>Loop canvases</b> run normally; <b>boot canvases</b> play once at power-on. Applying stores "
            "them on the controller and switches it to built-in effects."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.inner_lay = QVBoxLayout(inner)
        self.loop = CanvasSection("Loop canvases", False, self.labels)
        self.boot = CanvasSection("Boot canvases", True, self.labels)
        self.inner_lay.addWidget(self.loop)
        self.inner_lay.addWidget(self.boot)
        self.inner_lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        row = QHBoxLayout()
        for text, slot in (("Reload", self.refresh), ("Apply to controller", self._apply),
                           ("Preview boot canvases", lambda: self.device.run(lambda d: d.play_boot_canvas()))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        outer.addLayout(row)
        self._loaded = False

    def set_labels(self, labels: dict[int, str]) -> None:
        self.labels.clear()
        self.labels.update(labels)

    def showEvent(self, e):
        if not self._loaded:
            self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        def load(d):
            return d.canvases(False), d.frame_ms(False), d.canvases(True), d.frame_ms(True)
        def show(r):
            (ll, lp), lms, (bl, bp), bms = r
            self.loop.set(ll, lp, lms)
            self.boot.set(bl, bp, bms)
            self._loaded = True
        self.device.run(load, show)

    def _apply(self) -> None:
        loop, boot = self.loop.value(), self.boot.value()
        def write(d):
            d.set_mode(Mode.HARDWARE)        # stops a stored animation instantly (no dark pause)
            d.set_canvases(loop[0], loop[1], boot=False)
            d.set_frame_ms(loop[2], boot=False)
            d.set_canvases(boot[0], boot[1], boot=True)
            d.set_frame_ms(boot[2], boot=True)
            d.set_boot_playlist(p.BootIntro.START_CANVAS, p.BootSteady.HW_EFFECTS)
            d.set_mode(Mode.HARDWARE)
        self.device.run(write, lambda _: self.device.status.emit(
            "Built-in effects stored; the controller runs them at power-on"), lambda e: error(self, "Apply", e))
