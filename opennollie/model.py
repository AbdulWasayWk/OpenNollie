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

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceState":
        """Inverse of to_dict (e.g. a backup file)."""
        def canvas_set(c: dict) -> CanvasSet:
            params = [CanvasParams(**{k: tuple(v) if k.startswith("color") else v for k, v in prm.items()})
                      for prm in c["params"]]
            return CanvasSet(CanvasLayout([list(x) for x in c["layout"]["canvases"]]), params, int(c["frame_ms"]))
        return cls(
            serial=d.get("serial", ""), fw_version=d.get("fw_version", ""),
            config_version=d.get("config_version", 0), hardware=d.get("hardware", 0),
            name=d.get("name", ""), write_count=d.get("write_count", 0),
            mode=Mode[str(d.get("mode", "hardware")).upper()],
            loop=canvas_set(d["loop"]), boot=canvas_set(d["boot"]),
            boot_playlist=tuple(d["boot_playlist"]), ol_idle_s=int(d["ol_idle_s"]),
            led_counts=list(d["led_counts"]), zled_counts=list(d["zled_counts"]),
            palettes=[[tuple(c) for c in g] for g in d["palettes"]],
            dynamic_1=list(d["dynamic_1"]), dynamic_2=list(d["dynamic_2"]), frame_delay=list(d["frame_delay"]),
            vmap_names=list(d["vmap_names"]),
            led_index=[list(x) for x in d["led_index"]] if d.get("led_index") else None,
        )


def restore(dev: Nollie16, target: DeviceState, include_name: bool = False, dry_run: bool = False) -> list[str]:
    """Write `target` to the device, touching only what differs. Returns what changed (or would change).

    The stored animation itself isn't part of a backup; the boot playlist referring to it is.
    """
    cur = DeviceState.read(dev, with_led_index=target.led_index is not None)
    changes: list[str] = []

    def step(label: str, differs: bool, write) -> None:
        if differs:
            changes.append(label)
            if not dry_run:
                write()

    step("controller name", include_name and target.name != cur.name, lambda: dev.set_name(target.name))
    # LED + zLED must stay <= 256 per channel at every step: clear zLEDs, set LEDs, then set zLEDs.
    leds_differ = target.led_counts != cur.led_counts or target.zled_counts != cur.zled_counts
    def write_leds():
        if any(cur.zled_counts):
            dev.set_zled_counts([0] * p.NUM_CHANNELS)
        dev.set_led_counts(target.led_counts)
        if any(target.zled_counts):
            dev.set_zled_counts(target.zled_counts)
    step("LED counts", leds_differ, write_leds)
    for g in range(p.PALETTE_GROUPS):
        step(f"palette {g + 1}", target.palettes[g] != cur.palettes[g],
             lambda g=g: dev.set_palette(g, target.palettes[g]))
    step("palette assignment", target.dynamic_1 != cur.dynamic_1, lambda: dev.set_dynamic(1, target.dynamic_1))
    step("palette blend", target.dynamic_2 != cur.dynamic_2, lambda: dev.set_dynamic(2, target.dynamic_2))
    step("frame delays", target.frame_delay != cur.frame_delay, lambda: dev.set_frame_delay(target.frame_delay))
    for label, boot in (("loop canvases", False), ("boot canvases", True)):
        t, c = (target.boot, cur.boot) if boot else (target.loop, cur.loop)
        same = t.layout.canvases == c.layout.canvases and t.params == c.params
        step(label, not same, lambda t=t, boot=boot: dev.set_canvases(t.layout, t.params, boot=boot))
        step(f"{label} frame rate", t.frame_ms != c.frame_ms, lambda t=t, boot=boot: dev.set_frame_ms(t.frame_ms, boot))
    for ch in range(p.NUM_CHANNELS):
        step(f"name of port {p.CH_TO_PORT[ch]}", target.vmap_names[ch] != cur.vmap_names[ch],
             lambda ch=ch: dev.set_vmap_name(ch, target.vmap_names[ch]))
    if target.led_index is not None:
        for ch in range(p.NUM_CHANNELS):
            step(f"LED order of port {p.CH_TO_PORT[ch]}", target.led_index[ch] != cur.led_index[ch],
                 lambda ch=ch: dev.set_led_index(ch, target.led_index[ch]))
    step("idle fallback", target.ol_idle_s != cur.ol_idle_s, lambda: dev.set_ol_idle(target.ol_idle_s))
    step("startup playlist", tuple(target.boot_playlist) != tuple(cur.boot_playlist),
         lambda: dev.set_boot_playlist(*target.boot_playlist))
    if not dry_run and changes and cur.mode != Mode.FSEQ:
        dev.set_mode(Mode.HARDWARE)
    return changes
