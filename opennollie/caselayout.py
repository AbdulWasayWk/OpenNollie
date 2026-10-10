"""The picture of the PC case used for previews: which fans and LED strips there are, where they sit,
and which LEDs of which port each one shows. Kept on the PC in ~/.config/opennollie/layout.json and
edited on the GUI's Layout page.

Several items can share a port: daisy-chained fans use consecutive LED ranges (first LED 1, 9, 17 …).
"""

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .protocol import LEDS_PER_CHANNEL, NUM_CHANNELS

PATH = Path.home() / ".config" / "opennollie" / "layout.json"
KINDS = ("fan", "strip")


@dataclass
class Item:
    port: int
    kind: str = "fan"            # fan: ring of LEDs · strip: a line of LEDs
    first: int = 1               # first LED on the port (1-based, as wired)
    count: int = 0               # LEDs this item shows; 0 = the rest of the port
    x: float = 0.5               # center, as a fraction of the picture
    y: float = 0.5
    size: float = 1.0
    angle: float = 0.0           # degrees (strips)
    label: str = ""

    def leds(self, port_leds: int) -> int:
        """How many LEDs this item shows, given the port's LED count."""
        rest = max(0, port_leds - (self.first - 1))
        return min(self.count, rest) if self.count else rest

    def colors(self, wire: list, port_leds: int) -> list:
        """This item's slice of the port's colors (as wired); missing LEDs are dark."""
        n = self.leds(port_leds)
        part = list(wire[self.first - 1: self.first - 1 + n])
        return part + [(0, 0, 0)] * (n - len(part))


@dataclass
class CaseLayout:
    items: list[Item] = field(default_factory=list)

    def ports(self) -> set[int]:
        return {it.port for it in self.items}


def auto(led_counts: dict[int, int]) -> CaseLayout:
    """A starting point: one fan per connected port, in a grid. 0 or 256 LEDs (the firmware default)
    counts as not set up."""
    ports = [pt for pt in range(1, NUM_CHANNELS + 1) if 0 < led_counts.get(pt, 0) < LEDS_PER_CHANNEL]
    cols = max(1, math.ceil(math.sqrt(len(ports) * 1.5)))
    rows = max(1, math.ceil(len(ports) / cols))
    items = []
    for i, pt in enumerate(ports):
        r, c = divmod(i, cols)
        items.append(Item(pt, x=(c + 0.5) / cols, y=(r + 0.5) / rows * 0.9 + 0.02))
    return CaseLayout(items)


def to_dict(layout: CaseLayout) -> dict:
    return {"version": 1, "items": [asdict(it) for it in layout.items]}


def from_dict(d: dict) -> CaseLayout:
    items = []
    for raw in d.get("items", []):
        it = Item(**{k: v for k, v in raw.items() if k in Item.__dataclass_fields__})
        if not 1 <= it.port <= NUM_CHANNELS or it.kind not in KINDS:
            continue
        it.first = max(1, min(LEDS_PER_CHANNEL, int(it.first)))
        it.count = max(0, min(LEDS_PER_CHANNEL, int(it.count)))
        it.x, it.y = (min(1.0, max(0.0, float(v))) for v in (it.x, it.y))
        it.size = min(4.0, max(0.2, float(it.size)))
        items.append(it)
    return CaseLayout(items)


def load(path: Path | None = None) -> CaseLayout | None:
    """The saved layout, or None if there isn't one yet."""
    try:
        return from_dict(json.loads((path or PATH).read_text()))
    except (OSError, ValueError, TypeError):
        return None


def save(layout: CaseLayout, path: Path | None = None) -> None:
    path = path or PATH          # (looked up per call, so tests can point PATH elsewhere)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(layout), indent=1))
