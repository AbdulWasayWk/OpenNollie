"""Built-in effects page: the controller's own effects, as groups of ports (the firmware calls them
canvases). Normal lighting runs all the time; the startup animation plays once at power-on.
Changes are written to the controller as you make them (only what changed), with each port's color
balance applied (builtin.py)."""

from dataclasses import dataclass, field, replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSlider, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import builtin, calibration
from .. import protocol as p
from ..protocol import BootIntro, BootSteady, CanvasParams, Mode
from .common import ColorButton, Device, PortPicker, error, hbox

FPS_CHOICES = [(8, "Very smooth (120 fps)"), (16, "Smooth (60 fps)"), (33, "Normal (30 fps)"), (50, "20 fps"),
               (100, "10 fps")]
COLOR_NAMES = {"color1": "Color 1", "color2": "Color 2", "color3": "Color 3"}


GroupState = builtin.Group


@dataclass
class EffectsState:
    """Everything this page controls, as on the controller."""
    loop: list[GroupState] = field(default_factory=list)
    boot: list[GroupState] = field(default_factory=list)
    loop_ms: int = 33
    boot_ms: int = 16
    startup_on: bool = True
    builtin_active: bool = True


def _slider(lo: int, hi: int, value: int) -> tuple[QWidget, QSlider]:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(max(lo, min(hi, value)))
    s.setMinimumWidth(160)
    s.setMaximumWidth(460)
    lab = QLabel(str(s.value()))
    lab.setMinimumWidth(28)
    s.valueChanged.connect(lambda v: lab.setText(str(v)))
    return hbox(s, lab, stretch=False), s


