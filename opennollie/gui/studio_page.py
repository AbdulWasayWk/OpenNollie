"""Effects studio: build profiles from layers, preview them, run them live or store them on the device."""

import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QProgressDialog,
                               QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget, QToolButton, QVBoxLayout,
                               QWidget)

from .. import engine, profile
from .. import protocol as p
from ..effects import EFFECT_INFO, EffectSpec, parse_color
from ..profile import Layer, Profile
from .caseview import CaseView
from .common import ColorButton, Device, PortPicker, error, hbox, hexcolor

from ..profile import EXAMPLE_PROFILES, USER_PROFILES

# Quick port selections for this machine (see CLAUDE.md); order = travel order.
QUICK_GROUPS = {
    "All": [8, 7, 6, 15, 14, 16, 9, 13, 12, 4, 5],
    "Fans": [8, 7, 6, 15, 14, 16, 13, 12, 4, 5],
    "Intake": [8, 7, 6, 15, 14, 16],
    "Exhaust": [4, 5],
    "CPU": [13, 12],
    "GPU": [9],
}


def new_layer() -> Layer:
    return Layer(list(QUICK_GROUPS["All"]), EffectSpec("rainbow", period=8, spread=0.0, brightness=0.6))


# How a layer's pattern is laid out: (label, unit, span, flows). "flows" = uses the spread amount.
PATTERNS = [
    ("All ports together (in sync)", "port", "group", False),
    ("Flow across the ports", "port", "group", True),
    ("Around each fan (fans in sync)", "led", "port", True),
    ("Along all LEDs as one strip", "led", "group", True),
]


class ColorList(QWidget):
    """An editable row of color swatches."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.buttons: list[ColorButton] = []
        self.add_btn, self.del_btn = QToolButton(), QToolButton()
        self.add_btn.setText("+")
        self.del_btn.setText("−")
        self.add_btn.clicked.connect(lambda: (self._add((255, 255, 255)), self.changed.emit()))
        self.del_btn.clicked.connect(self._del)
        self.lay.addWidget(self.add_btn)
        self.lay.addWidget(self.del_btn)
        self.lay.addStretch(1)
        self.limits = (1, 8)

    def _add(self, c) -> None:
        b = ColorButton(c)
        b.changed.connect(lambda _: self.changed.emit())
        self.lay.insertWidget(len(self.buttons), b)
        self.buttons.append(b)
        self._enable()

    def _del(self) -> None:
        if len(self.buttons) > self.limits[0]:
            self.buttons.pop().deleteLater()
            self._enable()
            self.changed.emit()

    def _enable(self) -> None:
        self.add_btn.setEnabled(len(self.buttons) < self.limits[1])
        self.del_btn.setEnabled(len(self.buttons) > self.limits[0])

    def set(self, colors, limits) -> None:
        self.limits = limits
        while self.buttons:
            self.buttons.pop().deleteLater()
        for c in list(colors)[: limits[1]] or [(255, 255, 255)]:
            self._add(c)
        while len(self.buttons) < limits[0]:
            self._add((0, 0, 255))

    def colors(self):
        return [b.color() for b in self.buttons]


class KeyframeTable(QWidget):
    """Custom timeline: (position in cycle 0..1, color) rows."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Position in cycle", "Color"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(140)
        lay.addWidget(self.table)
        add, rm = QPushButton("Add keyframe"), QPushButton("Remove selected")
        add.clicked.connect(lambda: (self._row(1.0 if not self.table.rowCount() else min(0.99, self._last() + 0.2),
                                               (255, 255, 255)), self.changed.emit()))
        rm.clicked.connect(self._remove)
        lay.addWidget(hbox(add, rm))

    def _last(self) -> float:
        return max(self.table.cellWidget(r, 0).value() for r in range(self.table.rowCount()))

    def _row(self, pos: float, color) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 0.999)
        spin.setSingleStep(0.05)
        spin.setValue(pos)
        spin.valueChanged.connect(lambda _: self.changed.emit())
        btn = ColorButton(color)
        btn.changed.connect(lambda _: self.changed.emit())
        self.table.setCellWidget(r, 0, spin)
        self.table.setCellWidget(r, 1, btn)

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True) or [self.table.rowCount() - 1]
        for r in rows:
            if self.table.rowCount() > 1:
                self.table.removeRow(r)
        self.changed.emit()

    def set(self, frames) -> None:
        self.table.setRowCount(0)
        for pos, c in frames:
            self._row(float(pos), parse_color(c))

    def frames(self):
        return [[round(self.table.cellWidget(r, 0).value(), 3), hexcolor(self.table.cellWidget(r, 1).color())]
                for r in range(self.table.rowCount())]


