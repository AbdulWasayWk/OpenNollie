"""hidapi transport for the Nollie 16CH."""

import time
from collections.abc import Mapping, Sequence

import hid

from . import protocol
from .protocol import RGB

# OpenRGB pauses this long after the latch packet before the next write.
LATCH_DELAY_S = 0.008


class DeviceNotFound(RuntimeError):
    pass


class Nollie16:
    def __init__(self, path: bytes | None = None):
        if path is None:
            found = hid.enumerate(protocol.VID, protocol.PID)
            if not found:
                raise DeviceNotFound(
                    f"Nollie {protocol.VID:04x}:{protocol.PID:04x} not found. "
                    "If the VM just used it, run tools/setup-dev.sh bind"
                )
            path = found[0]["path"]
        self.path = path
        self._dev = hid.device()
        self._dev.open_path(path)

    def close(self) -> None:
        self._dev.close()

    def __enter__(self) -> "Nollie16":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def serial(self) -> str:
        return self._dev.get_serial_number_string()

    def write(self, packet: bytes) -> None:
        if len(packet) != protocol.OUT_REPORT_LEN:
            raise ValueError(f"packet must be {protocol.OUT_REPORT_LEN} bytes, got {len(packet)}")
        # Leading 0x00 = "no report ID" for hidapi.
        n = self._dev.write(b"\x00" + packet)
        if n < 0:
            raise OSError(f"HID write failed: {self._dev.error()}")

    def send_frame(self, port_colors: Mapping[int, Sequence[RGB]]) -> None:
        """Send one frame. `port_colors` maps 0-based port -> LED colors."""
        for packet in protocol.frame_packets(port_colors):
            self.write(packet)
            if packet[0] == protocol.LATCH_HW_CHANNEL:
                time.sleep(LATCH_DELAY_S)