class GroupCard(QFrame):
    """One group of ports with one built-in effect. Shows only the settings that effect uses."""

    changed = Signal()
    remove = Signal(object)
    ports_changed = Signal()

    def __init__(self, boot: bool, g: GroupState, labels: dict[int, str], gains: dict):
        super().__init__()
        self.boot, self.base, self.gains = boot, g.params, gains
        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)

        head = QHBoxLayout()
        self.title = QLabel()
        self.effect = QComboBox()
        self.effect.addItems(p.START_EFFECTS if boot else p.EFFECTS)
        self.effect.setCurrentIndex(min(g.params.effect, self.effect.count() - 1))
        self.effect.setMinimumWidth(180)
        rm = QPushButton("Remove group")
        rm.clicked.connect(lambda: self.remove.emit(self))
        head.addWidget(self.title)
        head.addWidget(self.effect)
        head.addStretch(1)
        head.addWidget(rm)
        lay.addLayout(head)

        form = QFormLayout()
        self.picker = PortPicker(labels)
        self.picker.set_ports(g.ports)
        self.picker.changed.connect(lambda _: (self.ports_changed.emit(), self.changed.emit(), self._balance_note()))
        form.addRow("Ports", self.picker)
        self.no_balance = QLabel("<small>Color balance can't be applied to this effect: the controller makes its "
                                 "colors itself. Effects with your own colors are balanced.</small>")
        self.no_balance.setWordWrap(True)
        form.addRow("", self.no_balance)
        rng = p.PARAM_RANGES
        self.rows: dict[str, QWidget] = {}
        self.sliders: dict[str, QSlider] = {}
        for key, label, value in (("brightness", "Brightness", g.params.brightness), ("step", "Speed", g.params.step),
                                  ("size", "Size", g.params.size)):
            row, s = _slider(*rng[key], value)
            self.rows[key], self.sliders[key] = row, s
            form.addRow(label, row)
        self.colors = {k: ColorButton(getattr(g.params, k)) for k in ("color1", "color2", "color3")}
        self.color_labels = {k: QLabel(f"<small>{COLOR_NAMES[k]}</small>") for k in self.colors}
        colors_row = QWidget()
        cl = QHBoxLayout(colors_row)
        cl.setContentsMargins(0, 0, 0, 0)
        for k, b in self.colors.items():
            cl.addWidget(b)
            cl.addWidget(self.color_labels[k])
            cl.addSpacing(8)
        cl.addStretch(1)
        self.rows["colors"] = colors_row
        form.addRow("Colors", colors_row)
        self.palette = QComboBox()
        for i in range(p.PALETTE_GROUPS):
            self.palette.addItem(f"Palette {i + 1} (Palettes page)", i)
        self.palette.setCurrentIndex(g.palette & 7)
        self.blend = QCheckBox("Blend smoothly between palette colors")
        self.blend.setChecked(bool(g.blend))
        self.rows["palette"], self.rows["blend"] = self.palette, self.blend
        form.addRow("Palette", self.palette)
        form.addRow("", self.blend)
        lay.addLayout(form)

        self.more_btn = QToolButton()
        self.more_btn.setText("More settings")
        self.more_btn.setCheckable(True)
        self.more_btn.setArrowType(Qt.RightArrow)
        self.more_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more_btn.setAutoRaise(True)
        lay.addWidget(self.more_btn)
        self.more = QWidget()
        mf = QFormLayout(self.more)
        mf.setContentsMargins(16, 0, 0, 0)
        row, self.sliders["delay"] = _slider(*rng["delay"], g.params.delay)
        self.rows["delay"] = row
        mf.addRow("Delay", row)
        row, self.sliders["frame_delay"] = _slider(*rng["frame_delay"], g.frame_delay)
        self.rows["frame_delay"] = row
        mf.addRow("Frame time (ms)", row)
        self.vmap = QCheckBox("Follow each port's LED order (Ports && LEDs → LED order)")
        self.vmap.setChecked(bool(g.params.vmap_process))
        self.rows["vmap"] = self.vmap
        mf.addRow("", self.vmap)
        lay.addWidget(self.more)
        self.more.setVisible(False)
        self.more_btn.toggled.connect(lambda on: (self.more.setVisible(on),
                                                  self.more_btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)))
        self.form, self.more_form = form, mf

        self.effect.currentIndexChanged.connect(self._effect_changed)
        for s in self.sliders.values():
            s.valueChanged.connect(lambda _: self.changed.emit())
        for b in self.colors.values():
            b.changed.connect(lambda _: self.changed.emit())
        self.palette.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.blend.toggled.connect(lambda _: self.changed.emit())
        self.vmap.toggled.connect(lambda _: self.changed.emit())
        self._effect_changed(emit=False)

    def set_index(self, i: int) -> None:
        self.title.setText(f"<b>Group {i + 1}</b>")

    def _show_row(self, form: QFormLayout, w: QWidget, visible: bool) -> None:
        w.setVisible(visible)
        lab = form.labelForField(w)
        if lab:
            lab.setVisible(visible)

    def _effect_changed(self, *_, emit: bool = True) -> None:
        uses = p.EFFECT_PARAMS.get(self.effect.currentText(), set())
        for key in ("brightness", "step", "size", "palette", "blend"):
            self._show_row(self.form, self.rows[key], key in uses)
        for key in ("delay", "frame_delay", "vmap"):
            self._show_row(self.more_form, self.rows[key], key in uses)
        used_colors = [k for k in self.colors if k in uses]
        for k in self.colors:
            self.colors[k].setVisible(k in uses)
            self.color_labels[k].setVisible(k in uses and (len(used_colors) > 1 or k != "color1"))
        self._show_row(self.form, self.rows["colors"], bool(used_colors))
        self.more_btn.setVisible(bool(uses & {"delay", "frame_delay", "vmap"}))
        self._balance_note()
        if emit:
            self.changed.emit()

    def _balance_note(self) -> None:
        """Say so when a port here has a color balance that this effect can't use."""
        params = replace(self.base, effect=self.effect.currentIndex())
        needs = any(self.gains.get(pt) for pt in self.picker.ports())
        self.no_balance.setVisible(needs and not builtin.balanceable(params, self.boot))

    def value(self) -> GroupState:
        s = self.sliders
        params = replace(self.base, effect=self.effect.currentIndex(), brightness=s["brightness"].value(),
                         step=s["step"].value(), size=s["size"].value(), delay=s["delay"].value(),
                         vmap_process=int(self.vmap.isChecked()), color1=self.colors["color1"].color(),
                         color2=self.colors["color2"].color(), color3=self.colors["color3"].color())
        return GroupState(self.picker.ports(), params, s["frame_delay"].value(), self.palette.currentData(),
                          int(self.blend.isChecked()))