class LayerEditor(QGroupBox):
    changed = Signal()

    def __init__(self):
        super().__init__("Layer")
        self._loading = False
        form = QFormLayout(self)
        self.picker = PortPicker()
        quick = QWidget()
        ql = QHBoxLayout(quick)
        ql.setContentsMargins(0, 0, 0, 0)
        for name, ports in QUICK_GROUPS.items():
            b = QToolButton()
            b.setText(name)
            b.clicked.connect(lambda _=False, pts=ports: (self.picker.set_ports(pts), self._emit()))
            ql.addWidget(b)
        ql.addStretch(1)
        form.addRow("Ports", self.picker)
        form.addRow("", quick)
        form.addRow("", QLabel("<small>Flowing patterns travel across ports in the order you pick them.</small>"))

        self.effect = QComboBox()
        for key, inf in EFFECT_INFO.items():
            self.effect.addItem(inf.label, key)
        form.addRow("Effect", self.effect)
        self.help = QLabel()
        self.help.setWordWrap(True)
        form.addRow("", self.help)

        self.colors = ColorList()
        form.addRow("Colors", self.colors)
        self.keyframes = KeyframeTable()
        form.addRow("Keyframes", self.keyframes)

        self.period = QDoubleSpinBox()
        self.period.setRange(0.1, 120)
        self.period.setSuffix(" s")
        self.period.setSingleStep(0.5)
        self.speed_label = QLabel("Cycle time")
        form.addRow(self.speed_label, self.period)
        self.pattern = QComboBox()
        for label, *_ in PATTERNS:
            self.pattern.addItem(label)
        form.addRow("Pattern", self.pattern)
        self.spread = QDoubleSpinBox()
        self.spread.setRange(0.05, 4)
        self.spread.setSingleStep(0.1)
        self.spread.setValue(1.0)
        self.spread.setToolTip("How much of one cycle the pattern spans: 1 = one full cycle across the ports/LEDs")
        form.addRow("Pattern spread", self.spread)
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        form.addRow("Brightness", self.brightness)
        self.reverse = QCheckBox("Reverse direction")
        self.interp = QComboBox()
        self.interp.addItems(["smooth", "linear", "step"])
        form.addRow("Blending", hbox(self.interp, self.reverse))

        self.param_box = QWidget()
        self.param_form = QFormLayout(self.param_box)
        self.param_form.setContentsMargins(0, 0, 0, 0)
        form.addRow(self.param_box)
        self.param_widgets: dict[str, QWidget] = {}
        self.form = form

        for sig in (self.picker.changed, self.colors.changed, self.keyframes.changed, self.period.valueChanged,
                    self.spread.valueChanged, self.brightness.valueChanged, self.reverse.toggled,
                    self.interp.currentIndexChanged):
            sig.connect(self._emit)
        self.effect.currentIndexChanged.connect(self._effect_changed)
        self.pattern.currentIndexChanged.connect(self._pattern_changed)

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def _set_row_visible(self, w: QWidget, visible: bool) -> None:
        w.setVisible(visible)
        label = self.form.labelForField(w)
        if label:
            label.setVisible(visible)

    def _effect_changed(self, *_):
        kind = self.effect.currentData()
        inf = EFFECT_INFO[kind]
        self.help.setText(inf.help)
        lo, hi = inf.colors
        self._set_row_visible(self.colors, hi > 0)
        if hi > 0:
            self.colors.set(self.colors.colors(), (max(lo, 1), hi))
        self._set_row_visible(self.keyframes, kind == "keyframes")
        if kind == "keyframes" and not self.keyframes.frames():
            self.keyframes.set([[0, "#ff0000"], [0.5, "#0000ff"]])
        animated = kind != "static"
        for w in (self.period, self.interp, self.reverse, self.pattern):
            self._set_row_visible(w, animated)
        self._build_params(kind)
        self._pattern_changed()

    def _pattern_changed(self, *_):
        flows = PATTERNS[self.pattern.currentIndex()][3]
        kind = self.effect.currentData()
        self._set_row_visible(self.spread, kind != "static" and flows)
        self.reverse.setEnabled(flows)
        self._emit()

    def _build_params(self, kind: str, values: dict | None = None) -> None:
        while self.param_form.rowCount():
            self.param_form.removeRow(0)
        self.param_widgets = {}
        values = values or {}
        for prm in EFFECT_INFO[kind].params:
            if prm.kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(values.get(prm.key, prm.default)))
                w.toggled.connect(self._emit)
            else:
                w = QDoubleSpinBox()
                w.setRange(prm.lo, prm.hi)
                w.setSingleStep((prm.hi - prm.lo) / 20)
                w.setValue(float(values.get(prm.key, prm.default)))
                w.valueChanged.connect(self._emit)
            self.param_form.addRow(prm.label, w)
            self.param_widgets[prm.key] = w

    def load(self, layer: Layer) -> None:
        self._loading = True
        s = layer.spec
        self.picker.set_ports(layer.ports)
        i = max(0, self.effect.findData(s.kind))
        self.effect.blockSignals(True)
        self.effect.setCurrentIndex(i)
        self.effect.blockSignals(False)
        self.colors.set(s.colors, (1, 8))
        self.keyframes.set(s.params.get("keyframes", []))
        self.period.setValue(s.period)
        if s.spread > 0:
            self.spread.setValue(s.spread)
        flows = s.spread > 0 or layer.unit == "led"
        idx = next(i for i, (_, unit, span, fl) in enumerate(PATTERNS)
                   if unit == layer.unit and (span == layer.span or unit == "port") and fl == flows)
        self.pattern.blockSignals(True)
        self.pattern.setCurrentIndex(idx)
        self.pattern.blockSignals(False)
        self.brightness.setValue(round(s.brightness * 100))
        self.reverse.setChecked(s.reverse)
        self.interp.setCurrentText(s.interp)
        self._effect_changed()
        self._build_params(self.effect.currentData(), s.params)
        self._loading = False

    def value(self) -> Layer:
        kind = self.effect.currentData()
        params = {}
        for key, w in self.param_widgets.items():
            params[key] = w.isChecked() if isinstance(w, QCheckBox) else (
                w.value() if isinstance(w, QSpinBox) else round(w.value(), 3))
        if kind == "keyframes":
            params["keyframes"] = self.keyframes.frames()
        colors = self.colors.colors() if self.colors.isVisibleTo(self) else [(255, 255, 255)]
        _, unit, span, flows = PATTERNS[self.pattern.currentIndex()]
        if kind == "static":
            unit, span, flows = "port", "group", False
        spec = EffectSpec(kind, colors=colors, period=self.period.value(),
                          spread=round(self.spread.value(), 3) if flows else 0.0,
                          brightness=self.brightness.value() / 100, reverse=self.reverse.isChecked(),
                          interp=self.interp.currentText(), params=params)
        return Layer(self.picker.ports() or [], spec, unit, span)


