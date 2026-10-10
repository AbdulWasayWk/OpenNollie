"""Snapshot of everything configurable on the device."""

from dataclasses import asdict, dataclass, field

from . import protocol as p
from .device import Nollie16
from .protocol import CanvasLayout, CanvasParams, Mode, RGB


@dataclass
class CanvasSet:
    """Loop or boot canvases: channel layout + one effect setting per canvas."""
    layout: CanvasLayout = field(default_factory=CanvasLayout)
    params: list[CanvasParams] = field(default_factory=list)
    frame_ms: int = 33


@dataclass
class DeviceState:
    serial: str = ""
    fw_version: str = ""
    config_version: int = 0
    hardware: int = 0
    name: str = ""
    write_count: int = 0
    mode: Mode = Mode.HARDWARE
    loop: CanvasSet = field(default_factory=CanvasSet)
    boot: CanvasSet = field(default_factory=CanvasSet)
    boot_playlist: tuple[int, int] = (0, 0)
    ol_idle_s: int = 0
    led_counts: list[int] = field(default_factory=list)
    zled_counts: list[int] = field(default_factory=list)
    palettes: list[list[RGB]] = field(default_factory=list)
    dynamic_1: list[int] = field(default_factory=list)
    dynamic_2: list[int] = field(default_factory=list)
    frame_delay: list[int] = field(default_factory=list)
    vmap_names: list[str] = field(default_factory=list)
    led_index: list[list[int]] | None = None

    @classmethod
    def read(cls, dev: Nollie16, with_led_index: bool = False) -> "DeviceState":
        """Read the full configuration. Read-only: sends GET commands only."""
        cfg_ver, hw = dev.version()
        loop_layout, loop_params = dev.canvases(boot=False)
        boot_layout, boot_params = dev.canvases(boot=True)
        return cls(
            serial=dev.serial,
            fw_version=dev.fw_version(),
            config_version=cfg_ver,
            hardware=hw,
            name=dev.name(),
            write_count=dev.write_count(),
            mode=dev.mode(),
            loop=CanvasSet(loop_layout, loop_params, dev.frame_ms(boot=False)),
            boot=CanvasSet(boot_layout, boot_params, dev.frame_ms(boot=True)),
            boot_playlist=dev.boot_playlist(),
            ol_idle_s=dev.ol_idle(),
            led_counts=dev.led_counts(),
            zled_counts=dev.zled_counts(),
            palettes=[dev.palette(g) for g in range(p.PALETTE_GROUPS)],
            dynamic_1=dev.dynamic(1),
            dynamic_2=dev.dynamic(2),
            frame_delay=dev.frame_delay(),
            vmap_names=[dev.vmap_name(ch) for ch in range(p.NUM_CHANNELS)],
            led_index=dev.led_index() if with_led_index else None,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.name.lower()
        return d