class GroupList(QWidget):
    """The groups of one kind (normal lighting or startup animation)."""

    changed = Signal()

    def __init__(self, boot: bool, labels: dict[int, str], gains: dict):
        super().__init__()
        self.boot, self.labels, self.gains = boot, labels, gains
        self.cards: list[GroupCard] = []
        lay = QVBoxLayout(self)
        self.box = QVBoxLayout()
        lay.addLayout(self.box)
        self.add_btn = QPushButton("+ Add a group")
        self.add_btn.clicked.connect(lambda: self.add(GroupState([], CanvasParams(vmap_process=1)), emit=True))
        self.free = QLabel()
        self.free_btn = QPushButton("Put them in a new group")
        self.free_btn.clicked.connect(self._group_free)
        lay.addWidget(hbox(self.add_btn))
        lay.addWidget(hbox(self.free, self.free_btn))
        self.fps = QComboBox()
        for ms, label in FPS_CHOICES:
            self.fps.addItem(label, ms)
        self.fps.currentIndexChanged.connect(lambda _: self.changed.emit())
        lay.addWidget(hbox("Animation frame rate", self.fps))
        lay.addStretch(1)

    def set(self, groups: list[GroupState], frame_ms: int) -> None:
        for c in list(self.cards):
            self._remove(c, emit=False)
        for g in groups:
            self.add(g, emit=False)
        self.fps.blockSignals(True)
        i = self.fps.findData(frame_ms)
        if i < 0:
            self.fps.addItem(f"{1000 / max(frame_ms, 1):.0f} fps", frame_ms)
            i = self.fps.count() - 1
        self.fps.setCurrentIndex(i)
        self.fps.blockSignals(False)
        self._sync()

    def add(self, g: GroupState, emit: bool = True) -> None:
        limit = p.BOOT_CANVAS_MAX if self.boot else p.LOOP_CANVAS_MAX
        if len(self.cards) >= limit:
            return error(self, "Groups", f"The controller allows at most {limit} groups here.")
        card = GroupCard(self.boot, g, self.labels, self.gains)
        card.remove.connect(self._remove)
        card.ports_changed.connect(self._sync)
        card.changed.connect(self.changed)
        self.cards.append(card)
        self.box.addWidget(card)
        self._sync()
        if emit:
            self.changed.emit()

    def _remove(self, card: GroupCard, emit: bool = True) -> None:
        self.cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._sync()
        if emit:
            self.changed.emit()

    def _sync(self) -> None:
        """Number the groups, keep each port in one group, show which ports are left over."""
        for i, card in enumerate(self.cards):
            card.set_index(i)
            others = {pt for c in self.cards if c is not card for pt in c.picker.ports()}
            card.picker.set_ports(card.picker.ports(), others)
        used = {pt for c in self.cards for pt in c.picker.ports()}
        free = [pt for pt in range(1, p.NUM_CHANNELS + 1) if pt not in used]
        named = [f"{pt} ({self.labels[pt]})" if self.labels.get(pt) else str(pt) for pt in free]
        self.free.setText(f"<small>Ports in no group stay dark: {', '.join(named)}</small>" if free else "")
        self.free_btn.setVisible(bool(free) and len(self.cards) < (p.BOOT_CANVAS_MAX if self.boot else p.LOOP_CANVAS_MAX))
        self.add_btn.setEnabled(len(self.cards) < (p.BOOT_CANVAS_MAX if self.boot else p.LOOP_CANVAS_MAX))

    def _group_free(self) -> None:
        used = {pt for c in self.cards for pt in c.picker.ports()}
        named = [pt for pt in range(1, p.NUM_CHANNELS + 1) if pt not in used and self.labels.get(pt)]
        free = named or [pt for pt in range(1, p.NUM_CHANNELS + 1) if pt not in used]
        self.add(GroupState(free, CanvasParams(vmap_process=1)))

    def value(self) -> tuple[list[GroupState], int]:
        return [c.value() for c in self.cards if c.picker.ports()], self.fps.currentData()


