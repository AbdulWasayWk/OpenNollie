"""OpenNollie main window."""

import sys

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QWidget

from .animation_page import AnimationPage
from .canvas_page import CanvasPage
from .common import Device
from .device_page import DevicePage
from .palette_page import PalettePage
from .ports_page import PortsPage
from .settings_page import SettingsPage
from .studio_page import StudioPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OpenNollie")
        self.resize(1280, 820)
        self.device = Device()
        self.device.status.connect(lambda msg: self.statusBar().showMessage(msg, 0 if msg.startswith("Error") else 8000))

        self.ports = PortsPage(self.device)
        self.studio = StudioPage(self.device, self.ports.led_counts, self.ports.orders)
        self.canvases = CanvasPage(self.device)
        pages = [self.studio, self.canvases, self.ports, PalettePage(self.device), AnimationPage(self.device),
                 DevicePage(self.device), SettingsPage(self.device)]
        self.ports.labels_changed.connect(self.studio.set_labels)
        self.ports.labels_changed.connect(self.canvases.set_labels)

        self.nav = QListWidget()
        self.nav.setIconSize(QSize(22, 22))
        self.nav.setFixedWidth(200)
        self.stack = QStackedWidget()
        for page in pages:
            item = QListWidgetItem(QIcon.fromTheme(page.icon), page.title.replace("&&", "&"))
            self.nav.addItem(item)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)
        self.ports.refresh()          # loads names + LED counts the other pages use

    def closeEvent(self, e):
        self.studio.autosave()
        self.device.shutdown()
        super().closeEvent(e)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenNollie")
    app.setDesktopFileName("opennollie")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
