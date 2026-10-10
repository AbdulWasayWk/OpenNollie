"""External SPI flash access (HID 0xF4) and stored-animation (FSEQ) upload/playback.

Upload sequence, as NollieRGB 2.3.0 does it: stop playback → set the FSEQ channel map → erase the
64 KiB blocks covering the file from FSEQ_BASE → write it raw in 1016-byte chunks → verify with the
device-side CRC (fallback: read back and compare) → wait until FSEQ_INFO reports the file.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum

from . import protocol as p
from .device import Nollie16
from .protocol import Cmd, EffectItem

FSEQ_BASE = 0x10000             # stored animation lives here; below is firmware-reserved
ERASE_BLOCK = 0x10000
PAYLOAD_MAX = p.REPORT_LEN - 8  # 1016
HEADER_LEN = 8


class FlashOp(IntEnum):
    READ = 1
    WRITE = 2
    STATUS = 3
    INFO = 4
    ERASE = 5
    WRITE_RAW = 6
    CRC = 7


class FlashStatus(IntEnum):
    OK = 0
    BUSY = 1
    ERROR = 2
    ADDR_ERR = 3
    LEN_ERR = 4
    NO_DEV = 5
    RETRY = 6


class RecCtrl(IntEnum):        # REC_CTRL (0xF3) sub-commands: stored-animation playback
    ECHO = 1
    PLAY = 2
    STOP = 3
    STATUS = 4


FLASH_VENDORS = {0x1F: "Atmel", 0x20: "Micron", 0x85: "PUYA", 0xC2: "Macronix", 0xC8: "GigaDevice", 0xEF: "Winbond"}


class FlashError(IOError):
    pass


def crc32_nollie(data: bytes) -> int:
    """The device's CRC: CRC-32 poly 0x04C11DB7, init 0xFFFFFFFF, MSB-first, no final XOR (as the
    STM32/CH32 CRC unit computes it). Whole 32-bit words are fed as little-endian words; trailing
    bytes are fed one at a time in the top byte."""
    crc = 0xFFFFFFFF
    n = len(data) & ~3
    for i in range(0, n, 4):
        crc = _crc_word(crc, int.from_bytes(data[i : i + 4], "little"), 32)
    for b in data[n:]:
        crc = _crc_word(crc, b << 24, 8)
    return crc


def _crc_word(crc: int, value: int, bits: int) -> int:
    crc ^= value
    for _ in range(bits):
        crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def header(op: FlashOp, addr: int = 0, length: int = 0) -> bytes:
    """[F4, op, addr (24-bit LE), length (16-bit LE), 0]"""
    return bytes((Cmd.FLASH, op)) + addr.to_bytes(3, "little") + (length & 0xFFFF).to_bytes(2, "little") + b"\0"


@dataclass
class FseqInfo:
    present: bool
    size: int


class Flash:
    def __init__(self, dev: Nollie16):
        self.dev = dev

    # --- primitives ---------------------------------------------------------------------------

    def _request(self, pkt: bytes, timeout_ms: int = 200) -> bytes:
        r = self.dev.query(p.packet(pkt), timeout_ms)
        if r[0] != Cmd.FLASH:
            raise FlashError(f"unexpected reply {r[:8].hex(' ')}")
        return r

    def _submit(self, pkt: bytes, what: str) -> FlashStatus:
        """Send a command the device acknowledges; retry while it answers RETRY."""
        for _ in range(400):
            st = FlashStatus(self._request(pkt)[7])
            if st != FlashStatus.RETRY:
                return st
            time.sleep(0.005)
        raise FlashError(f"{what}: device kept answering RETRY")

    def status(self) -> FlashStatus:
        return FlashStatus(self._request(header(FlashOp.STATUS))[7])

    def wait_idle(self, timeout_s: float, poll_s: float = 0.002, what: str = "flash") -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            st = self.status()
            if st == FlashStatus.OK:
                return
            if st != FlashStatus.BUSY:
                raise FlashError(f"{what}: status {st.name}")
            time.sleep(poll_s)
        raise TimeoutError(f"{what}: timed out after {timeout_s} s")

    def info(self) -> tuple[bool, str]:
        """(chip present, description such as 'GigaDevice, 16 MB')."""
        for _ in range(25):
            r = self._request(header(FlashOp.INFO))
            if r[7] != FlashStatus.BUSY:
                break
            time.sleep(0.02)
        if r[7] == FlashStatus.NO_DEV:
            return False, "no flash chip"
        mfr, _mem_type, cap = r[8], r[9], r[10]
        size = f", {(1 << cap) >> 20} MB" if 20 <= cap <= 30 else ""
        return True, FLASH_VENDORS.get(mfr, f"MFR 0x{mfr:02X}") + size

    def read(self, addr: int, length: int) -> bytes:
        if not 0 < length <= PAYLOAD_MAX:
            raise ValueError(f"read length must be 1..{PAYLOAD_MAX}")
        for _ in range(50):
            r = self._request(header(FlashOp.READ, addr, length), timeout_ms=500)
            if r[7] != FlashStatus.BUSY:
                break
            time.sleep(0.02)
        if r[7] != FlashStatus.OK:
            raise FlashError(f"read 0x{addr:06X}: status {FlashStatus(r[7]).name}")
        return r[8 : 8 + length]

    def erase_block(self, addr: int) -> None:
        if addr % ERASE_BLOCK or addr < FSEQ_BASE:
            raise ValueError(f"refusing to erase 0x{addr:06X} (must be a 64 KiB block at/above 0x{FSEQ_BASE:X})")
        st = self._submit(header(FlashOp.ERASE, addr, 1), "erase")
        if st != FlashStatus.BUSY:
            raise FlashError(f"erase 0x{addr:06X} rejected: {st.name}")
        self.wait_idle(2.0, 0.01, f"erase 0x{addr:06X}")

    def write_raw(self, addr: int, data: bytes) -> None:
        if addr < FSEQ_BASE:
            raise ValueError(f"refusing to write below 0x{FSEQ_BASE:X}")
        if not 0 < len(data) <= PAYLOAD_MAX:
            raise ValueError(f"write length must be 1..{PAYLOAD_MAX}")
        st = self._submit(header(FlashOp.WRITE_RAW, addr, len(data)) + bytes(data), "write")
        if st != FlashStatus.BUSY:
            raise FlashError(f"write 0x{addr:06X} rejected: {st.name}")
        self.wait_idle(5.0, 0.001, f"write 0x{addr:06X}")

    def device_crc(self, addr: int, length: int, timeout_s: float = 15.0) -> int | None:
        """Ask the device to CRC a region; None if unsupported."""
        pkt = bytearray(header(FlashOp.CRC, addr, length))
        pkt[7] = (length >> 16) & 0xFF
        if self._submit(bytes(pkt), "crc") != FlashStatus.BUSY:
            return None
        self.wait_idle(max(timeout_s, length / 1_048_576 * 3), 0.1, "crc")
        r = self._request(header(FlashOp.CRC))
        return int.from_bytes(r[8:12], "little") if r[7] == FlashStatus.OK else None

    # --- stored animation ---------------------------------------------------------------------

    def fseq_info(self) -> FseqInfo:
        r = self.dev.query(p.get_effect(EffectItem.FSEQ_INFO))
        return FseqInfo(bool(r[0]), p.parse_u32le(r[1:5]))

    def fseq_led_map(self) -> list[int]:
        return p.parse_u16le(self.dev.query(p.get_effect(EffectItem.FSEQ_LED_NUM)), p.NUM_CHANNELS)

    def set_fseq_led_map(self, led_map: Sequence[int]) -> None:
        """LEDs per fw channel for the stored animation; the file's ranges map onto the nonzero
        channels in ascending channel order."""
        self.dev.send(p.set_effect(EffectItem.FSEQ_LED_NUM, p.u16le(led_map)))

    def play(self, loop: bool = True) -> bool:
        """Start the stored animation now; returns whether it is playing.

        Right after an upload or stop the device sits in mode SL for a few seconds and ignores
        PLAY, so wait that out first."""
        deadline = time.monotonic() + 6
        while self.dev.mode() == p.Mode.SL and time.monotonic() < deadline:
            time.sleep(0.1)
        self.dev.send(p.packet(Cmd.REC_CTRL, RecCtrl.PLAY, 0, 0, int(loop)))
        time.sleep(0.2)
        return self.play_status()[0]

    def stop(self) -> None:
        """Stop the stored animation: switch to built-in effects (instant; the firmware's STOP command
        would blank the LEDs for ~1.5 s in mode SL)."""
        if self.dev.mode() in (p.Mode.FSEQ, p.Mode.SL):
            self.dev.set_mode(p.Mode.HARDWARE)

    def play_status(self) -> tuple[bool, int, int]:
        """(playing, frame/position, result code)."""
        r = self.dev.query(p.packet(Cmd.REC_CTRL, RecCtrl.STATUS))
        return bool(r[1]), p.parse_u32le(r[2:6]), r[6]

    def upload_fseq(self, data: bytes, led_map: Sequence[int],
                    progress: Callable[[str, int, int], None] | None = None) -> FseqInfo:
        progress = progress or (lambda *_: None)
        total = len(data)
        last_block = (FSEQ_BASE + total - 1) & ~(ERASE_BLOCK - 1)
        n_blocks = (last_block - FSEQ_BASE) // ERASE_BLOCK + 1

        # Stop reading the region we're about to overwrite. Switching to built-in effects does that instantly;
        # the STOP command would put the device in SL (LEDs dark) right away. Flash writes cause SL anyway,
        # until ~1.2 s after the last write: an unavoidable short pause of the lights.
        if self.dev.mode() == p.Mode.FSEQ:
            self.dev.set_mode(p.Mode.HARDWARE)
        self.set_fseq_led_map(led_map)

        for i in range(n_blocks):
            self.erase_block(FSEQ_BASE + i * ERASE_BLOCK)
            progress("erase", i + 1, n_blocks)

        for off in range(0, total, PAYLOAD_MAX):
            self.write_raw(FSEQ_BASE + off, data[off : off + PAYLOAD_MAX])
            progress("write", min(off + PAYLOAD_MAX, total), total)

        if self.device_crc(FSEQ_BASE, total) != crc32_nollie(data):
            for off in range(0, total, PAYLOAD_MAX):
                n = min(PAYLOAD_MAX, total - off)
                if self.read(FSEQ_BASE + off, n) != data[off : off + n]:
                    raise FlashError(f"verify failed at offset 0x{off:06X}")
                progress("verify", off + n, total)
        progress("verify", total, total)

        for _ in range(12):
            info = self.fseq_info()
            if info.present and info.size > 0:
                return info
            time.sleep(0.08)
        return info
