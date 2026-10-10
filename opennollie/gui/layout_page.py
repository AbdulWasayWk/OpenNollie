"""Layout page: arrange the fans and LED strips of the case picture used for previews."""

from dataclasses import replace

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPushButton, QSlider, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .. import caselayout
from .. import protocol as p
from ..caselayout import CaseLayout, Item
from .caseview import CaseView
from .common import Device, confirm, hbox
from .ports_page import LiveSession


class LayoutStore(QObject):
    """The case layout shared by the pages; saved to disk on every change."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.layout = caselayout.load()
        self.loaded_from_disk = self.layout is not None
        self.layout = self.layout or CaseLayout()

    def ensure(self, led_counts: dict[int, int]) -> None:
        """First run: start from an automatic arrangement of the connected ports."""
        if not self.loaded_from_disk:
            self.layout = caselayout.auto(led_counts)
            self.loaded_from_disk = True
            self.save()

    def save(self) -> None:
        caselayout.save(self.layout)
        self.changed.emit()

    def labels(self) -> dict[int, str]:
        out = {}
        for it in self.layout.items:
            if it.label and it.port not in out:
                out[it.port] = it.label
        return out


class LayoutPage(QWidget):
    title = "Layout"
    icon = "transform-move"

    def __init__(self, device: Device, store: LayoutStore, led_counts, port_labels):
        super().__init__()
        self.device, self.store = device, store
        self.led_counts, self.port_labels = led_counts, port_labels
        self.live = LiveSession(device, 1000, self)
        self._lit = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self.store.save)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "The picture used for previews. <b>Drag</b> fans and strips to where they are in your case, and set which "
            "port (and which LEDs on it) each one is. Daisy-chained fans on one port: one item per fan, with "
            "<i>First LED</i> 1, 9, 17 … The <b>white</b> LED marks where each item starts."))
        split = QSplitter()
        self.view = CaseView()
        self.view.editing = True
        self.view.show_order = True
        self.view.selected.connect(self._select)
        self.view.moved.connect(self._moved)
        split.addWidget(self.view)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        buttons = QWidget()
        bl = QHBoxLayout(buttons)
        bl.setContentsMargins(0, 0, 0, 0)
        for text, slot in (("Add fan", lambda: self._add("fan")), ("Add strip", lambda: self._add("strip")),
                           ("Duplicate", self._duplicate), ("Remove", self._remove)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bl.addWidget(b)
        sl.addWidget(buttons)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        sl.addWidget(self.list, 1)

        self.props = QGroupBox("Selected item")
        f = QFormLayout(self.props)
        self.label = QLineEdit()
        self.label.setPlaceholderText("(the port's name)")
        self.port = QSpinBox()
        self.port.setRange(1, p.NUM_CHANNELS)
        self.kind = QComboBox()
        self.kind.addItem("Fan (ring of LEDs)", "fan")
        self.kind.addItem("Strip (line of LEDs)", "strip")
        self.first = QSpinBox()
        self.first.setRange(1, p.LEDS_PER_CHANNEL)
        self.count = QSpinBox()
        self.count.setRange(0, p.LEDS_PER_CHANNEL)
        self.count.setSpecialValueText("rest of the port")
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(20, 400)
        self.angle = QSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setSuffix("°")
        self.angle.setWrapping(True)
        self.angle.setSingleStep(15)
        f.addRow("Name", self.label)
        f.addRow("Port", self.port)
        f.addRow("Type", self.kind)
        f.addRow("First LED", self.first)
        f.addRow("LEDs", self.count)
        f.addRow("Size", self.size)
        f.addRow("Angle", self.angle)
        self.identify = QPushButton("Light it up on the device")
        self.identify.setCheckable(True)
        self.identify.toggled.connect(self._toggle_identify)
        f.addRow("", self.identify)
        sl.addWidget(self.props)
        reset = QPushButton("Auto-arrange (start over)")
        reset.clicked.connect(self._auto)
        sl.addWidget(reset)
        split.addWidget(side)
        split.setSizes([900, 360])
        outer.addWidget(split, 1)

        for w, sig in ((self.label, self.label.textEdited), (self.port, self.port.valueChanged),
                       (self.kind, self.kind.currentIndexChanged), (self.first, self.first.valueChanged),
                       (self.count, self.count.valueChanged), (self.size, self.size.valueChanged),
                       (self.angle, self.angle.valueChanged)):
            sig.connect(self._edited)
        self._loading = False
        self.store.changed.connect(self._reload_from_store)

    # --- sync ---------------------------------------------------------------------------------

    def showEvent(self, e):
        self.store.ensure(self.led_counts())
        self.view.led_counts = self.led_counts()
        self.view.labels = self.port_labels()
        self._reload_list()
        super().showEvent(e)

    def hideEvent(self, e):
        self.identify.setChecked(False)
        super().hideEvent(e)

    def _reload_from_store(self) -> None:
        if self.view.layout_ is not self.store.layout:
            self.view.set_layout(self.store.layout)
            self._reload_list()

    def _title(self, it: Item) -> str:
        name = it.label or self.port_labels().get(it.port) or f"Port {it.port}"
        leds = it.leds(self.led_counts().get(it.port, 8))
        span = f", LEDs {it.first}–{it.first + leds - 1}" if it.first > 1 or it.count else f", {leds} LEDs"
        return f"{name}: port {it.port}, {it.kind}{span}"

    def _reload_list(self) -> None:
        self.view.set_layout(self.store.layout)
        row = self.view.current
        self.list.blockSignals(True)
        self.list.clear()
        for it in self.store.layout.items:
            self.list.addItem(self._title(it))
        self.list.blockSignals(False)
        self._select(min(row, len(self.store.layout.items) - 1) if self.store.layout.items else -1)

    def _select(self, i: int) -> None:
        self.view.current = i
        self.view.update()
        self.list.blockSignals(True)
        self.list.setCurrentRow(i)
        self.list.blockSignals(False)
        self.props.setEnabled(i >= 0)
        if i < 0:
            return
        it = self.store.layout.items[i]
        self._loading = True
        self.label.setText(it.label)
        self.port.setValue(it.port)
        self.kind.setCurrentIndex(0 if it.kind == "fan" else 1)
        self.first.setValue(it.first)
        self.count.setValue(it.count)
        self.size.setValue(round(it.size * 100))
        self.angle.setValue(round(it.angle))
        self.angle.setEnabled(it.kind == "strip")
        self._loading = False
        if self.identify.isChecked():
            self._light()

    def _edited(self, *_) -> None:
        i = self.view.current
        if self._loading or i < 0:
            return
        it = self.store.layout.items[i]
        it.label = self.label.text().strip()
        it.port = self.port.value()
        it.kind = self.kind.currentData()
        it.first = self.first.value()
        it.count = self.count.value()
        it.size = self.size.value() / 100
        it.angle = float(self.angle.value())
        self.angle.setEnabled(it.kind == "strip")
        self.list.item(i).setText(self._title(it))
        self.view.update()
        self._save_timer.start()
        if self.identify.isChecked():
            self._light()

    def _moved(self, i: int) -> None:
        self._save_timer.start()

    # --- actions ------------------------------------------------------------------------------

    def _add(self, kind: str) -> None:
        used = self.store.layout.ports()
        port = next((pt for pt in range(1, p.NUM_CHANNELS + 1)
                     if pt not in used and 0 < self.led_counts().get(pt, 0) < p.LEDS_PER_CHANNEL), 1)
        self.store.layout.items.append(Item(port, kind, x=0.5, y=0.5, size=1.0 if kind == "fan" else 1.2))
        self.store.save()
        self._reload_list()
        self._select(len(self.store.layout.items) - 1)

    def _duplicate(self) -> None:
        i = self.view.current
        if i < 0:
            return
        it = self.store.layout.items[i]
        self.store.layout.items.append(replace(it, x=min(1.0, it.x + 0.08), y=min(1.0, it.y + 0.08)))
        self.store.save()
        self._reload_list()
        self._select(len(self.store.layout.items) - 1)

    def _remove(self) -> None:
        i = self.view.current
        if i >= 0:
            del self.store.layout.items[i]
            self.store.save()
            self._reload_list()

    def _auto(self) -> None:
        if confirm(self, "Auto-arrange", "Replace your layout with an automatic arrangement of the connected ports?"):
            self.store.layout = caselayout.auto(self.led_counts())
            self.store.save()
            self._reload_list()

    def _toggle_identify(self, on: bool) -> None:
        if on:
            self.live.start()
            self._light()
        else:
            self.live.stop()

    def _light(self) -> None:
        """Light only the selected item's LEDs (white, its first LED blue)."""
        i = self.view.current
        if i < 0:
            return self.live.show({})
        it = self.store.layout.items[i]
        n = it.leds(self.led_counts().get(it.port, 8))
        wire = [(0, 0, 0)] * (it.first - 1) + [(0, 0, 255)] + [(255, 255, 255)] * max(0, n - 1)
        self.live.show({it.port: wire})
