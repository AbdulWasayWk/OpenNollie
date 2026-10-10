"""Stored animation page: what's on the controller's flash, playback, and uploading FSEQ files."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QProgressDialog,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import fseq
from .. import protocol as p
from ..flash import Flash
from .common import Device, error, hbox


class AnimationPage(QWidget):
    title = "Stored animation"
    icon = "media-playback-start"
    progress = Signal(str, int, int)

    def __init__(self, device: Device):
        super().__init__()
        self.device = device
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "The controller stores one animation in its flash and can play it at power-on (see <i>Device → Startup</i>). "
            "<b>Effects studio → Save to device</b> renders your effects into it; here you can play it or upload an "
            "FSEQ file made elsewhere (xLights: uncompressed v2, color order <b>GRB</b>)."))
        st = QGroupBox("On the controller")
        f = QFormLayout(st)
        self.state = {k: QLabel("—") for k in ("stored", "playing", "map")}
        f.addRow("Stored", self.state["stored"])
        f.addRow("Playback", self.state["playing"])
        f.addRow("Channel map", self.state["map"])
        self.loop = QCheckBox("Loop")
        self.loop.setChecked(True)
        play, stop, refresh = QPushButton("Play"), QPushButton("Stop"), QPushButton("Refresh")
        play.clicked.connect(lambda: self.device.run(lambda d: Flash(d).play(self.loop.isChecked()),
                                                     lambda ok: (self.refresh(), None if ok else
                                                                 error(self, "Play", "The controller didn't start playback."))))
        stop.clicked.connect(lambda: self.device.run(lambda d: Flash(d).stop(),
                                                     lambda _: self.refresh()))
        refresh.clicked.connect(self.refresh)
        f.addRow("", hbox(play, stop, self.loop, refresh))
        lay.addWidget(st)

        up = QGroupBox("Upload an FSEQ file")
        uf = QFormLayout(up)
        self.file_label = QLabel("No file")
        pick = QPushButton("Choose file…")
        pick.clicked.connect(self._pick)
        uf.addRow("File", hbox(self.file_label, pick))
        uf.addRow(QLabel("LEDs per port in the file's order (ports with 0 aren't in the file):"))
        self.map_spins: dict[int, QSpinBox] = {}
        grid = QHBoxLayout()
        for port in range(1, p.NUM_CHANNELS + 1):
            col = QVBoxLayout()
            col.addWidget(QLabel(str(port)))
            s = QSpinBox()
            s.setRange(0, p.LEDS_PER_CHANNEL)
            s.valueChanged.connect(self._validate)
            col.addWidget(s)
            grid.addLayout(col)
            self.map_spins[port] = s
        uf.addRow(grid)
        self.check = QLabel()
        self.upload_btn = QPushButton("Upload && play")
        self.upload_btn.clicked.connect(self._upload)
        uf.addRow(self.check)
        uf.addRow(hbox(self.upload_btn))
        lay.addWidget(up)
        lay.addStretch(1)
        self.data: bytes | None = None
        self.hdr = None
        self.progress.connect(self._on_progress)
        self._validate()

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)

    def refresh(self) -> None:
        def load(d):
            f = Flash(d)
            return f.fseq_info(), f.play_status(), f.fseq_led_map(), d.mode()
        def show(r):
            info, (playing, pos, code), led_map, mode = r
            self.state["stored"].setText(f"{info.size / 1024:.0f} KiB" if info.present else "nothing stored")
            self.state["playing"].setText(f"playing (frame {pos})" if playing else f"stopped (mode: {mode.name.lower()})")
            ports = sorted((p.CH_TO_PORT[ch], n) for ch, n in enumerate(led_map) if n)
            self.state["map"].setText(", ".join(f"port {pt}: {n}" for pt, n in ports) or "—")
        self.device.run(load, show)

    def _led_map(self) -> list[int]:
        m = [0] * p.NUM_CHANNELS
        for port, s in self.map_spins.items():
            m[p.PORT_TO_CH[port - 1]] = s.value()
        return m

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "FSEQ file", "", "FSEQ (*.fseq)")
        if not path:
            return
        try:
            data = open(path, "rb").read()
            self.hdr = fseq.parse_header(data)
        except Exception as e:
            self.data = self.hdr = None
            return error(self, "FSEQ", e)
        self.data = data
        self.file_label.setText(f"{path.rsplit('/', 1)[-1]}: {self.hdr.frame_count} frames, "
                                f"{1000 / self.hdr.step_ms:.0f} fps, {len(self.hdr.ranges) or 1} channel range(s)")
        self._validate()

    def _validate(self) -> None:
        if not self.hdr:
            self.check.setText("")
            self.upload_btn.setEnabled(False)
            return
        why = fseq.led_map_matches(self.hdr, self._led_map())
        self.check.setText("✔ map fits the file" if why is None else f"✘ {why}")
        self.upload_btn.setEnabled(why is None)

    def _upload(self) -> None:
        data, led_map = self.data, self._led_map()
        self.dlg = QProgressDialog("Uploading…", None, 0, 100, self)
        self.dlg.setMinimumDuration(0)
        def job(d):
            f = Flash(d)
            info = f.upload_fseq(data, led_map, lambda ph, a, b: self.progress.emit(ph, a, b))
            f.play(self.loop.isChecked())
            return info
        self.device.run(job, lambda _: (self.dlg.close(), self.refresh()),
                        lambda e: (self.dlg.close(), error(self, "Upload", e)))

    def _on_progress(self, phase: str, done: int, total: int) -> None:
        self.dlg.setLabelText(phase.capitalize() + "…")
        self.dlg.setValue(int(100 * done / max(total, 1)))
