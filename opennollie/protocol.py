"""Byte-level packet builders for the Nollie 16CH (USB 3061:4716).

Pure functions only, no I/O. See docs/PROTOCOL.md for the wire format and where each
piece came from.
"""

from collections.abc import Mapping, Sequence

VID = 0x3061
PID = 0x4716

OUT_REPORT_LEN = 1024   # HID output report size (no report IDs)
NUM_PORTS = 16
MAX_LEDS_PER_PORT = 256

# Port index (0-based, OpenRGB's "Channel 1..16" order) -> channel number used on the wire.
PORT_TO_HW = (19, 18, 17, 16, 24, 25, 26, 27, 20, 21, 22, 23, 31, 30, 29, 28)

# The packet for this wire channel carries the latch flag that makes the device show the frame.
LATCH_HW_CHANNEL = 31
LATCH_FLAG = 2

RGB = tuple[int, int, int]


def channel_packet(hw_channel: int, colors: Sequence[RGB], flag: int = 0) -> bytes:
    """One output report: [hw_channel, flag, count_hi, count_lo, (G, R, B) * count], zero-padded."""
    if not 0 <= hw_channel <= 0xFF:
        raise ValueError(f"hw_channel out of range: {hw_channel}")
    if len(colors) > MAX_LEDS_PER_PORT:
        raise ValueError(f"too many LEDs: {len(colors)} > {MAX_LEDS_PER_PORT}")

    buf = bytearray(OUT_REPORT_LEN)
    buf[0] = hw_channel
    buf[1] = flag
    buf[2], buf[3] = divmod(len(colors), 256)
    for i, (r, g, b) in enumerate(colors):
        buf[4 + i * 3 : 7 + i * 3] = bytes((g, r, b))
    return bytes(buf)


def frame_packets(port_colors: Mapping[int, Sequence[RGB]]) -> list[bytes]:
    """All packets for one frame, in the order OpenRGB sends them.

    `port_colors` maps a 0-based port index to that port's LED colors. Ports that are left out,
    or have no LEDs, are not sent. Packets go out in ascending wire-channel order, and the latch
    channel is always included (with 0 LEDs if it is unused) so the frame gets displayed.
    """
    by_hw: dict[int, Sequence[RGB]] = {}
    for port, colors in port_colors.items():
        if not 0 <= port < NUM_PORTS:
            raise ValueError(f"port out of range: {port}")
        if colors:
            by_hw[PORT_TO_HW[port]] = colors
    by_hw.setdefault(LATCH_HW_CHANNEL, [])

    return [
        channel_packet(hw, by_hw[hw], LATCH_FLAG if hw == LATCH_HW_CHANNEL else 0)
        for hw in sorted(by_hw)
    ]
