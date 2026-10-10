"""Palettes page: the controller's 8 palette groups of 16 colors (used by palette-based built-in effects)."""

import json

from PySide6.QtWidgets import QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import protocol as p
from .common import ColorButton, Device, error, hexcolor


class PalettePage(QWidget):
    title = "Palettes"
    icon = "color-management"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Palette groups used by the controller's palette effects (e.g. <i>ColorFromPalette</i>)."))
        grid_box = QGroupBox("Groups")
        grid = QGridLayout(grid_box)
        self.buttons: list[list[ColorButton]] = []
        for g in range(p.PALETTE_GROUPS):
            grid.addWidget(QLabel(f"Group {g + 1}"), g, 0)
            row = []
            for i in range(p.PALETTE_SIZE):
                b = ColorButton((0, 0, 0))
                grid.addWidget(b, g, i + 1)
                row.append(b)
            self.buttons.append(row)
        lay.addWidget(grid_box)
        row = QHBoxLayout()
        for text, slot in (("Reload", self.refresh), ("Apply to controller", self._apply),
                           ("Export…", self._export), ("Import…", self._import)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self._loaded = False

    def showEvent(self, e):
        if not self._loaded:
            self.refresh()
        super().showEvent(e)

    def _get(self) -> list[list[tuple]]:
        return [[b.color() for b in row] for row in self.buttons]

    def _set(self, palettes) -> None:
        for row, colors in zip(self.buttons, palettes):
            for b, c in zip(row, colors):
                b.set_color(tuple(c))

    def refresh(self) -> None:
        def show(pals):
            self._set(pals)
            self._loaded = True
        self.device.run(lambda d: [d.palette(g) for g in range(p.PALETTE_GROUPS)], show)

    def _apply(self) -> None:
        pals = self._get()
        def write(d):
            for g, colors in enumerate(pals):
                if d.palette(g) != colors:
                    d.set_palette(g, colors)
        self.device.run(write, lambda _: self.device.status.emit("Palettes stored"), lambda e: error(self, "Palettes", e))

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
