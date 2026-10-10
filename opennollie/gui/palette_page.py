"""Palettes page: the controller's 8 palettes of 16 colors. Only the built-in effect ColorFromPalette uses
them (each group with that effect picks one on the Built-in effects page)."""

import json

from PySide6.QtWidgets import (QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from .. import builtin
from .. import protocol as p
from .common import ColorButton, Device, error, hexcolor

PALETTE_EFFECT = p.EFFECTS.index("ColorFromPalette")


class PalettePage(QWidget):
    title = "Palettes"
    icon = "color-management"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        lay = QVBoxLayout(self)
        intro = QLabel(
            "A palette is a set of 16 colors stored on the controller. Only one built-in effect uses palettes: "
            "<b>ColorFromPalette</b>. To use one, go to <b>Built-in effects</b>, give a group the effect "
            "<i>ColorFromPalette</i> and pick a palette there; the group then runs through that palette's colors. "
            "Changing a palette here changes every group that uses it.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        grid_box = QGroupBox("Palettes")
        grid = QGridLayout(grid_box)
        self.buttons: list[list[ColorButton]] = []
        self.used: list[QLabel] = []
        for g in range(p.PALETTE_GROUPS):
            grid.addWidget(QLabel(f"Palette {g + 1}"), 2 * g, 0)
            row = []
            for i in range(p.PALETTE_SIZE):
                b = ColorButton((0, 0, 0))
                b.changed.connect(lambda _: self._update_pending())
                grid.addWidget(b, 2 * g, i + 1)
                row.append(b)
            self.buttons.append(row)
            used = QLabel()
            grid.addWidget(used, 2 * g + 1, 1, 1, p.PALETTE_SIZE)
            self.used.append(used)
        grid.setColumnStretch(p.PALETTE_SIZE + 1, 1)        # swatches next to their names, space on the right
        lay.addWidget(grid_box)
        row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply to controller")
        for b, slot in ((QPushButton("Reload"), self.refresh), (self.apply_btn, self._apply),
                        (QPushButton("Export…"), self._export), (QPushButton("Import…"), self._import)):
            b.clicked.connect(slot)
            row.addWidget(b)
        self.pending = QLabel("<span style='color:#d08000'>Changed: not on the controller until you press "
                              "<b>Apply to controller</b>.</span>")
        row.addWidget(self.pending)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self._stored: list[list[tuple]] | None = None       # the palettes as on the controller
        self._update_pending()

    def showEvent(self, e):
        if self._stored is None or self._get() == self._stored:       # (don't throw away unapplied edits)
            self.refresh()
        super().showEvent(e)

    def _get(self) -> list[list[tuple]]:
        return [[tuple(b.color()) for b in row] for row in self.buttons]

    def _set(self, palettes) -> None:
        for row, colors in zip(self.buttons, palettes):
            for b, c in zip(row, colors):
                b.set_color(tuple(c))
        self._update_pending()

    def _update_pending(self) -> None:
        changed = self._stored is not None and self._get() != self._stored
        self.apply_btn.setEnabled(changed)
        self.pending.setVisible(changed)

    def refresh(self) -> None:
        def load(d):
            pals = [[tuple(c) for c in d.palette(g)] for g in range(p.PALETTE_GROUPS)]
            groups = builtin.groups(d, builtin.read(d))
            return pals, [(i, g) for i, g in enumerate(groups) if g.params.effect == PALETTE_EFFECT]
        def show(r):
            pals, users = r
            self._stored = pals
            self._set(pals)
            for g, lab in enumerate(self.used):
                who = [f"group {i + 1}" for i, grp in users if grp.palette & 7 == g]
                lab.setText(f"<small>Used by Normal lighting {', '.join(who)}</small>" if who else
                            "<small style='color:gray'>Not used by any group</small>")
        self.device.run(load, show)

    def _apply(self) -> None:
        pals = self._get()
        def write(d):
            for g, colors in enumerate(pals):
                if [tuple(c) for c in d.palette(g)] != colors:
                    d.set_palette(g, colors)
        def done(_):
            self._stored = pals
            self._update_pending()
            self.device.status.emit("Palettes saved on the controller")
        self.device.run(write, done, lambda e: error(self, "Palettes", e))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export palettes", "palettes.json", "JSON (*.json)")
        if path:
            with open(path, "w") as f:
                json.dump({"palettes": [[hexcolor(c) for c in row] for row in self._get()]}, f, indent=1)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import palettes", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.load(open(path))["palettes"]
            self._set([[tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for h in row] for row in data])
        except Exception as e:
            error(self, "Import", f"Not a palette file: {e}")
