"""Device page: identity, power, startup behaviour, maintenance."""

import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from .. import model
from .. import protocol as p
from ..flash import Flash
from ..model import DeviceState
from ..protocol import BootIntro, BootSteady
from .common import Device, confirm, error, hbox, info

IDLE_CHOICES = [(0, "Default (~3 s)"), (5, "5 s"), (10, "10 s"), (30, "30 s"), (60, "1 min"), (300, "5 min"),
                (1800, "30 min"), (3600, "1 hour"), (p.IDLE_NEVER, "Never")]
INTRO_CHOICES = [(BootIntro.START_CANVAS, "Boot canvases (default)"), (BootIntro.NONE, "None"),
                 (BootIntro.FSEQ_ONCE, "Stored animation, once")]
STEADY_CHOICES = [(BootSteady.HW_EFFECTS, "Built-in effects (default)"), (BootSteady.FSEQ_LOOP, "Stored animation, loop")]


def _combo(choices) -> QComboBox:
    c = QComboBox()
    for value, label in choices:
        c.addItem(label, int(value))
    return c


def _select(combo: QComboBox, value: int) -> None:
    i = combo.findData(int(value))
    if i < 0:
        combo.addItem(f"Custom ({value})", int(value))
        i = combo.count() - 1
    combo.blockSignals(True)
    combo.setCurrentIndex(i)
    combo.blockSignals(False)


