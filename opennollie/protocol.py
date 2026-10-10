"""Nollie 16CH (firmware 2.3.0, USB 16d5:2a16) protocol: packet builders and response parsers.

Pure functions only, no I/O. See docs/PROTOCOL.md for the wire format.

Every command is one 1024-byte HID output report whose first byte is the command. GET commands
are answered with one 1024-byte input report; its payload starts at byte 0. SET commands get no
reply. Multi-byte integers are little-endian.

The firmware's bootloader/OTA commands (0xFD, 0x81, 0x83, 0x84) are deliberately not defined here.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum

VID = 0x16D5
PID = 0x2A16
INTERFACE = 0            # if0 = vendor protocol; if1 is a keyboard interface we never touch

REPORT_LEN = 1024
NUM_CHANNELS = 16
LEDS_PER_CHANNEL = 256

# Port number (1-based; printed on the board and used by NollieRGB/OpenRGB as "Channel N") -> fw channel.
PORT_TO_CH = (3, 2, 1, 0, 8, 9, 10, 11, 4, 5, 6, 7, 15, 14, 13, 12)
CH_TO_PORT = tuple(PORT_TO_CH.index(ch) + 1 for ch in range(NUM_CHANNELS))

LOOP_CANVAS_MAX = 14     # loop canvases are 0..13
BOOT_CANVAS_BASE = 14    # boot ("start") canvases are 14..15
BOOT_CANVAS_MAX = 2
CANVAS_MAP_LEN = 32      # canvas channel maps are sent padded to 32 entries

PALETTE_GROUPS = 8
PALETTE_SIZE = 16
NAME_MAX_CHARS = 30
LED_INDEX_CHUNK = 255    # max bytes per LED-index (vmap) read/write packet

RGB = tuple[int, int, int]


class Cmd(IntEnum):
    SHOW_LED = 0xFF
    GET_CONFIG = 0xFC
    SET_EFFECT = 0xFA
    GET_EFFECT = 0xF9
    SET_LED_INDEX = 0xF8
    GET_LED_INDEX = 0xF7
    SET_CORE = 0xF6
    GET_CORE = 0xF5
    FLASH = 0xF4
    REC_CTRL = 0xF3
    SET_LED_STREAM = 0xF2


class ConfigItem(IntEnum):      # GET_CONFIG sub-commands
    VERSION = 1
    FW_VERSION = 2
    CRASH_FLAG = 3
    VOLTAGE = 26


class EffectItem(IntEnum):      # SET_EFFECT / GET_EFFECT sub-commands
    MODE = 1
    CANVAS_PARAMS = 2
    CANVAS_MAP = 3
    CANVAS_LEN = 4
    START_CANVAS_LEN = 5
    FPS = 6
    START_FPS = 7
    LED_NUM = 8
    ZLED_NUM = 9
    GPIO = 10
    START_CANVAS_MAP = 11
    PALETTE = 13
    DYNAMIC_1 = 14
    DYNAMIC_2 = 15
    FSEQ_LED_NUM = 16
    FSEQ_INFO = 17
    FRAME_DELAY = 18
    BOOT_PLAYLIST = 19
    LED_DETECT = 20
    INIT_START = 32
    RESTORE = 33            # factory reset (keeps calibration)
    RESTORE_SUPER = 34
    SOFT_RESET = 35         # reboot


class CoreItem(IntEnum):        # SET_CORE / GET_CORE sub-commands
    FIRST_UNIX = 1
    UNIX = 2
    WRITE_COUNT = 3
    NAME_COUNT = 4
    NAME_LEN = 5
    NAME = 6
    VMAP_NAME = 7
    OL_IDLE = 22


class Mode(IntEnum):
    ONLINE = 0      # host streams frames
    HARDWARE = 1    # built-in effects on canvases (factory default)
    SL = 2
    FSEQ = 3        # play the stored animation


class BootIntro(IntEnum):     # boot playlist, first item (played once at power-on)
    START_CANVAS = 0
    NONE = 1
    FSEQ_ONCE = 2


class BootSteady(IntEnum):    # boot playlist, second item (runs after the intro)
    HW_EFFECTS = 0
    FSEQ_LOOP = 1


IDLE_NEVER = 0xFFFF         # OL idle timeout value meaning "never fall back"; 0 = firmware default (~3 s)

# Effect index == firmware fx_mode value; order is fixed by the firmware.
EFFECTS = (
    "Off", "Solid", "Breath", "Scanning", "Rainbow", "Rainbow cycle", "Meteor", "Shake",
    "Slowly wipe", "Color wipe", "Rainbow strip", "Rainbow strip l", "Wave", "Sunny", "Pull flower",
    "Stackab1", "Stackab2", "Stackab3", "Integration1", "Integration2", "Interchange", "Bullets",
    "Sequence", "ColorFromPalette",
)
# Effects for boot canvases (14+) index this table instead.
START_EFFECTS = (
    "Light up", "Color wipe", "Slowly wipe", "Pull flower", "Stackab1", "Stackab2", "Stackab3", "Bullets",
)

# Which settings each built-in effect uses (from the NollieRGB 2.3.0 effect catalog). Keys: brightness, step
# (speed), size, delay, frame_delay, color1..3, palette (palette group), blend, vmap (follow LED order).
_WIPE = {"frame_delay", "size", "vmap", "delay", "color2", "step", "color3", "brightness", "color1"}
_WAVE = {"frame_delay", "size", "vmap", "delay", "step", "color3", "brightness", "color1"}
_BULLETS = {"size", "vmap", "delay", "step", "color3", "brightness", "color1"}
EFFECT_PARAMS: dict[str, set[str]] = {
    "Off": set(),
    "Solid": {"color3", "vmap", "brightness", "color1"},
    "Breath": {"vmap", "delay", "step", "color3", "brightness", "color1"},
    "Scanning": {"size", "vmap", "delay", "step", "color3", "brightness", "color1"},
    "Rainbow": {"vmap", "delay", "step", "color3", "brightness"},
    "Rainbow cycle": {"size", "vmap", "delay", "step", "color3", "brightness"},
    "Meteor": {"size", "vmap", "delay", "color2", "step", "color3", "brightness", "color1"},
    "Shake": {"size", "vmap", "delay", "step", "color3", "brightness", "color1"},
    "Slowly wipe": _WIPE, "Color wipe": _WIPE,
    "Rainbow strip": {"size", "vmap", "delay", "step", "color3", "brightness"},
    "Rainbow strip l": {"size", "vmap", "delay", "step", "color3", "brightness"},
    "Wave": _WAVE, "Sunny": _WAVE, "Pull flower": _WAVE,
    "Stackab1": _WIPE, "Stackab2": _WIPE, "Stackab3": _WIPE, "Integration1": _WIPE, "Integration2": _WIPE,
    "Interchange": _WIPE,
    "Bullets": _BULLETS, "Sequence": _BULLETS,
    "ColorFromPalette": {"palette", "size", "vmap", "delay", "step", "color3", "brightness", "blend"},
    "Light up": {"step", "brightness", "color1"},
}
# Setting ranges used by the official app's sliders.
PARAM_RANGES = {"brightness": (0, 100), "step": (1, 10), "size": (0, 50), "delay": (0, 8), "frame_delay": (1, 255)}

# Voltage report (GET_CONFIG VOLTAGE) for the 16CH: (field, u16 index, scale, unit).
VOLTAGE_FIELDS = (
    ("input_voltage", 0, 0.001, "V"),
    ("input_current", 1, 0.01, "A"),
    ("mcu_supply", 2, 0.001, "V"),
    ("mcu_temp", 3, 0.01, "°C"),
    ("cpu_usage", 4, 1, "%"),
    ("rom_state", 5, 1, ""),
)


# --- encoding helpers -------------------------------------------------------------------------

def packet(*parts: int | Sequence[int] | bytes) -> bytes:
    """Concatenate ints/byte sequences into one zero-padded output report."""
    buf = bytearray()
    for p in parts:
        if isinstance(p, int):
            buf.append(p)
        else:
            buf.extend(p)
    if len(buf) > REPORT_LEN:
        raise ValueError(f"packet too long: {len(buf)} > {REPORT_LEN}")
    if any(not 0 <= b <= 0xFF for b in buf):
        raise ValueError("packet byte out of range")
    return bytes(buf) + bytes(REPORT_LEN - len(buf))


def u16le(values: Sequence[int]) -> bytes:
    return b"".join(int(v).to_bytes(2, "little") for v in values)


def u32le(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def parse_u16le(data: bytes, count: int) -> list[int]:
    return [int.from_bytes(data[i * 2 : i * 2 + 2], "little") for i in range(count)]


def parse_u32le(data: bytes) -> int:
    return int.from_bytes(data[:4], "little")


def utf16le(text: str) -> bytes:
    """Names are UTF-16LE, max 30 characters."""
    return text[:NAME_MAX_CHARS].encode("utf-16-le")


def parse_utf16le(data: bytes) -> str:
    return data[: len(data) // 2 * 2].decode("utf-16-le", errors="replace")


# --- GET builders (each answered by one input report) -----------------------------------------

def get_config(item: ConfigItem, *args: int) -> bytes:
    return packet(Cmd.GET_CONFIG, item, args)


def get_effect(item: EffectItem, *args: int) -> bytes:
    return packet(Cmd.GET_EFFECT, item, args)


def get_core(item: CoreItem, *args: int) -> bytes:
    return packet(Cmd.GET_CORE, item, args)


def get_led_index(offset: int, length: int) -> bytes:
    if not 1 <= length <= LED_INDEX_CHUNK:
        raise ValueError(f"LED index read length must be 1..{LED_INDEX_CHUNK}")
    return packet(Cmd.GET_LED_INDEX, u16le([offset]), length)


# --- SET builders (no reply) ------------------------------------------------------------------

def set_effect(item: EffectItem, *args: int | Sequence[int] | bytes) -> bytes:
    return packet(Cmd.SET_EFFECT, item, *args)


def set_core(item: CoreItem, *args: int | Sequence[int] | bytes) -> bytes:
    return packet(Cmd.SET_CORE, item, *args)


def set_mode(mode: Mode) -> bytes:
    return set_effect(EffectItem.MODE, int(mode))


def set_led_num(counts: Sequence[int]) -> bytes:
    """LED count per fw channel (16 × u16)."""
    _check_channels(counts)
    if any(not 0 <= c <= LEDS_PER_CHANNEL for c in counts):
        raise ValueError(f"LED counts must be 0..{LEDS_PER_CHANNEL}")
    return set_effect(EffectItem.LED_NUM, u16le(counts))


def set_canvas_map(flat: Sequence[int], boot: bool = False) -> bytes:
    """Channel order shared by all loop (or boot) canvases; see CanvasLayout."""
    item = EffectItem.START_CANVAS_MAP if boot else EffectItem.CANVAS_MAP
    return set_effect(item, 0, _pad(flat, CANVAS_MAP_LEN))


def set_palette(group: int, colors: Sequence[RGB]) -> bytes:
    """One palette group: 16 colors, sent as GRB."""
    if len(colors) != PALETTE_SIZE:
        raise ValueError(f"palette needs {PALETTE_SIZE} colors")
    grb = [b for r, g, b_ in colors for b in (g, r, b_)]
    return set_effect(EffectItem.PALETTE, group & 7, grb)


def set_ol_idle(seconds: int) -> bytes:
    """Return to built-in effects this long after host streaming stops (0 = never)."""
    return set_core(CoreItem.OL_IDLE, u16le([max(0, min(0xFFFF, int(seconds)))]))


def set_vmap_name(ch: int, text: str) -> bytes:
    data = utf16le(text)
    return set_core(CoreItem.VMAP_NAME, ch, len(data), data)


def set_led_index(address: int, chunk: Sequence[int]) -> bytes:
    if not 1 <= len(chunk) <= LED_INDEX_CHUNK:
        raise ValueError(f"LED index chunk must be 1..{LED_INDEX_CHUNK} bytes")
    return packet(Cmd.SET_LED_INDEX, u16le([address]), len(chunk), chunk)


# --- live streaming ---------------------------------------------------------------------------

def live_channel(ch: int, colors: Sequence[RGB]) -> bytes:
    """One channel's frame: [ch, 0, 0, 0, (G, R, B) × 256]. Unspecified LEDs are off."""
    if not 0 <= ch < NUM_CHANNELS:
        raise ValueError(f"channel out of range: {ch}")
    if len(colors) > LEDS_PER_CHANNEL:
        raise ValueError(f"too many LEDs: {len(colors)}")
    grb = bytearray(LEDS_PER_CHANNEL * 3)
    for i, (r, g, b) in enumerate(colors):
        grb[i * 3 : i * 3 + 3] = bytes((g, r, b))
    return packet(ch, 0, 0, 0, grb)


