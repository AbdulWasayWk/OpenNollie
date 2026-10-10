"""Per-port color balance: red/green/blue levels that make different devices show the same color.

Devices differ: e.g. a GPU whose own lighting controller has weaker blue LEDs shows cyan as greenish
and white as yellowish next to the fans. The controller has no such setting, so OpenNollie keeps the
levels on the PC (~/.config/opennollie/calibration.toml) and applies them when rendering effects
(live and saved to the device) and static colors.

    [ports.9]
    gain = [0.75, 0.8, 1.0]      # red, green, blue (0..1)
"""

import tomllib
from pathlib import Path

RGB = tuple[int, int, int]
Gain = tuple[float, float, float]

PATH = Path.home() / ".config" / "opennollie" / "calibration.toml"
NEUTRAL: Gain = (1.0, 1.0, 1.0)


def load(path: Path | None = None) -> dict[int, Gain]:
    try:
        data = tomllib.loads((path or PATH).read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for port, entry in data.get("ports", {}).items():
        try:
            g = tuple(max(0.0, min(1.0, float(x))) for x in entry["gain"])
            if len(g) == 3 and g != NEUTRAL:
                out[int(port)] = g
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save(gains: dict[int, Gain], path: Path | None = None) -> None:
    lines = ["# OpenNollie: per-port color balance (red, green, blue levels, 0..1).",
             "# Edit in the app: Ports & LEDs → Color balance, or: opennollie balance PORT R G B", ""]
    for port in sorted(gains):
        g = gains[port]
        if tuple(g) != NEUTRAL:
            lines += [f"[ports.{port}]", "gain = [" + ", ".join(f"{x:.3f}" for x in g) + "]", ""]
    path = path or PATH          # (looked up per call, so tests can point PATH elsewhere)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def apply(color: RGB, gain: Gain | None) -> RGB:
    if not gain:
        return color
    return tuple(max(0, min(255, round(c * k))) for c, k in zip(color, gain))