class DevicePage(QWidget):
    title = "Device"
    icon = "computer"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        lay = QVBoxLayout(self)

        ident = QGroupBox("Controller")
        f = QFormLayout(ident)
        self.name = QLineEdit()
        self.name.setMaxLength(p.NAME_MAX_CHARS)
        rename = QPushButton("Rename")
        rename.clicked.connect(self._rename)
        f.addRow("Name", hbox(self.name, rename, stretch=False))
        self.fields = {}
        for key, label in (("fw", "Firmware"), ("serial", "Serial"), ("mode", "Mode"), ("writes", "Flash writes"),
                           ("anim", "Stored animation"), ("chip", "Flash chip")):
            self.fields[key] = QLabel("—")
            f.addRow(label, self.fields[key])
        lay.addWidget(ident)

        power = QGroupBox("Power (refreshes every 2 s)")
        pf = QFormLayout(power)
        for key, label in (("v", "Input voltage"), ("a", "Input current"), ("w", "Power"), ("t", "MCU temperature"),
                           ("cpu", "MCU load")):
            self.fields[key] = QLabel("—")
            pf.addRow(label, self.fields[key])
        lay.addWidget(power)

        boot = QGroupBox("Startup && fallback (stored on the device)")
        bf = QFormLayout(boot)
        self.intro, self.steady, self.idle = _combo(INTRO_CHOICES), _combo(STEADY_CHOICES), _combo(IDLE_CHOICES)
        bf.addRow("At power-on, first play", self.intro)
        bf.addRow("Then keep running", self.steady)
        bf.addRow("After live streaming stops, return after", self.idle)
        self.intro.currentIndexChanged.connect(self._set_playlist)
        self.steady.currentIndexChanged.connect(self._set_playlist)
        self.idle.currentIndexChanged.connect(
            lambda: self.device.run(lambda d: d.set_ol_idle(self.idle.currentData())))
        lay.addWidget(boot)

        maint = QGroupBox("Maintenance")
        ml = QHBoxLayout(maint)
        for text, slot in (("Refresh", self.refresh), ("Back up settings…", self._backup),
                           ("Restore settings…", self._restore),
                           ("Restart controller", self._restart), ("Factory reset…", self._reset)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            ml.addWidget(b)
        ml.addStretch(1)
        lay.addWidget(maint)
        lay.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_power)
        self._timer.setInterval(2000)

    # --- data ---------------------------------------------------------------------------------

    def showEvent(self, e):
        self.refresh()
        self._poll_power()
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def refresh(self) -> None:
        def load(d):
            f = Flash(d)
            return (d.serial, d.fw_version(), d.version(), d.name(), d.mode(), d.write_count(),
                    d.boot_playlist(), d.ol_idle(), f.fseq_info(), f.info())
        self.device.run(load, self._show)

    def _show(self, r) -> None:
        serial, fw, (cfg, hw), name, mode, writes, (intro, steady), idle, anim, (chip_ok, chip) = r
        self.name.setText(name)
        self.fields["fw"].setText(f"{fw} (config {cfg}, hardware {hw})")
        self.fields["serial"].setText(serial)
        self.fields["mode"].setText({p.Mode.ONLINE: "Live (host streaming)", p.Mode.HARDWARE: "Built-in effects",
                                     p.Mode.SL: "Busy (storage)", p.Mode.FSEQ: "Stored animation"}.get(mode, str(mode)))
        self.fields["writes"].setText(str(writes))
        self.fields["anim"].setText(f"{anim.size / 1024:.0f} KiB" if anim.present else "none")
        self.fields["chip"].setText(chip if chip_ok else "none")
        _select(self.intro, intro)
        _select(self.steady, steady)
        _select(self.idle, idle)

    def _poll_power(self) -> None:
        def show(v):
            self.fields["v"].setText(f"{v['input_voltage']:.2f} V")
            self.fields["a"].setText(f"{v['input_current']:.2f} A")
            self.fields["w"].setText(f"{v['input_voltage'] * v['input_current']:.1f} W")
            self.fields["t"].setText(f"{v['mcu_temp']:.1f} °C")
            self.fields["cpu"].setText(f"{v['cpu_usage']:.0f} %")
        self.device.run(lambda d: d.voltage(), show, lambda e: None)

    # --- actions ------------------------------------------------------------------------------

    def _rename(self) -> None:
        name = self.name.text().strip()
        self.device.run(lambda d: d.set_name(name), lambda _: self.device.status.emit(f"Renamed to {name!r}"))

    def _set_playlist(self) -> None:
        intro, steady = self.intro.currentData(), self.steady.currentData()
        self.device.run(lambda d: d.set_boot_playlist(intro, steady))

    def _backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Back up controller settings", "nollie-backup.json", "JSON (*.json)")
        if not path:
            return
        def save(state):
            with open(path, "w") as f:
                json.dump(state.to_dict(), f, indent=1)
            info(self, "Backup", f"Saved to {path}")
        self.device.run(lambda d: DeviceState.read(d, with_led_index=True), save, lambda e: error(self, "Backup", e))

    def _restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Restore controller settings", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path) as f:
                target = DeviceState.from_dict(json.load(f))
        except Exception as e:
            return error(self, "Restore", f"Not a backup file: {e}")

        def preview(changes):
            if not changes:
                return info(self, "Restore", "Nothing to change: the controller already matches this backup.")
            note = (f"\n\nThis backup is from controller {target.serial}." if target.serial and
                    target.serial != self.fields["serial"].text() else "")
            if confirm(self, "Restore settings", "These settings will be written to the controller:\n\n• " +
                       "\n• ".join(changes) + note + "\n\nThe stored animation isn't part of a backup."):
                self.device.run(lambda d: model.restore(d, target),
                                lambda done: (info(self, "Restore", f"Restored {len(done)} setting(s)."), self.refresh()),
                                lambda e: error(self, "Restore", e))
        self.device.run(lambda d: model.restore(d, target, dry_run=True), preview, lambda e: error(self, "Restore", e))

    def _restart(self) -> None:
        if confirm(self, "Restart controller", "Restart the controller now? Lighting blinks briefly."):
            self.device.run(lambda d: d.reboot(), lambda _: QTimer.singleShot(4000, self.refresh))

    def _reset(self) -> None:
        if confirm(self, "Factory reset",
                   "Reset all canvases and lighting settings to factory defaults?\n"
                   "The controller name and hardware calibration are kept. The stored animation is not erased."):
            self.device.run(lambda d: d.factory_reset(), lambda _: QTimer.singleShot(4000, self.refresh))
