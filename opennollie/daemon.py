"""OpenNollie daemon: owns the device, hosts live effects, and serves clients on a Unix socket.

Clients (CLI, GUI) send raw device packets through it, so all device code works unchanged on top of
`client.DaemonTransport`; live effects run here so they survive the client closing.

Wire format: one JSON object per line. Request {"op": ..., ...} → reply {"ok": true, ...} or
{"ok": false, "error": "..."}. Packets are base64.

    python3 -m opennollie.daemon            # usually started by the systemd user unit
"""

import base64
import json
import logging
import os
import signal
import socket
import threading
import time
import tomllib
from pathlib import Path

from . import engine, layouts, profile
from . import protocol as p
from .device import DeviceNotFound, HidTransport, NoResponse, Nollie16

log = logging.getLogger("opennollie.daemon")

SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/opennollie-{os.getuid()}")) / "opennollie.sock"
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opennollie" / "daemon.toml"
VERSION = 1


class ManagedTransport:
    """A transport that (re)connects on demand and drops the handle when the device goes away
    (unplug, controller reboot), so the next call reconnects."""

    def __init__(self):
        self._t: HidTransport | None = None
        self._lock = threading.Lock()

    def _get(self) -> HidTransport:
        with self._lock:
            if self._t is None:
                self._t = HidTransport()
                log.info("device connected: %s", self._t.path)
            return self._t

    def _dropped(self) -> None:
        with self._lock:
            if self._t is not None:
                try:
                    self._t.close()
                except Exception:
                    pass
                self._t = None
                log.warning("device disconnected")

    @property
    def connected(self) -> bool:
        return self._t is not None

    def _do(self, fn):
        t = self._get()
        try:
            return fn(t)
        except OSError as e:
            if not isinstance(e, NoResponse):
                self._dropped()
            raise

    def send_many(self, packets) -> None:
        self._do(lambda t: t.send_many(packets))

    def query(self, packet: bytes, timeout_ms: int = 200) -> bytes:
        return self._do(lambda t: t.query(packet, timeout_ms))

    @property
    def serial(self) -> str:
        return self._do(lambda t: t.serial)

    def close(self) -> None:
        self._dropped()


class LiveRunner:
    """Runs one profile live in a thread; can be paused while a client drives the LEDs itself."""

    def __init__(self, transport: ManagedTransport):
        self.dev = Nollie16(transport=transport)
        self.prof: profile.Profile | None = None
        self.source = ""
        self.started = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pauses = 0
        self._pause_lock = threading.Lock()
        self._resumed = threading.Event()
        self._resumed.set()

    def start(self, text: str, source: str = "") -> profile.Profile:
        prof = profile.loads(text)
        engine.Renderer(prof)                # validate before stopping what's running
        self.stop()
        self.prof, self.source, self.started = prof, source, time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(prof, self._stop), daemon=True)
        self._thread.start()
        log.info("live: started %r", prof.name)
        return prof

    def stop(self) -> None:
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=2)
            log.info("live: stopped %r", self.prof.name if self.prof else "")
        self._thread, self.prof = None, None

    def pause(self) -> None:
        with self._pause_lock:
            self._pauses += 1
            self._resumed.clear()

    def resume(self) -> None:
        with self._pause_lock:
            self._pauses = max(0, self._pauses - 1)
            if not self._pauses:
                self._resumed.set()

    def status(self) -> dict:
        return {"running": self._thread is not None, "paused": self._pauses > 0,
                "name": self.prof.name if self.prof else None, "source": self.source,
                "since": self.started if self._thread else None}

    def _run(self, prof: profile.Profile, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self._resumed.is_set():
                stop.wait(0.1)
                continue
            try:
                resolved = prof.with_device_leds(*layouts.read_ports(self.dev))
                pause_or_stop = threading.Event()
                watcher = threading.Thread(target=self._watch, args=(stop, pause_or_stop), daemon=True)
                watcher.start()
                engine.run_live(self.dev, resolved, pause_or_stop)
            except (OSError, DeviceNotFound) as e:
                log.warning("live: %s; retrying", e)
                stop.wait(1.0)

    def _watch(self, stop: threading.Event, out: threading.Event) -> None:
        while not stop.wait(0.05):
            if not self._resumed.is_set():
                break
        out.set()


class Server:
    def __init__(self, path: Path = SOCKET_PATH):
        self.path = path
        self.transport = ManagedTransport()
        self.live = LiveRunner(self.transport)
        self._sock: socket.socket | None = None

    # --- request handling ---------------------------------------------------------------------

    def handle(self, req: dict, conn_state: dict) -> dict:
        op = req.get("op")
        if op == "ping":
            return {"version": VERSION, "device": self.transport.connected, "live": self.live.status()}
        if op == "serial":
            return {"serial": self.transport.serial}
        if op == "query":
            data = self.transport.query(base64.b64decode(req["data"]), int(req.get("timeout", 200)))
            return {"data": base64.b64encode(data).decode()}
        if op == "send":
            self.transport.send_many([base64.b64decode(x) for x in req["packets"]])
            return {}
        if op == "live_start":
            prof = self.live.start(req["profile"], req.get("source", ""))
            return {"name": prof.name}
        if op == "live_stop":
            self.live.stop()
            return {}
        if op == "live_status":
            return self.live.status()
        if op == "live_pause":
            self.live.pause()
            conn_state["pauses"] += 1
            return {}
        if op == "live_resume":
            if conn_state["pauses"]:
                conn_state["pauses"] -= 1
                self.live.resume()
            return {}
        raise ValueError(f"unknown op {op!r}")

    def _serve_conn(self, conn: socket.socket) -> None:
        state = {"pauses": 0}
        with conn, conn.makefile("rwb") as f:
            for line in f:
                try:
                    reply = {"ok": True} | self.handle(json.loads(line), state)
                except Exception as e:             # report every failure to the client
                    reply = {"ok": False, "error": str(e) or type(e).__name__, "type": type(e).__name__}
                f.write(json.dumps(reply).encode() + b"\n")
                f.flush()
        for _ in range(state["pauses"]):           # client went away while paused
            self.live.resume()

    def serve_forever(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists():
            try:                                   # another daemon alive?
                socket.socket(socket.AF_UNIX).connect(str(self.path))
                raise SystemExit(f"another daemon is already listening on {self.path}")
            except ConnectionRefusedError:
                self.path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            self._sock.bind(str(self.path))
        finally:
            os.umask(old)
        self._sock.listen(8)
        log.info("listening on %s", self.path)
        self._autostart()
        while True:
            conn, _ = self._sock.accept()
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _autostart(self) -> None:
        if not CONFIG_PATH.exists():
            return
        cfg = tomllib.loads(CONFIG_PATH.read_text())
        path = cfg.get("live_profile")
        if path:
            try:
                self.live.start(Path(path).expanduser().read_text(), str(path))
            except Exception as e:
                log.error("autostart of %s failed: %s", path, e)

    def shutdown(self, *_) -> None:
        self.live.stop()
        if self.path.exists():
            self.path.unlink()
        raise SystemExit(0)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    server = Server()
    signal.signal(signal.SIGTERM, server.shutdown)
    signal.signal(signal.SIGINT, server.shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
