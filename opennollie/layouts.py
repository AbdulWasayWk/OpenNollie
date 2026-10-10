"""LED layouts: the order in which effects travel over a port's LEDs (the app calls it "vmap").

`order[i]` is the physical LED (counted along the wire) at logical position `i`. The first `leds`
entries are lit; `zleds` more may follow: LEDs that exist on the wire but are kept dark (e.g. an
inner ring that should stay off). The device stores 256 entries per channel; its built-in effects
use them when a canvas has `vmap_process` on. Live frames and stored animations are addressed
physically, so OpenNollie's engine applies the order itself (see engine.Renderer).

Layout files:
  * SignalRGB components (NollieRGB's `Components/*.json`): LedCount, LedMapping, LedCoordinates [y, x]
  * Nollie vmap files (`vmap/*.json`): fx_ch_led_num, fx_ch_zled_num, led_index
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import LEDS_PER_CHANNEL

ORDERS = ("as wired", "left → right", "right → left", "top → bottom", "bottom → top")


@dataclass
class Layout:
    name: str
    leds: int
    zleds: int = 0
    base: list[int] = field(default_factory=list)          # logical -> physical, as wired / as in the file
    coords: list[tuple[float, float]] | None = None        # (x, y) per entry of `base` (components only)
    kind: str = "strip"                                    # strip | component | vmap

    def __post_init__(self):
        if not self.base:
            self.base = list(range(self.leds + self.zleds))
        if not 1 <= self.leds or self.leds + self.zleds > LEDS_PER_CHANNEL:
            raise ValueError(f"{self.name}: needs 1..{LEDS_PER_CHANNEL} LEDs in total")
        # Entries may repeat (some vendor vmaps reuse LEDs on purpose); the device table is a lookup.
        if len(self.base) != self.leds + self.zleds or any(not 0 <= x < LEDS_PER_CHANNEL for x in self.base):
            raise ValueError(f"{self.name}: LED mapping must have {self.leds + self.zleds} entries of 0..255")


def strip(n: int, zleds: int = 0, name: str = "") -> Layout:
    return Layout(name or f"{n} LEDs", n, zleds)


def load(path: str | Path) -> Layout:
    """Load a SignalRGB component or a Nollie vmap file."""
    text = Path(path).read_text(encoding="utf-8-sig")
    d = json.loads(re.sub(r",\s*([}\]])", r"\1", text))          # some vendor files have trailing commas
    name = str(d.get("DisplayName") or d.get("ProductName") or Path(path).stem)
    if "led_index" in d:
        leds, zleds = int(d["fx_ch_led_num"]), int(d.get("fx_ch_zled_num", 0))
        return Layout(name, leds, zleds, [int(x) for x in d["led_index"][: leds + zleds]], kind="vmap")
    if "LedCount" in d:
        n = int(d["LedCount"])
        mapping = [int(x) for x in d.get("LedMapping") or range(n)]
        coords = [(float(c[1]), float(c[0])) for c in d["LedCoordinates"]] if d.get("LedCoordinates") else None
        return Layout(name, n, 0, mapping, coords, kind="component")
    raise ValueError(f"{path}: not a SignalRGB component or Nollie vmap file")


def order(layout: Layout, how: str = "as wired", reverse: bool = False, offset: int = 0) -> list[int]:
    """Logical -> physical order: lit LEDs (arranged as asked), then the dark ones."""
    lit, dark = list(layout.base[: layout.leds]), list(layout.base[layout.leds :])
    if how != "as wired":
        if how not in ORDERS:
            raise ValueError(f"order must be one of: {', '.join(ORDERS)}")
        if not layout.coords:
            raise ValueError(f"{layout.name} has no LED coordinates; only 'as wired' is possible")
        axis = 0 if "left" in how else 1
        pairs = sorted(zip(layout.base, layout.coords), key=lambda it: it[1][axis])   # stable, like the app
        lit = [phys for phys, _ in pairs][: layout.leds]
        if how in ("right → left", "bottom → top"):
            lit.reverse()
    if reverse:
        lit.reverse()
    if lit and offset % len(lit):
        k = offset % len(lit)
        lit = lit[k:] + lit[:k]
    return lit + dark


def full_index(order_: list[int]) -> list[int]:
    """The 256-entry table the device stores: the order, then every other LED as wired."""
    used = set(order_)
    return (list(order_) + [i for i in range(LEDS_PER_CHANNEL) if i not in used])[:LEDS_PER_CHANNEL]


def from_device(index: list[int], leds: int, zleds: int) -> list[int] | None:
    """The logical->physical order a channel uses, or None when it's the plain wiring order."""
    o = list(index[: leds + zleds])
    if any(x >= leds + zleds for x in o):           # table points past this port's LEDs; ignore it
        return None
    return None if o == list(range(leds + zleds)) else o


def describe(order_: list[int], leds: int) -> str:
    lit = order_[:leds]
    if lit == list(range(leds)):
        return "as wired"
    if lit == list(range(leds - 1, -1, -1)):
        return "reversed"
    k = lit[0]
    if lit == [(k + i) % leds for i in range(leds)]:
        return f"starts at LED {k + 1}"
    if lit == [(k - i) % leds for i in range(leds)]:
        return f"reversed, starts at LED {k + 1}"
    return "custom"


def read_ports(dev) -> tuple[dict[int, int], dict[int, list[int]]]:
    """From the device: LED count and (non-default) LED order per port."""
    from . import protocol as p
    counts, zleds, index = dev.led_counts(), dev.zled_counts(), dev.led_index()
    by_port_counts, by_port_orders = {}, {}
    for port in range(1, p.NUM_CHANNELS + 1):
        ch = p.PORT_TO_CH[port - 1]
        by_port_counts[port] = counts[ch]
        o = from_device(index[ch], counts[ch], zleds[ch])
        if o:
            by_port_orders[port] = o
    return by_port_counts, by_port_orders


def apply(dev, port: int, order_: list[int], leds: int, zleds: int = 0, name: str | None = None) -> None:
    """Store a port's LED count, dark-LED count and order (and optionally a name) on the device."""
    from . import protocol as p
    if len(order_) != leds + zleds:
        raise ValueError("order must list the lit LEDs then the dark ones")
    ch = p.PORT_TO_CH[port - 1]
    counts, z = dev.led_counts(), dev.zled_counts()
    if z[ch]:                       # keep LED + zLED <= 256 at every step
        z[ch] = 0
        dev.set_zled_counts(z)
    counts[ch] = leds
    dev.set_led_counts(counts)
    if zleds:
        z[ch] = zleds
        dev.set_zled_counts(z)
    dev.set_led_index(ch, full_index(order_))
    if name is not None:
        dev.set_vmap_name(ch, name)
