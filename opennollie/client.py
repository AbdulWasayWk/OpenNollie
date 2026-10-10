"""Talk to the device through the daemon if it's running, otherwise directly over USB."""

import base64
import json
import socket
import threading
from collections.abc import Sequence

from .daemon import SOCKET_PATH
from .device import DeviceNotFound, NoResponse, Nollie16


class DaemonError(RuntimeError):
    pass


class DaemonTransport:
    """Same interface as device.HidTransport, forwarded over the daemon's socket."""

    def __init__(self, path=SOCKET_PATH):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(str(path))
        self._f = self._sock.makefile("rwb")
        self._lock = threading.Lock()
        self._serial: str | None = None

    def call(self, op: str, **kw) -> dict:
        with self._lock:
            self._f.write(json.dumps({"op": op} | kw).encode() + b"\n")
            self._f.flush()
            line = self._f.readline()
        if not line:
            raise DaemonError("daemon closed the connection")
        reply = json.loads(line)
        if not reply.pop("ok"):
            kind = reply.get("type")
            exc = {"NoResponse": NoResponse, "DeviceNotFound": DeviceNotFound}.get(kind, DaemonError)
            raise exc(reply.get("error", "daemon error"))
        return reply

    def send_many(self, packets: Sequence[bytes]) -> None:
        self.call("send", packets=[base64.b64encode(x).decode() for x in packets])

    def query(self, packet: bytes, timeout_ms: int = 200) -> bytes:
        return base64.b64decode(self.call("query", data=base64.b64encode(packet).decode(), timeout=timeout_ms)["data"])

    @property
    def serial(self) -> str:
        if self._serial is None:
            self._serial = self.call("serial")["serial"]
        return self._serial

    def close(self) -> None:
        self._f.close()
        self._sock.close()


def daemon_transport() -> DaemonTransport | None:
    """A connection to the running daemon, or None."""
    try:
        return DaemonTransport()
    except (FileNotFoundError, ConnectionRefusedError):
        return None


def connect() -> Nollie16:
    """The device: via the daemon when it's running (it owns the USB handle), else directly."""
    t = daemon_transport()
    return Nollie16(transport=t) if t else Nollie16()