class CanvasPage(QWidget):
    title = "Built-in effects"
    icon = "preferences-desktop-color"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        self.labels: dict[int, str] = {}
        self.gains: dict = {}                          # color balance per port (for the cards' notes)
        self.state: EffectsState | None = None        # what the controller has (last read / written)
        outer = QVBoxLayout(self)
        intro = QLabel(
            "Effects that run <b>on the controller itself</b>. Put ports into <b>groups</b> and give each group an "
            "effect. Changes show on your fans right away and stay on the controller, even with the PC off. "
            "Colors are adjusted by each port's color balance (Ports & LEDs).")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.banner = QFrame()
        self.banner.setStyleSheet("QFrame#banner { background: palette(alternate-base); border-radius: 6px; }")
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        use = QPushButton("Use built-in effects")
        use.clicked.connect(self._activate)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(use)
        outer.addWidget(self.banner)
        self.banner.hide()

        self.tabs = QTabWidget()
        self.loop = GroupList(False, self.labels, self.gains)
        self.boot = GroupList(True, self.labels, self.gains)
        self.tabs.addTab(self._scroll(self.loop), "Normal lighting")
        boot_tab = QWidget()
        btl = QVBoxLayout(boot_tab)
        self.startup = QCheckBox("Play a startup animation when the PC turns on (once, then normal lighting)")
        play = QPushButton("▶ Play it now")
        play.clicked.connect(lambda: self.device.run(lambda d: d.play_boot_canvas()))
        btl.addWidget(hbox(self.startup, play))
        btl.addWidget(self._scroll(self.boot), 1)
        self.tabs.addTab(boot_tab, "Startup animation")
        outer.addWidget(self.tabs, 1)
        self.status = QLabel()
        outer.addWidget(hbox(self.status))

        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(600)
        self._apply_timer.timeout.connect(self._apply)
        self.loop.changed.connect(self._schedule)
        self.boot.changed.connect(self._schedule)
        self.startup.toggled.connect(self._startup_toggled)
        self._loaded = False

    @staticmethod
    def _scroll(w: QWidget) -> QScrollArea:
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setFrameShape(QFrame.NoFrame)
        s.setWidget(w)
        return s

    def set_labels(self, labels: dict[int, str]) -> None:
        self.labels.clear()
        self.labels.update(labels)

    def showEvent(self, e):
        # Read again each time: other pages (studio save, color balance) change the controller's groups too, and
        # edits here are written as differences from what was read.
        if not self._apply_timer.isActive():
            self.refresh()
        super().showEvent(e)

    # --- read ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        def load(d):
            def groups(boot):        # your groups (canvases split for color balance are shown as one)
                return builtin.groups(d, builtin.read(d, boot), boot)
            intro, steady = d.boot_playlist()
            mode = d.mode()
            boot = groups(True)
            return EffectsState(groups(False), boot, d.frame_ms(False), d.frame_ms(True),
                                intro == BootIntro.START_CANVAS and bool(boot),   # "on" = something will play
                                steady == BootSteady.HW_EFFECTS and mode != Mode.FSEQ), calibration.load()
        self.device.run(load, lambda r: self._show(*r))

    def _show(self, st: EffectsState, gains: dict) -> None:
        self.gains.clear()
        self.gains.update(gains)
        self.state = st
        self.loop.set(st.loop, st.loop_ms)
        self.boot.set(st.boot, st.boot_ms)
        self.startup.blockSignals(True)
        self.startup.setChecked(st.startup_on)
        self.startup.blockSignals(False)
        self.boot.setEnabled(st.startup_on)
        self.banner_text.setText(
            "Your controller is showing a <b>saved animation</b> from the Effects studio, so these effects aren't "
            "active. Changes here are kept, and take over when you switch.")
        self.banner.setVisible(not st.builtin_active)
        self.status.setText("")
        self._loaded = True

    # --- write --------------------------------------------------------------------------------

    def _schedule(self) -> None:
        if self._loaded:
            self.status.setText("Saving…")
            self._apply_timer.start()

    def _startup_toggled(self, on: bool) -> None:
        self.boot.setEnabled(on)
        if on and not self.boot.cards:
            self.boot.add(GroupState([pt for pt in range(1, p.NUM_CHANNELS + 1) if self.labels.get(pt)] or
                                     list(range(1, p.NUM_CHANNELS + 1)), CanvasParams(effect=0, vmap_process=1)),
                          emit=False)
        self._schedule()

    def _wanted(self) -> EffectsState:
        (loop, loop_ms), (boot, boot_ms) = self.loop.value(), self.boot.value()
        cur = self.state
        return EffectsState(loop, boot, loop_ms, boot_ms, self.startup.isChecked(), cur.builtin_active)

    def _apply(self, activate: bool = False) -> None:
        if self.state is None:
            return
        want, have = self._wanted(), self.state

        def write(d):
            gains = calibration.load()
            complete = True
            for boot, groups in ((False, want.loop), (True, want.boot)):
                complete &= builtin.write(d, groups, gains, boot)[1]
            if want.loop_ms != have.loop_ms:
                d.set_frame_ms(want.loop_ms, boot=False)
            if want.boot_ms != have.boot_ms:
                d.set_frame_ms(want.boot_ms, boot=True)
            intro = BootIntro.START_CANVAS if want.startup_on and want.boot else BootIntro.NONE
            active = want.builtin_active or activate
            current_steady = d.boot_playlist()[1]
            steady = BootSteady.HW_EFFECTS if active else current_steady
            if (intro, steady) != tuple(d.boot_playlist()):
                d.set_boot_playlist(intro, steady)
            if active:
                d.set_mode(Mode.HARDWARE)          # also stops a stored animation instantly
            return replace(want, builtin_active=active), complete

        def done(result) -> None:
            self.state, complete = result
            self.banner.setVisible(not self.state.builtin_active)
            self.status.setText("✓ Saved on the controller" + ("" if complete else
                                " (too many groups to split for color balance: some ports aren't balanced)"))

        self.device.run(write, done, lambda e: (self.status.setText(""), error(self, "Built-in effects", e)))

    def _activate(self) -> None:
        self._apply(activate=True)
