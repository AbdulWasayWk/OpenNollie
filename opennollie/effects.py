"""Software effects: pure functions of (time, position) -> RGB.

Every effect is periodic in time with its `period` (seconds), so any combination can be rendered
into a seamless loop and stored on the device as an FSEQ animation.

`u` is the pixel's position in [0, 1): the port's place within its group, or the LED's place along
its port / group (see engine.Layer). `spread` scales how much of a cycle the positions span: with
spread 1 a rainbow shows every hue once across the group, with 0 every pixel shows the same color.
"""

import colorsys
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

RGB = tuple[int, int, int]
Effect = Callable[[float, float], RGB]


def parse_color(text: str | Sequence[int]) -> RGB:
    if not isinstance(text, str):
        r, g, b = text
        return int(r), int(g), int(b)
    named = {
        "off": "000000", "black": "000000", "white": "ffffff", "red": "ff0000", "green": "00ff00",
        "blue": "0000ff", "yellow": "ffff00", "cyan": "00ffff", "magenta": "ff00ff", "orange": "ff6400",
        "purple": "8000ff", "pink": "ff3080", "warmwhite": "ffb070",
    }
    hexstr = named.get(text.strip().lower(), text.strip().lower().removeprefix("#"))
    if len(hexstr) != 6:
        raise ValueError(f"bad color {text!r}: use a name or RRGGBB hex")
    return tuple(int(hexstr[i : i + 2], 16) for i in (0, 2, 4))


def lerp(a: RGB, b: RGB, x: float) -> RGB:
    return tuple(round(ca + (cb - ca) * x) for ca, cb in zip(a, b))


def scale(c: RGB, k: float) -> RGB:
    return tuple(max(0, min(255, round(v * k))) for v in c)


def ease(x: float, kind: str) -> float:
    if kind == "step":
        return 0.0
    if kind == "smooth":
        return x * x * (3 - 2 * x)
    return x  # linear


def palette_at(colors: Sequence[RGB], x: float, interp: str = "linear", wrap: bool = True) -> RGB:
    """Color at x in [0,1) along evenly spaced colors (wrapping back to the first if `wrap`)."""
    n = len(colors)
    if n == 1:
        return colors[0]
    segs = n if wrap else n - 1
    pos = (x % 1.0) * segs if wrap else min(max(x, 0.0), 1.0) * segs
    i = min(int(pos), segs - 1)
    return lerp(colors[i], colors[(i + 1) % n], ease(pos - i, interp))


def _phase(t: float, u: float, period: float, spread: float, reverse: bool) -> float:
    p = t / period + (-u if reverse else u) * spread
    return p % 1.0


@dataclass
class EffectSpec:
    """A configured effect. `make()` returns the render function."""
    kind: str
    colors: list[RGB] = field(default_factory=lambda: [(255, 255, 255)])
    period: float = 4.0
    spread: float = 0.0
    brightness: float = 1.0
    reverse: bool = False
    interp: str = "smooth"              # linear | smooth | step
    params: dict = field(default_factory=dict)

    @property
    def animated(self) -> bool:
        """False for effects that don't change over time (they don't constrain the loop length)."""
        return not (self.kind == "static" or (self.kind == "gradient" and not self.params.get("scroll")))

    def make(self) -> Effect:
        if self.kind not in EFFECTS:
            raise ValueError(f"unknown effect {self.kind!r}; available: {', '.join(sorted(EFFECTS))}")
        if self.period <= 0:
            raise ValueError("period must be > 0")
        fn = EFFECTS[self.kind](self)
        k = self.brightness
        return fn if k == 1.0 else (lambda t, u: scale(fn(t, u), k))


# --- built-in effects ---------------------------------------------------------------------------

def _static(s: EffectSpec) -> Effect:
    c = s.colors[0]
    return lambda t, u: c


def _gradient(s: EffectSpec) -> Effect:
    """Colors spread across the positions; scrolls if `params.scroll` is true."""
    scroll = bool(s.params.get("scroll", False))
    def f(t, u):
        x = _phase(t, u, s.period, 1.0, s.reverse) if scroll else u
        return palette_at(s.colors, x, s.interp, wrap=scroll)
    return f


