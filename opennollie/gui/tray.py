"""System tray icon: switch profiles (live, or saved to the controller), stop live effects, open the app.

    opennollie-tray            (or: python3 -m opennollie.gui.tray)

Live effects run in the OpenNollie daemon, so they keep going if the tray quits.
"""

import math
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import client, engine, layouts, profile


def ring_icon(size: int = 64) -> QIcon:
    """A ring of rainbow LEDs."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    n, r, dot = 8, size * 0.36, size * 0.11
    for i in range(n):
        a = -math.pi / 2 + 2 * math.pi * i / n
        qp.setPen(Qt.NoPen)
        qp.setBrush(QColor.fromHsvF(i / n, 0.9, 1.0))
        qp.drawEllipse(size / 2 + math.cos(a) * r - dot, size / 2 + math.sin(a) * r - dot, 2 * dot, 2 * dot)
    qp.end()
    return QIcon(pm)


class Tray(QObject):
    _note = Signal(str, str)       # title, message (from worker threads)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.icon = QSystemTrayIcon(ring_icon())
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._build)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self._note.connect(lambda t, m: self.icon.showMessage(t, m, ring_icon(), 5000))
        self._busy = False
        self._update_tooltip()
        self.icon.show()

    # --- state --------------------------------------------------------------------------------

    def _live_status(self) -> dict | None:
        t = client.daemon_transport()
        if not t:
            return None
        try:
            return t.call("live_status")
        except Exception:
            return None
        finally:
            t.close()

    def _update_tooltip(self) -> str:
        st = self._live_status()
        if st is None:
            text = "OpenNollie service not running"
        elif st.get("running"):
            text = f"Live: {st['name']}" + (" (paused)" if st.get("paused") else "")
        else:
            text = "Stored lighting (no live effect)"
        self.icon.setToolTip(f"OpenNollie\n{text}")
        return text

    # --- menu ---------------------------------------------------------------------------------

    def _build(self) -> None:
        self.menu.clear()
        status = QAction(self._update_tooltip(), self.menu)
        status.setEnabled(False)
        self.menu.addAction(status)
        self.menu.addSeparator()
        profiles = profile.available()
        live = self.menu.addMenu("Run live")
        save = self.menu.addMenu("Save to device")
        for path in profiles:
            try:
                label = profile.load(path).name
            except Exception:
                label = path.stem
            live.addAction(label, lambda p=path: self._run_live(p))
            save.addAction(label, lambda p=path: self._save(p))
        if not profiles:
            for m in (live, save):
                m.addAction("No profiles yet: make one in the Effects studio").setEnabled(False)
        stop = self.menu.addAction("Stop live effect", self._stop_live)
        stop.setEnabled(bool((self._live_status() or {}).get("running")))
        self.menu.addSeparator()
        self.menu.addAction("Open OpenNollie", self._open_app)
        self.menu.addAction("Quit tray", self.app.quit)

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._open_app()

    # --- actions ------------------------------------------------------------------------------

    def _run_live(self, path) -> None:
        t = client.daemon_transport()
        if not t:
            return self._note.emit("OpenNollie", "Live effects need the OpenNollie service:\n"
                                                 "systemctl --user start opennollie")
        try:
            name = t.call("live_start", profile=path.read_text(), source=str(path))["name"]
            self._note.emit("OpenNollie", f"Live: {name}")
        except Exception as e:
            self._note.emit("OpenNollie: live effect failed", str(e))
        finally:
            t.close()
        self._update_tooltip()

    def _stop_live(self) -> None:
        t = client.daemon_transport()
        if t:
            try:
                t.call("live_stop")
            finally:
                t.close()
        self._update_tooltip()

    def _save(self, path) -> None:
        if self._busy:
            return self._note.emit("OpenNollie", "Still saving the previous profile…")
        self._busy = True

        def work():
            try:
                with client.connect() as dev:
                    if isinstance(dev.transport, client.DaemonTransport):
                        dev.transport.call("live_stop")        # stored lighting takes over
                    prof = engine.prepare(dev, profile.load(path))
                    msg = engine.save_to_device(dev, prof)
                self._note.emit("OpenNollie", f"'{prof.name}' {msg}")
            except Exception as e:
                self._note.emit("OpenNollie: save failed", str(e))
            finally:
                self._busy = False
        self._note.emit("OpenNollie", f"Saving {path.stem} to the controller…")
        threading.Thread(target=work, daemon=True).start()

    def _open_app(self) -> None:
        subprocess.Popen([sys.executable, "-m", "opennollie.gui"], start_new_session=True)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OpenNollie")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        sys.exit("no system tray available")
    tray = Tray(app)              # noqa: F841 (kept alive for the app's lifetime)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
