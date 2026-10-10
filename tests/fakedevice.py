"""A simulated Nollie 16CH (fw 2.3.0) at the transport level, for offline tests of the CLI, engine,
flash upload and backup/restore. It implements the commands OpenNollie uses, as documented in
docs/PROTOCOL.md, including an emulated SPI flash with the device-side CRC."""

from opennollie import protocol as p
from opennollie.flash import FSEQ_BASE, FlashOp, crc32_nollie
from opennollie.protocol import Cmd, ConfigItem, CoreItem, EffectItem

FLASH_SIZE = 4 << 20        # enough for tests (the real chip has 16 MB)


def _u16(data: bytes, n: int) -> list[int]:
    return [int.from_bytes(data[i * 2: i * 2 + 2], "little") for i in range(n)]


class FakeTransport:
    serial = "FAKE00000000"

    def __init__(self):
        self.mode = p.Mode.HARDWARE
        self.led = [256] * 16
        self.zled = [0] * 16
        self.index = bytearray(i % 256 for i in range(16 * 256))
        self.params: dict[int, bytes] = {0: bytes([2, 100, 1, 1, 16, 0, 0, 0, 255, 0, 0, 0, 255, 255, 0, 0])}
        self.maps = {False: bytes(range(16)) + bytes(16), True: bytes(32)}
        self.lens = {False: 1, True: 0}
        self.frame_ms = {False: 33, True: 16}
        self.palettes = {g: bytes(48) for g in range(8)}
        self.dyn = {1: bytes(16), 2: bytes(16)}
        self.frame_delay = bytes([33] * 16)
        self.playlist = (0, 0)
        self.idle = 0
        self.name = b""
        self.name_count = 0
        self.vmap_names = {ch: b"" for ch in range(16)}
        self.fseq_map = [0] * 16
        self.flash = bytearray(b"\xff" * FLASH_SIZE)
        self.crc = 0
        self.playing = False
        self.writes = 0
        self.frames = 0                     # live frames shown
        self.last_frame: dict[int, bytes] = {}
        self.log: list[bytes] = []          # every packet received

    # --- transport interface ----------------------------------------------------------------

    def close(self) -> None:
        pass

    def send_many(self, packets) -> None:
        for pkt in packets:
            self._handle(bytes(pkt))

    def query(self, packet: bytes, timeout_ms: int = 200) -> bytes:
        r = self._handle(bytes(packet))
        if r is None:
            raise AssertionError(f"no reply for {packet[:4].hex(' ')}")
        return r + bytes(p.REPORT_LEN - len(r))

    # --- firmware ---------------------------------------------------------------------------

    def _fseq_info(self) -> tuple[bool, int]:
        hdr = self.flash[FSEQ_BASE: FSEQ_BASE + 32]
        if hdr[:4] != b"PSEQ":
            return False, 0
        off = int.from_bytes(hdr[4:6], "little")
        frames = int.from_bytes(hdr[14:18], "little")
        n_ranges = hdr[22]
        frame_bytes = sum(int.from_bytes(self.flash[FSEQ_BASE + 32 + i * 6 + 3: FSEQ_BASE + 32 + i * 6 + 6], "little")
                          for i in range(n_ranges)) or int.from_bytes(hdr[10:14], "little")
        return True, off + frames * frame_bytes

    def _handle(self, pkt: bytes) -> bytes | None:
        assert len(pkt) == p.REPORT_LEN, len(pkt)
        self.log.append(pkt)
        cmd, sub = pkt[0], pkt[1]
        if cmd in (0xFD, 0x81, 0x83, 0x84):
            raise AssertionError(f"bootloader/OTA command 0x{cmd:02X} sent")
        if cmd < 16:                                            # live channel frame
            self.last_frame[cmd] = pkt[4:]
            return None
        if cmd == Cmd.SHOW_LED:
            self.mode, self.playing = p.Mode.ONLINE, False
            self.frames += 1
            return None
        if cmd == Cmd.SET_EFFECT:
            return self._set_effect(sub, pkt)
        if cmd == Cmd.GET_EFFECT:
            return self._get_effect(sub, pkt)
        if cmd == Cmd.SET_CORE:
            return self._set_core(sub, pkt)
        if cmd == Cmd.GET_CORE:
            return self._get_core(sub, pkt)
        if cmd == Cmd.GET_CONFIG:
            return {ConfigItem.VERSION: bytes([0, 19, 165, 0]), ConfigItem.FW_VERSION: bytes([2, 3, 0]),
                    ConfigItem.VOLTAGE: p.u16le([5000, 75, 3300, 4100, 1, 0])}[sub]
        if cmd == Cmd.SET_LED_INDEX:
            addr, n = int.from_bytes(pkt[1:3], "little"), pkt[3]
            self.index[addr: addr + n] = pkt[4: 4 + n]
            return None
        if cmd == Cmd.GET_LED_INDEX:
            addr, n = int.from_bytes(pkt[1:3], "little"), pkt[3]
            return bytes(self.index[addr: addr + n])
        if cmd == Cmd.FLASH:
            return self._flash(pkt)
        if cmd == Cmd.REC_CTRL:
            if sub == 2:
                self.playing, self.mode = self._fseq_info()[0], p.Mode.FSEQ
            elif sub == 3:
                self.playing, self.mode = False, p.Mode.SL
            elif sub == 4:
                return bytes([Cmd.REC_CTRL, int(self.playing)]) + (0).to_bytes(4, "little") + bytes([0])
            return None
        raise AssertionError(f"unexpected command {pkt[:4].hex(' ')}")

    def _set_effect(self, sub: int, pkt: bytes) -> None:
        E = EffectItem
        self.writes += 1
        if sub == E.MODE:
            self.mode = p.Mode(pkt[2])
            if self.mode != p.Mode.FSEQ:
                self.playing = False
        elif sub == E.CANVAS_PARAMS:
            self.params[pkt[2]] = pkt[3:19]
        elif sub in (E.CANVAS_MAP, E.START_CANVAS_MAP):
            self.maps[sub == E.START_CANVAS_MAP] = pkt[3:35]
        elif sub in (E.CANVAS_LEN, E.START_CANVAS_LEN):
            self.lens[sub == E.START_CANVAS_LEN] = pkt[2]
        elif sub in (E.FPS, E.START_FPS):
            self.frame_ms[sub == E.START_FPS] = pkt[2]
        elif sub == E.LED_NUM:
            self.led = _u16(pkt[2:], 16)
        elif sub == E.ZLED_NUM:
            z = _u16(pkt[2:], 16)
            if all(a + b <= 256 for a, b in zip(self.led, z)):     # firmware ignores invalid writes
                self.zled = z
        elif sub == E.PALETTE:
            self.palettes[pkt[2] & 7] = pkt[3:51]
        elif sub in (E.DYNAMIC_1, E.DYNAMIC_2):
            self.dyn[1 if sub == E.DYNAMIC_1 else 2] = pkt[2:18]
        elif sub == E.FRAME_DELAY:
            self.frame_delay = pkt[2:18]
        elif sub == E.FSEQ_LED_NUM:
            self.fseq_map = _u16(pkt[2:], 16)
        elif sub == E.BOOT_PLAYLIST:
            self.playlist = (pkt[2], pkt[3])
        elif sub in (E.INIT_START, E.RESTORE, E.SOFT_RESET):
            pass
        else:
            raise AssertionError(f"unexpected SET_EFFECT {sub}")
        return None

    def _get_effect(self, sub: int, pkt: bytes) -> bytes:
        E = EffectItem
        if sub == E.MODE:
            return bytes([self.mode])
        if sub == E.CANVAS_PARAMS:
            return bytes(3) + self.params.get(pkt[2], bytes(16))
        if sub in (E.CANVAS_MAP, E.START_CANVAS_MAP):
            return self.maps[sub == E.START_CANVAS_MAP][:16]
        if sub in (E.CANVAS_LEN, E.START_CANVAS_LEN):
            return bytes([self.lens[sub == E.START_CANVAS_LEN]])
        if sub in (E.FPS, E.START_FPS):
            return bytes([self.frame_ms[sub == E.START_FPS]])
        if sub == E.LED_NUM:
            return p.u16le(self.led)
        if sub == E.ZLED_NUM:
            return p.u16le(self.zled)
        if sub == E.PALETTE:
            return self.palettes[pkt[2] & 7]
        if sub in (E.DYNAMIC_1, E.DYNAMIC_2):
            return self.dyn[1 if sub == E.DYNAMIC_1 else 2]
        if sub == E.FRAME_DELAY:
            return self.frame_delay
        if sub == E.FSEQ_LED_NUM:
            return p.u16le(self.fseq_map)
        if sub == E.FSEQ_INFO:
            present, size = self._fseq_info()
            return bytes([present]) + size.to_bytes(4, "little")
        if sub == E.BOOT_PLAYLIST:
            return bytes(self.playlist)
        if sub == E.LED_DETECT:
            return bytes(36)
        raise AssertionError(f"unexpected GET_EFFECT {sub}")

    def _set_core(self, sub: int, pkt: bytes) -> None:
        C = CoreItem
        if sub == C.NAME_COUNT:
            self.name_count = int.from_bytes(pkt[2:6], "little")
        elif sub == C.NAME_LEN:
            self._name_len = int.from_bytes(pkt[2:6], "little")
        elif sub == C.NAME:
            self.name = pkt[2: 2 + self._name_len]
        elif sub == C.VMAP_NAME:
            self.vmap_names[pkt[2]] = pkt[4: 4 + pkt[3]]
        elif sub == C.OL_IDLE:
            self.idle = int.from_bytes(pkt[2:4], "little")
        elif sub in (C.UNIX, C.FIRST_UNIX):
            pass
        else:
            raise AssertionError(f"unexpected SET_CORE {sub}")
        return None

    def _get_core(self, sub: int, pkt: bytes) -> bytes:
        C = CoreItem
        if sub == C.WRITE_COUNT:
            return self.writes.to_bytes(4, "little")
        if sub == C.NAME_COUNT:
            return self.name_count.to_bytes(4, "little")
        if sub == C.NAME_LEN:
            return len(self.name).to_bytes(4, "little")
        if sub == C.NAME:
            return self.name
        if sub == C.VMAP_NAME:
            n = self.vmap_names[pkt[2]]
            return bytes([len(n)]) + n
        if sub == C.OL_IDLE:
            return self.idle.to_bytes(2, "little")
        raise AssertionError(f"unexpected GET_CORE {sub}")

    def _flash(self, pkt: bytes) -> bytes:
        op = FlashOp(pkt[1])
        addr = int.from_bytes(pkt[2:5], "little")
        length = int.from_bytes(pkt[5:7], "little")
        head = bytes(pkt[:7])
        if op == FlashOp.INFO:
            return head + bytes([0, 0xC8, 0x40, 24])           # OK, GigaDevice, 16 MB
        if op == FlashOp.STATUS:
            return head + bytes([0])
        if op == FlashOp.READ:
            return head + bytes([0]) + bytes(self.flash[addr: addr + length])
        if op == FlashOp.ERASE:
            assert addr >= FSEQ_BASE and addr % 0x10000 == 0, hex(addr)
            self.flash[addr: addr + 0x10000] = b"\xff" * 0x10000
            return head + bytes([1])                           # accepted (BUSY), then STATUS → OK
        if op == FlashOp.WRITE_RAW:
            assert addr >= FSEQ_BASE, hex(addr)
            self.flash[addr: addr + length] = pkt[8: 8 + length]
            return head + bytes([1])
        if op == FlashOp.CRC:
            if addr == 0 and length == 0 and pkt[7] == 0:      # result request
                return head + bytes([0]) + self.crc.to_bytes(4, "little")
            total = length | pkt[7] << 16
            self.crc = crc32_nollie(bytes(self.flash[addr: addr + total]))
            return head + bytes([1])
        raise AssertionError(f"unexpected flash op {op}")
