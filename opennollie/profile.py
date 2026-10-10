"""Lighting profiles (TOML).

    name = "Evening"
    fps = 30                    # live/render frame rate
    leds = 32                   # LEDs per port; omit to use the counts stored on the device

    [groups]                    # optional names for port sets
    intake = "6-8,14-16"

    [ports.9]
    leds = 40

    [[layer]]                   # later layers win on ports they share with earlier ones
    ports = "all"               # "all", "4-9,12-16", or a group name
    effect = "rainbow"          # see effects.EFFECTS, or "hw:<firmware effect name>"
    period = 8                  # seconds per cycle
    spread = 1.0
    brightness = 0.6
    unit = "port"               # "port": each port one color | "led": per LED
    span = "group"              # with unit = "led": "port" (each port its own strip) or "group"
    colors = ["#ff0000", "blue"]
    # any other keys go to the effect's params, e.g. keyframes = [[0, "red"], [0.5, "blue"]]

Hardware layers (`effect = "hw:Breath"` etc.) run on the firmware's own effect engine. A profile
whose layers are all `static` or `hw:` is stored on the device as canvases; anything else is
rendered to an animation (FSEQ) or streamed live.
"""

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import protocol as p
from .effects import EFFECTS, EffectSpec, parse_color

USED_PORTS_DEFAULT = list(range(1, p.NUM_CHANNELS + 1))
LAYER_KEYS = {"ports", "effect", "colors", "color", "period", "spread", "brightness", "reverse",
              "interp", "unit", "span"}


@dataclass
class Layer:
    ports: list[int]
    spec: EffectSpec
    unit: str = "port"
    span: str = "group"

    @property
    def hw_effect(self) -> int | None:
        """Firmware effect index for `hw:` layers, else None."""
        if not self.spec.kind.startswith("hw:"):
            return None
        name = self.spec.kind[3:].strip().lower()
        for i, e in enumerate(p.EFFECTS):
            if e.lower() == name:
                return i
        raise ValueError(f"unknown firmware effect {name!r}; available: {', '.join(p.EFFECTS)}")


@dataclass
class Profile:
    name: str = "Untitled"
    fps: int = 30
    leds: int | None = None                 # None: use the device's stored counts (fallback 32)
    port_leds: dict[int, int] = field(default_factory=dict)
    groups: dict[str, list[int]] = field(default_factory=dict)
    layers: list[Layer] = field(default_factory=list)

    def leds_for(self, port: int) -> int:
        return self.port_leds.get(port, self.leds or 32)

    def with_device_leds(self, counts_by_port: dict[int, int]) -> "Profile":
        """Fill in LED counts the profile leaves open from the device's stored counts."""
        if self.leds is not None:
            return self
        merged = {pt: n for pt, n in counts_by_port.items() if 1 <= n <= p.LEDS_PER_CHANNEL} | self.port_leds
        return replace(self, port_leds=merged)

    @property
    def is_hardware(self) -> bool:
        """True if the device's own effect engine can run this profile (no rendering needed)."""
        return all(l.spec.kind == "static" and l.unit == "port" or l.hw_effect is not None for l in self.layers)

    def port_layers(self) -> dict[int, Layer]:
        """Which layer drives each port (last one wins)."""
        out = {}
        for layer in self.layers:
            for port in layer.ports:
                out[port] = layer
        return out


def parse_ports(text: str | list[int], groups: dict[str, list[int]] | None = None) -> list[int]:
    """'all', '3', '1,4', '5-8', group names (comma-separated) -> ports, in the order given.

    Order matters for effects that travel across ports (wave, comet, gradient): "16,15,14" runs the
    other way round from "14-16".
    """
    if isinstance(text, list):
        ports = [int(x) for x in text]
    else:
        ports = []
        for part in str(text).split(","):
            part = part.strip()
            if not part:
                continue
            if part.lower() == "all":
                ports += USED_PORTS_DEFAULT
            elif groups and part in groups:
                ports += groups[part]
            else:
                lo, _, hi = part.partition("-")
                try:
                    a, b = int(lo), int(hi or lo)
                except ValueError:
                    raise ValueError(f"bad port spec {part!r}") from None
                ports += range(a, b + 1) if a <= b else range(a, b - 1, -1)
    if any(not 1 <= x <= p.NUM_CHANNELS for x in ports):
        raise ValueError(f"ports must be 1-{p.NUM_CHANNELS}")
    return list(dict.fromkeys(ports))


def layer_from_dict(d: dict, groups: dict[str, list[int]]) -> Layer:
    kind = str(d.get("effect", "static"))
    if not kind.startswith("hw:") and kind not in EFFECTS:
        raise ValueError(f"unknown effect {kind!r}; available: {', '.join(sorted(EFFECTS))} or hw:<name>")
    colors = d.get("colors") or ([d["color"]] if "color" in d else ["white"])
    spec = EffectSpec(
        kind=kind,
        colors=[parse_color(c) for c in colors],
        period=float(d.get("period", 4.0)),
        spread=float(d.get("spread", 0.0)),
        brightness=float(d.get("brightness", 1.0)),
        reverse=bool(d.get("reverse", False)),
        interp=str(d.get("interp", "smooth")),
        params={k: v for k, v in d.items() if k not in LAYER_KEYS},
    )
    unit, span = str(d.get("unit", "port")), str(d.get("span", "group"))
    if unit not in ("port", "led") or span not in ("port", "group"):
        raise ValueError("unit must be port|led, span must be port|group")
    layer = Layer(parse_ports(d.get("ports", "all"), groups), spec, unit, span)
    layer.hw_effect  # validates hw: names
    if not layer.spec.kind.startswith("hw:"):
        spec.make()      # validates parameters early
    return layer


def from_dict(d: dict) -> Profile:
    groups = {name: parse_ports(spec) for name, spec in d.get("groups", {}).items()}
    prof = Profile(
        name=str(d.get("name", "Untitled")),
        fps=int(d.get("fps", 30)),
        leds=int(d["leds"]) if "leds" in d else None,
        port_leds={int(k): int(v["leds"]) for k, v in d.get("ports", {}).items() if "leds" in v},
        groups=groups,
        layers=[layer_from_dict(l, groups) for l in d.get("layer", [])],
    )
    if not 1 <= prof.fps <= 120:
        raise ValueError("fps must be 1..120")
    if any(not 1 <= n <= p.LEDS_PER_CHANNEL for n in [prof.leds or 1, *prof.port_leds.values()]):
        raise ValueError(f"LED counts must be 1..{p.LEDS_PER_CHANNEL}")
    if not prof.layers:
        raise ValueError("profile has no [[layer]]")
    return prof


def load(path: str | Path) -> Profile:
    with open(path, "rb") as f:
        return from_dict(tomllib.load(f))


def loads(text: str) -> Profile:
    return from_dict(tomllib.loads(text))
