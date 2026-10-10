"""Settings page: what starts at login, where things are kept."""

import subprocess

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import autostart, calibration, profile
from .common import Device, error, hbox


class SettingsPage(QWidget):
    title = "Settings"
    icon = "configure"

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        lay = QVBoxLayout(self)

        start = QGroupBox("When you log in")
        sl = QVBoxLayout(start)
        self.tray = QCheckBox("Show the OpenNollie tray icon")
        sl.addWidget(self.tray)
        sl.addWidget(self._note("Quick access to your profiles from the panel. The app window never opens by itself."))
        self.service = QCheckBox("Run the background service")
        sl.addWidget(self.service)
        sl.addWidget(self._note("Needed for live effects (Preview on device, Run live) and the tray. Lighting you "
                                "saved to the controller runs without it. Turning it off also stops it now."))
        self.tray.toggled.connect(self._set_tray)
        self.service.toggled.connect(self._set_service)
        lay.addWidget(start)

        files = QGroupBox("Your files")
        fl = QFormLayout(files)
        for label, path in (("Profiles", profile.USER_PROFILES), ("Color balance", calibration.PATH.parent)):
            b = QPushButton("Open folder")
            b.clicked.connect(lambda _=False, p=path: (p.mkdir(parents=True, exist_ok=True),
                                                      QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))))
            fl.addRow(label, hbox(QLabel(str(path)), b))
        lay.addWidget(files)
        lay.addStretch(1)

    @staticmethod
    def _note(text: str) -> QLabel:
        lab = QLabel(f"<small>{text}</small>")
        lab.setWordWrap(True)
        lab.setContentsMargins(24, 0, 0, 6)
        return lab

    def showEvent(self, e):
        for box, value in ((self.tray, autostart.tray_enabled()), (self.service, autostart.service_enabled())):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.service.setEnabled(autostart.service_installed())
        super().showEvent(e)

    def _set_tray(self, on: bool) -> None:
        try:
            autostart.set_tray(on)
            self.device.status.emit("The tray icon will " + ("" if on else "not ") + "start at login")
        except OSError as e:
            error(self, "Settings", e)

    def _set_service(self, on: bool) -> None:
        try:
            autostart.set_service(on)
            self.device.status.emit("Background service " + ("enabled and started" if on else "disabled and stopped"))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            error(self, "Settings", e)
            self.service.blockSignals(True)
            self.service.setChecked(autostart.service_enabled())
            self.service.blockSignals(False)