def _breathe(s: EffectSpec) -> Effect:
    """Fade in and out; with several colors, each breath uses the next color."""
    floor = float(s.params.get("min", 0.0))
    n = len(s.colors)
    def f(t, u):
        p = _phase(t, u, s.period, s.spread, s.reverse) * n
        i = int(p) % n
        level = floor + (1 - floor) * (0.5 - 0.5 * math.cos(2 * math.pi * (p - int(p))))
        return scale(s.colors[i], level)
    return f


def _cycle(s: EffectSpec) -> Effect:
    """Step through the colors, cross-fading (interp) between them, once per period."""
    return lambda t, u: palette_at(s.colors, _phase(t, u, s.period, s.spread, s.reverse), s.interp)


def _rainbow(s: EffectSpec) -> Effect:
    sat = float(s.params.get("saturation", 1.0))
    def f(t, u):
        r, g, b = colorsys.hsv_to_rgb(_phase(t, u, s.period, s.spread, s.reverse), sat, 1.0)
        return round(r * 255), round(g * 255), round(b * 255)
    return f


def _wave(s: EffectSpec) -> Effect:
    """A smooth band of colors[0] travelling over colors[1] (default black)."""
    fg = s.colors[0]
    bg = s.colors[1] if len(s.colors) > 1 else (0, 0, 0)
    width = float(s.params.get("width", 0.5))
    spread = s.spread or 1.0
    def f(t, u):
        d = _phase(t, u, s.period, spread, not s.reverse)
        d = min(d, 1 - d) * 2                     # 0 at the crest, 1 opposite
        k = max(0.0, 1 - d / max(width, 1e-6))
        return lerp(bg, fg, 0.5 - 0.5 * math.cos(math.pi * k))
    return f


def _comet(s: EffectSpec) -> Effect:
    """A head with a fading tail running across the positions."""
    head = s.colors[0]
    bg = s.colors[1] if len(s.colors) > 1 else (0, 0, 0)
    tail = float(s.params.get("tail", 0.3))
    spread = s.spread or 1.0
    def f(t, u):
        behind = _phase(t, u, s.period, spread, not s.reverse)
        k = max(0.0, 1 - behind / max(tail, 1e-6))
        return lerp(bg, head, k * k)
    return f


def _blink(s: EffectSpec) -> Effect:
    """Hard on/off (strobe with a small duty); each flash uses the next color."""
    duty = float(s.params.get("duty", 0.5))
    n = len(s.colors)
    def f(t, u):
        p = _phase(t, u, s.period, s.spread, s.reverse) * n
        return s.colors[int(p) % n] if (p - int(p)) < duty else (0, 0, 0)
    return f


def _flicker(s: EffectSpec) -> Effect:
    """Candle/fire flicker between the colors; loops seamlessly every period."""
    amount = float(s.params.get("amount", 0.6))
    harmonics = (3, 7, 11, 17)
    def f(t, u):
        x = (t / s.period) % 1.0
        seed = u * 97.13
        n = sum(math.sin(2 * math.pi * (h * x + seed * h * 0.37)) / h for h in harmonics)
        level = max(0.0, min(1.0, 0.5 + 0.5 * n / 0.7))
        c = palette_at(s.colors, level, "linear", wrap=False)
        return scale(c, 1 - amount + amount * level)
    return f


def _keyframes(s: EffectSpec) -> Effect:
    """User-defined: colors at given points of the cycle. params.keyframes = [[pos 0..1, color], ...]."""
    raw = s.params.get("keyframes")
    if not raw:
        raise ValueError("keyframes effect needs params.keyframes = [[0, color], [0.5, color], ...]")
    frames = sorted((float(pos) % 1.0, parse_color(c)) for pos, c in raw)
    def f(t, u):
        x = _phase(t, u, s.period, s.spread, s.reverse)
        for (p0, c0), (p1, c1) in zip(frames, frames[1:] + [(frames[0][0] + 1, frames[0][1])]):
            xx = x if x >= p0 else x + 1
            if p0 <= xx < p1:
                return lerp(c0, c1, ease((xx - p0) / (p1 - p0), s.interp))
        return frames[0][1]
    return f


EFFECTS: dict[str, Callable[[EffectSpec], Effect]] = {
    "static": _static,
    "gradient": _gradient,
    "breathe": _breathe,
    "cycle": _cycle,
    "rainbow": _rainbow,
    "wave": _wave,
    "comet": _comet,
    "blink": _blink,
    "flicker": _flicker,
    "keyframes": _keyframes,
}