def show() -> bytes:
    """Display the channels sent since the last show."""
    return packet(Cmd.SHOW_LED)


# --- structured values ------------------------------------------------------------------------

@dataclass
class CanvasParams:
    """Effect settings of one canvas (CANVAS_PARAMS). Colors are RGB."""
    effect: int = 2
    brightness: int = 100
    step: int = 1               # speed
    size: int = 1
    ch_count: int = 0           # number of channels this canvas takes from the canvas map
    vmap_process: int = 0
    delay: int = 0
    color1: RGB = (0, 255, 0)
    color2: RGB = (255, 255, 0)
    color3: RGB = (0, 255, 255)

    def encode(self, canvas: int) -> bytes:
        return set_effect(
            EffectItem.CANVAS_PARAMS, canvas, self.effect, self.brightness, self.step, self.size,
            self.ch_count, self.vmap_process, self.delay, self.color1, self.color2, self.color3,
        )

    @classmethod
    def decode(cls, resp: bytes) -> "CanvasParams":
        b = resp
        return cls(
            effect=b[3], brightness=b[4], step=b[5], size=b[6], ch_count=b[7], vmap_process=b[8],
            delay=b[9], color1=tuple(b[10:13]), color2=tuple(b[13:16]), color3=tuple(b[16:19]),
        )