class StudioPage(QWidget):
    title = "Effects studio"
    icon = "applications-graphics"
    progress = Signal(str, int, int)

    def __init__(self, device: Device, led_counts, orders=lambda: {}):
        super().__init__()
        self.device = device
        self.led_counts = led_counts          # callable -> {port: count} (from the Ports page)
        self.orders = orders                  # callable -> {port: LED order} (from the Ports page)
        self.prof = Profile(name="New profile", layers=[new_layer()])
        self.path: Path | None = None
        self._live_stop: threading.Event | None = None
        self._live_restart = QTimer(self)
        self._live_restart.setSingleShot(True)
        self._live_restart.setInterval(400)
        self._live_restart.timeout.connect(self._live_update)

        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.name = QLineEdit(self.prof.name)
        self.fps = QSpinBox()
        self.fps.setRange(5, 60)
        self.fps.setValue(30)
        self.fps.setSuffix(" fps")
        top.addWidget(QLabel("Profile"))
        top.addWidget(self.name, 1)
        top.addWidget(self.fps)
        for text, slot in (("New", self._new), ("Open…", self._open), ("Save", self._save), ("Save as…", self._save_as)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            top.addWidget(b)
        outer.addLayout(top)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Layers <small>(later layers win on shared ports)</small>"))
        self.layers = QListWidget()
        self.layers.currentRowChanged.connect(self._select_layer)
        ll.addWidget(self.layers)
        lb = QHBoxLayout()
        for text, slot in (("Add", self._add_layer), ("Remove", self._remove_layer), ("↑", lambda: self._move(-1)),
                           ("↓", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            lb.addWidget(b)
        ll.addLayout(lb)
        split.addWidget(left)

        self.editor = LayerEditor()
        self.editor.changed.connect(self._layer_edited)
        split.addWidget(self.editor)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.view = CaseView()
        rl.addWidget(self.view, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        rl.addWidget(self.note)
        btns = QHBoxLayout()
        self.live_btn = QPushButton("Preview on device")
        self.live_btn.setCheckable(True)
        self.live_btn.toggled.connect(self._toggle_live)
        save_dev = QPushButton("Save to device")
        save_dev.setDefault(True)
        save_dev.clicked.connect(self._save_to_device)
        btns.addWidget(self.live_btn)
        btns.addWidget(save_dev)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([180, 420, 520])
        outer.addWidget(split, 1)

        self.name.textEdited.connect(self._meta_edited)
        self.fps.valueChanged.connect(self._meta_edited)
        self.progress.connect(self._on_progress)
        self._t0 = time.monotonic()
        self._renderer = None
        self.anim = QTimer(self)
        self.anim.setInterval(33)
        self.anim.timeout.connect(self._tick)
        self._load_profile(self.prof)

    # --- profile <-> UI -----------------------------------------------------------------------

    def _load_profile(self, prof: Profile) -> None:
        self.prof = prof
        self.name.setText(prof.name)
        self.fps.setValue(prof.fps)
        self.layers.blockSignals(True)
        self.layers.clear()
        for l in prof.layers:
            self.layers.addItem(self._layer_title(l))
        self.layers.blockSignals(False)
        self.layers.setCurrentRow(0)
        self._rebuild()

    def _layer_title(self, l: Layer) -> str:
        name = EFFECT_INFO[l.spec.kind].label
        ports = profile._ports_text(l.ports) if l.ports else "no ports"
        return f"{name} — {ports}"

    def _select_layer(self, row: int) -> None:
        if 0 <= row < len(self.prof.layers):
            self.editor.load(self.prof.layers[row])

    def _layer_edited(self) -> None:
        row = self.layers.currentRow()
        if row < 0:
            return
        layer = self.editor.value()
        self.prof.layers[row] = layer
        self.layers.item(row).setText(self._layer_title(layer))
        self._rebuild()

    def _meta_edited(self, *_):
        self.prof = replace(self.prof, name=self.name.text().strip() or "Untitled", fps=self.fps.value())
        self._rebuild()

    def _add_layer(self) -> None:
        self.prof.layers.append(new_layer())
        self.layers.addItem(self._layer_title(self.prof.layers[-1]))
        self.layers.setCurrentRow(len(self.prof.layers) - 1)
        self._rebuild()

    def _remove_layer(self) -> None:
        row = self.layers.currentRow()
        if row >= 0 and len(self.prof.layers) > 1:
            del self.prof.layers[row]
            self.layers.takeItem(row)
            self._rebuild()

    def _move(self, d: int) -> None:
        row = self.layers.currentRow()
        j = row + d
        if 0 <= row and 0 <= j < len(self.prof.layers):
            L = self.prof.layers
            L[row], L[j] = L[j], L[row]
            self._load_profile(self.prof)
            self.layers.setCurrentRow(j)

    def current_profile(self) -> Profile:
        """The edited profile with LED counts from the controller (unless the profile sets its own)."""
        prof = replace(self.prof, layers=[l for l in self.prof.layers if l.ports])
        return prof.with_device_leds(self.led_counts(), self.orders())

    # --- preview ------------------------------------------------------------------------------

    def _rebuild(self) -> None:
        prof = self.current_profile()
        try:
            self._renderer = engine.Renderer(prof) if prof.layers else None
            n, _ = engine.loop_frames(prof) if prof.layers else (0, [])
            how = ("stored as built-in Solid canvases" if prof.is_static else
                   f"stored as a {n / prof.fps:.1f} s seamless animation")
            self.note.setText(f"Save to device: {how}.")
        except ValueError as e:
            self._renderer = None
            self.note.setText(f"<span style='color:#e05050'>{e}</span>")
        if self.live_btn.isChecked():
            self._live_restart.start()

    def _tick(self) -> None:
        if not self._renderer:
            self.view.set_frame({})
            return
        frame = self._renderer.frame(time.monotonic() - self._t0)
        self.view.set_frame({p.CH_TO_PORT[ch]: cols for ch, cols in frame.items() if cols})

    def set_labels(self, labels: dict[int, str]) -> None:
        """Called when the Ports page (re)loads: port names and LED counts may have changed."""
        self.view.labels = labels
        self.editor.picker.set_labels(labels)
        self._rebuild()

    def showEvent(self, e):
        self.anim.start()
        self._rebuild()
        super().showEvent(e)

    def hideEvent(self, e):
        self.anim.stop()
        super().hideEvent(e)

    # --- device -------------------------------------------------------------------------------

    def _toggle_live(self, on: bool) -> None:
        if on:
            self._live_update()
        else:
            self._live_off()

    def _live_off(self) -> None:
        if self._live_stop:
            self._live_stop.set()
            self._live_stop = None
        if self.device.via_daemon:
            self.device.run(lambda d: d.transport.call("live_stop"))

    def _live_update(self) -> None:
        prof = self.current_profile()
        if not prof.layers:
            self.live_btn.setChecked(False)
            error(self, "Preview", "Pick some ports first.")
            return
        text = profile.dumps(prof)
        if self.device.via_daemon:
            self.device.run(lambda d: d.transport.call("live_start", profile=text, source="studio"),
                            err=lambda e: (self.live_btn.setChecked(False), error(self, "Preview", e)))
            return
        if self._live_stop:
            self._live_stop.set()
        stop = self._live_stop = threading.Event()
        def run():
            try:
                engine.run_live(self.device.dev_sync(), prof, stop)
            except Exception as e:
                self.progress.emit(f"error:{e}", 0, 0)
        self.device.run(lambda d: threading.Thread(target=run, daemon=True).start())

    def _save_to_device(self) -> None:
        prof = self.current_profile()
        if not prof.layers:
            return error(self, "Save to device", "Pick some ports first.")
        if self.live_btn.isChecked():
            self.live_btn.setChecked(False)
        self.dlg = QProgressDialog("Saving to the controller…", None, 0, 100, self)
        self.dlg.setWindowTitle("Save to device")
        self.dlg.setMinimumDuration(0)
        self.dlg.setValue(0)
        self.device.run(lambda d: engine.save_to_device(d, prof, lambda ph, a, b: self.progress.emit(ph, a, b)),
                        self._saved, lambda e: (self.dlg.close(), error(self, "Save to device", e)))

    def _on_progress(self, phase: str, done: int, total: int) -> None:
        if phase.startswith("error:"):
            self.live_btn.setChecked(False)
            return error(self, "Preview", phase[6:])
        if hasattr(self, "dlg"):
            pause = "\n(the lights pause for about 2 s while the controller saves to its memory)"
            self.dlg.setLabelText({"render": "Rendering…", "erase": "Erasing…" + pause, "write": "Writing…" + pause,
                                   "verify": "Verifying…" + pause}.get(phase, phase))
            self.dlg.setValue(int(100 * done / max(total, 1)))

    def _saved(self, msg: str) -> None:
        self.dlg.close()
        self.device.status.emit(f"'{self.prof.name}' {msg}")

    # --- files --------------------------------------------------------------------------------

    def _new(self) -> None:
        self.path = None
        self._load_profile(Profile(name="New profile", layers=[new_layer()]))

    def _open(self) -> None:
        USER_PROFILES.mkdir(parents=True, exist_ok=True)
        start = str(USER_PROFILES if any(USER_PROFILES.glob("*.toml")) else EXAMPLE_PROFILES)
        path, _ = QFileDialog.getOpenFileName(self, "Open profile", start, "Profiles (*.toml)")
        if path:
            try:
                self._load_profile(profile.load(path))
                self.path = Path(path) if Path(path).parent != EXAMPLE_PROFILES else None
            except Exception as e:
                error(self, "Open profile", e)

    def _save(self) -> None:
        if not self.path:
            return self._save_as()
        profile.save(self.prof, self.path)
        self.device.status.emit(f"Saved {self.path}")

    def _save_as(self) -> None:
        USER_PROFILES.mkdir(parents=True, exist_ok=True)
        default = USER_PROFILES / (self.prof.name.lower().replace(" ", "-") + ".toml")
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", str(default), "Profiles (*.toml)")
        if path:
            self.path = Path(path)
            self._save()
