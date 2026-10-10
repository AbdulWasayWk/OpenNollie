"""FSEQ v2 (xLights) files: writer for our rendered animations, reader for validation.

The firmware plays only **uncompressed** v2 files and sends the frame bytes to the LEDs as-is, so
colors must be stored **G, R, B** (verified on hardware: bytes 00 66 00 show red). Files exported
from xLights need GRB as the model color order. Frame data is packed by sparse range, and it
maps the ranges, in order, onto the channels with a nonzero entry in the FSEQ channel map
(FSEQ_LED_NUM), in ascending fw channel order. We write one range per active channel.

Header (32 bytes, little-endian):
  0  "PSEQ"            4  u16 channel data offset   6  minor   7  major (2)
  8  u16 variable-header offset   10 u32 channels per frame   14 u32 frame count
  18 u8 step ms   19 flags   20 compression (low 4 bits) | block count high bits
  21 block count low   22 sparse range count   23 reserved   24 u64 unique id
then 8 bytes per compression block (none here), 6 bytes per sparse range (u24 start, u24 count).
"""

import struct
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .protocol import LEDS_PER_CHANNEL, NUM_CHANNELS, RGB

MAGIC = b"PSEQ"
HEADER_LEN = 32
MAX_RANGES = 32


@dataclass
class FseqHeader:
    data_offset: int
    channel_count: int
    frame_count: int
    step_ms: int
    compression: int
    ranges: list[tuple[int, int]] = field(default_factory=list)

    @property
    def frame_bytes(self) -> int:
        return sum(n for _, n in self.ranges) if self.ranges else self.channel_count

    @property
    def duration_ms(self) -> int:
        return self.frame_count * self.step_ms


class FseqError(ValueError):
    pass


def build_fseq(led_map: Sequence[int], step_ms: int, frames: Iterable[Mapping[int, Sequence[RGB]]]) -> bytes:
    """Pack frames into an FSEQ v2 file.

    led_map: LEDs per fw channel (16 entries); 0 = channel not in the animation.
    frames:  per frame, fw channel -> LED colors as RGB (missing LEDs/channels are black).
             They are stored as GRB, which is what the firmware sends to the LEDs.
    """
    if len(led_map) != NUM_CHANNELS or any(not 0 <= n <= LEDS_PER_CHANNEL for n in led_map):
        raise FseqError(f"led_map needs {NUM_CHANNELS} entries of 0..{LEDS_PER_CHANNEL}")
    if not 1 <= step_ms <= 255:
        raise FseqError("step_ms must be 1..255")
    active = [ch for ch in range(NUM_CHANNELS) if led_map[ch]]
    if not active:
        raise FseqError("no active channels")

    ranges, start = [], 0
    for ch in active:
        ranges.append((start, led_map[ch] * 3))
        start += led_map[ch] * 3
    frame_bytes = start

    body = bytearray()
    frame_count = 0
    for frame in frames:
        for ch in active:
            colors = list(frame.get(ch, ()))[: led_map[ch]]
            seg = bytearray(led_map[ch] * 3)
            for i, (r, g, b) in enumerate(colors):
                seg[i * 3 : i * 3 + 3] = bytes((g, r, b))
            body += seg
        frame_count += 1
    if not frame_count:
        raise FseqError("no frames")

    var_offset = HEADER_LEN + 6 * len(ranges)
    data_offset = (var_offset + 3) & ~3
    # The "unique id" is derived from the content, so rendering the same thing twice gives the same file.
    uid = zlib.crc32(bytes(body)) << 32 | zlib.crc32(repr((led_map, step_ms)).encode())
    head = struct.pack(
        "<4sHBBHIIBBBBBBQ",
        MAGIC, data_offset, 0, 2, var_offset, frame_bytes, frame_count, step_ms,
        0, 0, 0, len(ranges), 0, uid,
    )
    rng = b"".join(s.to_bytes(3, "little") + n.to_bytes(3, "little") for s, n in ranges)
    return head + rng + bytes(data_offset - var_offset) + bytes(body)


def parse_header(data: bytes) -> FseqHeader:
    """Validate an FSEQ file the way the firmware/app do; raise FseqError if unplayable."""
    if len(data) < HEADER_LEN or data[:4] != MAGIC:
        raise FseqError("not an FSEQ file")
    if data[7] != 2:
        raise FseqError(f"only FSEQ v2 is supported (file is v{data[7]})")
    data_offset, = struct.unpack_from("<H", data, 4)
    channels, frames = struct.unpack_from("<II", data, 10)
    step_ms, comp, blocks_lo, n_ranges = data[18], data[20], data[21], data[22]
    if comp & 0x0F:
        raise FseqError("compressed FSEQ is not supported (export as 'Uncompressed' in xLights)")
    if not frames or not step_ms:
        raise FseqError("frame count or step time is 0")
    if n_ranges > MAX_RANGES:
        raise FseqError(f"{n_ranges} sparse ranges > device limit {MAX_RANGES}")
    ranges_off = HEADER_LEN + (((comp & 0xF0) << 4) | blocks_lo) * 8
    if len(data) < ranges_off + n_ranges * 6:
        raise FseqError("truncated sparse range table")
    ranges = [
        (int.from_bytes(data[o : o + 3], "little"), int.from_bytes(data[o + 3 : o + 6], "little"))
        for o in (ranges_off + i * 6 for i in range(n_ranges))
    ]
    hdr = FseqHeader(data_offset, channels, frames, step_ms, comp & 0x0F, ranges)
    if len(data) < data_offset + frames * hdr.frame_bytes:
        raise FseqError("file shorter than its header says")
    return hdr


def led_map_matches(hdr: FseqHeader, led_map: Sequence[int]) -> str | None:
    """None if the channel map fits the file, else the reason (mirrors the app's validation)."""
    active = [n for n in led_map if n]
    ranges = hdr.ranges or [(0, hdr.channel_count)]
    if len(active) != len(ranges):
        return f"{len(active)} channels configured but the file has {len(ranges)} ranges"
    for i, (leds, (_, count)) in enumerate(zip(active, ranges)):
        if leds * 3 != count:
            return f"channel #{i + 1}: {leds} LEDs ({leds * 3} bytes) != range of {count} bytes"
    return None
