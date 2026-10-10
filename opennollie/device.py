"""hidapi transport and high-level operations for the Nollie 16CH (fw 2.3.0)."""

import threading
from collections.abc import Mapping, Sequence
from dataclasses import replace

import hid

from . import protocol as p
from .protocol import CanvasLayout, CanvasParams, ConfigItem, CoreItem, EffectItem, Mode, RGB

READ_TIMEOUT_MS = 200


class DeviceNotFound(RuntimeError):
    pass


class NoResponse(IOError):
    pass


def find_path() -> bytes:
    for info in hid.enumerate(p.VID, p.PID):
        if info["interface_number"] == p.INTERFACE:
            return info["path"]
    legacy = hid.enumerate(0x3061, 0x4716)
    hint = " (found legacy firmware 3061:4716; update it with the official NollieRGB app)" if legacy else ""
    raise DeviceNotFound(f"Nollie 16CH {p.VID:04x}:{p.PID:04x} not found{hint}")


class Nollie16:
    """One open Nollie. Thread-safe: each request/response pair holds a lock."""

    def __init__(self, path: bytes | None = None):
        self.path = path or find_path()
        self._dev = hid.device()
        self._dev.open_path(self.path)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._dev.close()

    def __enter__(self) -> "Nollie16":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- transport ----------------------------------------------------------------------------

    def send(self, packet: bytes) -> None:
        with self._lock:
            self._write(packet)

    def query(self, packet: bytes, timeout_ms: int = READ_TIMEOUT_MS) -> bytes:
        """Send a GET and return its 1024-byte reply."""
        with self._lock:
            self._drain()
            self._write(packet)
            data = self._dev.read(p.REPORT_LEN, timeout_ms)
        if not data:
            raise NoResponse(f"no reply to {packet[:3].hex(' ')}")
        return bytes(data)

    def _write(self, packet: bytes) -> None:
        if len(packet) != p.REPORT_LEN:
            raise ValueError(f"packet must be {p.REPORT_LEN} bytes, got {len(packet)}")
        if self._dev.write(b"\x00" + packet) < 0:     # leading 0x00 = no report ID
            raise OSError(f"HID write failed: {self._dev.error()}")

    def _drain(self) -> None:
        # hidapi treats timeout 0 as "block forever", so poll with 1 ms.
        while self._dev.read(p.REPORT_LEN, 1):
            pass

    # --- identity & status --------------------------------------------------------------------

    @property
    def serial(self) -> str:
        return self._dev.get_serial_number_string()

    def version(self) -> tuple[int, int]:
        return p.parse_version(self.query(p.get_config(ConfigItem.VERSION)))

    def fw_version(self) -> str:
        return p.parse_fw_version(self.query(p.get_config(ConfigItem.FW_VERSION)))

    def voltage(self) -> dict[str, float]:
        return p.parse_voltage(self.query(p.get_config(ConfigItem.VOLTAGE)))

    def write_count(self) -> int:
        return p.parse_u32le(self.query(p.get_core(CoreItem.WRITE_COUNT)))

    def name(self) -> str:
        n = p.parse_u32le(self.query(p.get_core(CoreItem.NAME_LEN)))
        return p.parse_utf16le(self.query(p.get_core(CoreItem.NAME))[:n]) if n else ""

    # --- effect engine state ------------------------------------------------------------------

    def mode(self) -> Mode:
        return Mode(self.query(p.get_effect(EffectItem.MODE))[0])

    def canvas_count(self, boot: bool = False) -> int:
        item = EffectItem.START_CANVAS_LEN if boot else EffectItem.CANVAS_LEN
        return self.query(p.get_effect(item))[0]

    def canvas_params(self, canvas: int) -> CanvasParams:
        return CanvasParams.decode(self.query(p.get_effect(EffectItem.CANVAS_PARAMS, canvas)))

    def canvas_map(self, boot: bool = False) -> list[int]:
        item = EffectItem.START_CANVAS_MAP if boot else EffectItem.CANVAS_MAP
        return list(self.query(p.get_effect(item, 0))[: p.NUM_CHANNELS])

    def canvases(self, boot: bool = False) -> tuple[CanvasLayout, list[CanvasParams]]:
        base = p.BOOT_CANVAS_BASE if boot else 0
        params = [self.canvas_params(base + i) for i in range(self.canvas_count(boot))]
        layout = CanvasLayout.from_device(self.canvas_map(boot), [c.ch_count for c in params])
        return layout, params

    def frame_ms(self, boot: bool = False) -> int:
        return self.query(p.get_effect(EffectItem.START_FPS if boot else EffectItem.FPS))[0]

    def led_counts(self) -> list[int]:
        return p.parse_u16le(self.query(p.get_effect(EffectItem.LED_NUM)), p.NUM_CHANNELS)

    def zled_counts(self) -> list[int]:
        return p.parse_u16le(self.query(p.get_effect(EffectItem.ZLED_NUM)), p.NUM_CHANNELS)

    def palette(self, group: int) -> list[RGB]:
        return p.parse_palette(self.query(p.get_effect(EffectItem.PALETTE, group & 7)))

    def dynamic(self, which: int) -> list[int]:
        item = EffectItem.DYNAMIC_1 if which == 1 else EffectItem.DYNAMIC_2
        return list(self.query(p.get_effect(item))[:16])

    def frame_delay(self) -> list[int]:
        return list(self.query(p.get_effect(EffectItem.FRAME_DELAY))[:16])

    def boot_playlist(self) -> tuple[int, int]:
        r = self.query(p.get_effect(EffectItem.BOOT_PLAYLIST))
        return r[0], r[1]

    def ol_idle(self) -> int:
        return p.parse_u16le(self.query(p.get_core(CoreItem.OL_IDLE)), 1)[0]

    def vmap_name(self, ch: int) -> str:
        r = self.query(p.get_core(CoreItem.VMAP_NAME, ch))
        return p.parse_utf16le(r[1 : 1 + r[0]])

    def led_index(self) -> list[list[int]]:
        """LED order remap ("vmap") per channel: 16 × 256 entries."""
        total = p.NUM_CHANNELS * p.LEDS_PER_CHANNEL
        data = bytearray()
        while len(data) < total:
            n = min(p.LED_INDEX_CHUNK, total - len(data))
            data += self.query(p.get_led_index(len(data), n))[:n]
        L = p.LEDS_PER_CHANNEL
        return [list(data[ch * L : (ch + 1) * L]) for ch in range(p.NUM_CHANNELS)]

    # --- writes (each is persisted by the firmware) -------------------------------------------

    def set_mode(self, mode: Mode) -> None:
        self.send(p.set_mode(mode))

    def set_canvas_params(self, canvas: int, params: CanvasParams) -> None:
        self.send(params.encode(canvas))

    def set_canvases(self, layout: CanvasLayout, params: Sequence[CanvasParams], boot: bool = False) -> None:
        """Replace all loop (or boot) canvases. Same order as the app: params, channel map, count."""
        limit = p.BOOT_CANVAS_MAX if boot else p.LOOP_CANVAS_MAX
        if len(layout.canvases) != len(params) or len(params) > limit:
            raise ValueError(f"need one params entry per canvas, at most {limit} canvases")
        base = p.BOOT_CANVAS_BASE if boot else 0
        for i, (chans, prm) in enumerate(zip(layout.canvases, params)):
            self.send(replace(prm, ch_count=len(chans)).encode(base + i))
        self.send(p.set_canvas_map(layout.flat(), boot))
        item = EffectItem.START_CANVAS_LEN if boot else EffectItem.CANVAS_LEN
        self.send(p.set_effect(item, len(params)))

    def set_led_counts(self, counts: Sequence[int]) -> None:
        self.send(p.set_led_num(counts))

    def set_palette(self, group: int, colors: Sequence[RGB]) -> None:
        self.send(p.set_palette(group, colors))

    def set_ol_idle(self, seconds: int) -> None:
        self.send(p.set_ol_idle(seconds))

    def set_frame_ms(self, ms: int, boot: bool = False) -> None:
        self.send(p.set_effect(EffectItem.START_FPS if boot else EffectItem.FPS, ms))

    def set_zled_counts(self, counts: Sequence[int]) -> None:
        """Dark LEDs after the lit ones, per channel. The firmware ignores the write if any channel's
        LED + zLED count exceeds 256."""
        self.send(p.set_effect(EffectItem.ZLED_NUM, p.u16le(counts)))

    def set_dynamic(self, which: int, values: Sequence[int]) -> None:
        item = EffectItem.DYNAMIC_1 if which == 1 else EffectItem.DYNAMIC_2
        self.send(p.set_effect(item, list(values)[:16]))

    def set_frame_delay(self, values: Sequence[int]) -> None:
        self.send(p.set_effect(EffectItem.FRAME_DELAY, list(values)[:16]))

    def set_boot_playlist(self, intro: int, steady: int) -> None:
        self.send(p.set_effect(EffectItem.BOOT_PLAYLIST, intro, steady))

    def set_name(self, text: str) -> None:
        data = p.utf16le(text)
        count = p.parse_u32le(self.query(p.get_core(CoreItem.NAME_COUNT)))
        self.send(p.set_core(CoreItem.NAME_COUNT, p.u32le(count + 1)))
        self.send(p.set_core(CoreItem.NAME_LEN, p.u32le(len(data))))
        self.send(p.set_core(CoreItem.NAME, data))

    def set_vmap_name(self, ch: int, text: str) -> None:
        self.send(p.set_vmap_name(ch, text))

    def set_led_index(self, ch: int, order: Sequence[int]) -> None:
        """LED order remap for one channel (app: "vmap"); entries are LED indices < 256."""
        if not order or max(order) >= p.LEDS_PER_CHANNEL:
            raise ValueError("LED index entries must be 0..255")
        addr, data = ch * p.LEDS_PER_CHANNEL, list(order)
        while data:
            chunk, data = data[: p.LED_INDEX_CHUNK], data[p.LED_INDEX_CHUNK :]
            self.send(p.set_led_index(addr, chunk))
            addr += len(chunk)

    def play_boot_canvas(self) -> None:
        """Preview the boot canvases now (app: "play")."""
        self.send(p.set_effect(EffectItem.INIT_START))

    def factory_reset(self) -> None:
        """Reset canvas and lighting settings; hardware calibration is kept. The device may reboot."""
        self.send(p.set_effect(EffectItem.RESTORE))

    def reboot(self) -> None:
        self.send(p.set_effect(EffectItem.SOFT_RESET))

    # --- live streaming -----------------------------------------------------------------------

    def send_frame(self, frame: Mapping[int, Sequence[RGB]]) -> None:
        """Stream one frame: fw channel -> LED colors. Channels left out are not updated."""
        with self._lock:
            for ch, colors in frame.items():
                self._write(p.live_channel(ch, colors))
            self._write(p.show())