@dataclass
class CanvasLayout:
    """Which channels belong to which canvas.

    The device stores one flat channel order plus a channel count per canvas (in CanvasParams):
    canvas i owns the next `ch_count` entries of the flat order. Unused channels follow.
    """
    canvases: list[list[int]] = field(default_factory=list)

    @classmethod
    def from_device(cls, flat: Sequence[int], counts: Sequence[int]) -> "CanvasLayout":
        out, pos = [], 0
        for n in counts:
            out.append(list(flat[pos : pos + n]))
            pos += n
        return cls(out)

    def flat(self) -> list[int]:
        used = [ch for chans in self.canvases for ch in chans]
        if len(used) != len(set(used)) or any(not 0 <= ch < NUM_CHANNELS for ch in used):
            raise ValueError(f"invalid canvas layout: {self.canvases}")
        return used + [ch for ch in range(NUM_CHANNELS) if ch not in used]


def parse_version(resp: bytes) -> tuple[int, int]:
    """GET_CONFIG VERSION -> (firmware config version, hardware id)."""
    return resp[1], resp[2] | resp[3] << 8


def parse_fw_version(resp: bytes) -> str:
    return f"{resp[0]}.{resp[1]}.{resp[2]}"


def parse_palette(resp: bytes) -> list[RGB]:
    return [(resp[i * 3 + 1], resp[i * 3], resp[i * 3 + 2]) for i in range(PALETTE_SIZE)]


def parse_voltage(resp: bytes) -> dict[str, float]:
    raw = parse_u16le(resp, len(VOLTAGE_FIELDS))
    return {name: raw[idx] * scale for name, idx, scale, _ in VOLTAGE_FIELDS}


def frame_ms_to_fps(ms: int) -> float:
    return 1000 / ms if ms else 0.0


def _check_channels(values: Sequence[int]) -> None:
    if len(values) != NUM_CHANNELS:
        raise ValueError(f"need {NUM_CHANNELS} values, got {len(values)}")


def _pad(values: Sequence[int], n: int) -> list[int]:
    if len(values) > n:
        raise ValueError(f"too many values: {len(values)} > {n}")
    return list(values) + [0] * (n - len(values))
