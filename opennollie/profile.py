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
    effect = "rainbow"          # see effects.EFFECTS
    period = 8                  # seconds per cycle
    spread = 1.0                # 0 = every port in sync; > 0 = the pattern flows across ports/LEDs
    brightness = 0.6
    unit = "port"               # "port": each port one color | "led": per LED
    span = "group"              # with unit = "led": "port" (each port its own strip) or "group"
    colors = ["#ff0000", "blue"]
    # any other keys go to the effect's params, e.g. keyframes = [[0, "red"], [0.5, "blue"]]

A profile whose layers are all `static` (whole-port colors) is stored on the device as built-in
canvases; anything else is rendered to an animation (FSEQ) or streamed live. The controller's own
effects are edited on the GUI's Built-in effects page, not in profiles.
"""

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import protocol as p
from .effects import EFFECTS, EffectSpec, parse_color

USED_PORTS_DEFAULT = list(range(1, p.NUM_CHANNELS + 1))
USER_PROFILES = Path.home() / ".config" / "opennollie" / "profiles"
EXAMPLE_PROFILES = Path(__file__).resolve().parent / "examples"
LAYER_KEYS = {"ports", "effect", "colors", "color", "period", "spread", "brightness", "reverse",
              "interp", "unit", "span"}


@dataclass
class Layer:
    ports: list[int]
    spec: EffectSpec
    unit: str = "port"
    span: str = "group"


@dataclass
class Profile:
    name: str = "Untitled"
    fps: int = 30
    leds: int | None = None                 # None: use the device's stored counts (fallback 32)
    port_leds: dict[int, int] = field(default_factory=dict)
    groups: dict[str, list[int]] = field(default_factory=dict)
    layers: list[Layer] = field(default_factory=list)
    # Per port: logical -> physical LED order (lit LEDs, then dark ones), from the device. Not saved in profiles.
    port_orders: dict[int, list[int]] = field(default_factory=dict)
    # Per port: red/green/blue levels (calibration.py), applied when rendering. Not saved in profiles.
    port_gains: dict[int, tuple[float, float, float]] = field(default_factory=dict)

    def leds_for(self, port: int) -> int:
        return self.port_leds.get(port, self.leds or 32)

    def wire_len(self, port: int) -> int:
        """LEDs on the wire for this port (lit + dark)."""
        order = self.port_orders.get(port)
        return max(len(order), max(order) + 1) if order else self.leds_for(port)

    def with_device_leds(self, counts_by_port: dict[int, int],
                         orders_by_port: dict[int, list[int]] | None = None) -> "Profile":
        """Fill in what the profile leaves open from the device: LED counts (unless the profile sets
        `leds`) and the LED order of each port (when it matches the LED count used)."""
        prof = self
        if self.leds is None:
            merged = {pt: n for pt, n in counts_by_port.items() if 1 <= n <= p.LEDS_PER_CHANNEL} | self.port_leds
            prof = replace(prof, port_leds=merged)
        if orders_by_port:
            orders = {pt: o for pt, o in orders_by_port.items()
                      if o and counts_by_port.get(pt) == prof.leds_for(pt)}
            prof = replace(prof, port_orders=orders)
        return prof

    def with_calibration(self, gains: dict[int, tuple[float, float, float]]) -> "Profile":
        return replace(self, port_gains=dict(gains))

    @property
    def is_static(self) -> bool:
        """True if every layer is a static whole-port color: stored as built-in Solid canvases."""
        return all(l.spec.kind == "static" and l.unit == "port" for l in self.layers)

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
    if kind not in EFFECTS:
        hint = " (the controller's built-in effects are set on the Built-in effects page)" if kind.startswith("hw:") else ""
        raise ValueError(f"unknown effect {kind!r}; available: {', '.join(sorted(EFFECTS))}{hint}")
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
    spec.make()          # validates parameters early
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


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(c)


def _ports_text(ports: list[int]) -> str:
    """[4,5,6,7,9] -> '4-7,9' (keeps order; collapses ascending runs)."""
    out, i = [], 0
    while i < len(ports):
        j = i
        while j + 1 < len(ports) and ports[j + 1] == ports[j] + 1:
            j += 1
        out.append(str(ports[i]) if j - i < 2 else f"{ports[i]}-{ports[j]}")
        if 0 < j - i < 2:
            out[-1:] = [str(x) for x in ports[i : j + 1]]
        i = j + 1
    return ",".join(out)


def layer_to_dict(layer: Layer) -> dict:
    s = layer.spec
    d = {"ports": _ports_text(layer.ports), "effect": s.kind}
    if s.kind != "rainbow" and s.kind != "keyframes":
        d["colors"] = [_hex(c) for c in s.colors]
    d |= {"period": s.period, "spread": s.spread, "brightness": s.brightness}
    if s.reverse:
        d["reverse"] = True
    if s.interp != "smooth":
        d["interp"] = s.interp
    if layer.unit != "port":
        d |= {"unit": layer.unit, "span": layer.span}
    for k, v in s.params.items():
        if k == "keyframes":
            v = [[float(pos), c if isinstance(c, str) else _hex(c)] for pos, c in v]
        d[k] = v
    return d


def to_dict(prof: Profile) -> dict:
    d = {"name": prof.name, "fps": prof.fps}
    if prof.leds is not None:
        d["leds"] = prof.leds
    if prof.groups:
        d["groups"] = {k: _ports_text(v) for k, v in prof.groups.items()}
    if prof.port_leds:
        d["ports"] = {str(k): {"leds": v} for k, v in sorted(prof.port_leds.items())}
    d["layer"] = [layer_to_dict(l) for l in prof.layers]
    return d


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(v, 4)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"can't write {type(v).__name__} to TOML")


def dumps(prof: Profile) -> str:
    """Profile -> TOML text (round-trips through loads)."""
    d = to_dict(prof)
    lines = [f"{k} = {_toml_value(d[k])}" for k in ("name", "fps", "leds") if k in d]
    if "groups" in d:
        lines += ["", "[groups]"] + [f"{k} = {_toml_value(v)}" for k, v in d["groups"].items()]
    for port, v in d.get("ports", {}).items():
        lines += ["", f"[ports.{port}]", f"leds = {v['leds']}"]
    for layer in d["layer"]:
        lines += ["", "[[layer]]"] + [f"{k} = {_toml_value(v)}" for k, v in layer.items()]
    return "\n".join(lines) + "\n"


def save(prof: Profile, path: str | Path) -> None:
    Path(path).write_text(dumps(prof))


def find(name: str | Path) -> Path:
    """A profile path from a path, or a name looked up in the user's profiles, then the examples."""
    path = Path(name).expanduser()
    if path.exists():
        return path
    for d in (USER_PROFILES, EXAMPLE_PROFILES):
        for cand in (d / str(name), d / f"{name}.toml"):
            if cand.is_file():
                return cand
    raise FileNotFoundError(f"no profile {str(name)!r} (looked in {USER_PROFILES} and the examples)")


def available() -> list[Path]:
    """User profiles, then examples (user ones shadow examples with the same file name)."""
    seen, out = set(), []
    for d in (USER_PROFILES, EXAMPLE_PROFILES):
        for f in sorted(d.glob("*.toml")) if d.is_dir() else []:
            if f.name not in seen:
                seen.add(f.name)
                out.append(f)
    return out


def load(path: str | Path) -> Profile:
    with open(find(path), "rb") as f:
        return from_dict(tomllib.load(f))


def loads(text: str) -> Profile:
    return from_dict(tomllib.loads(text))
