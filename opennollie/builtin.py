"""Built-in effects with color balance: the groups of ports you set up ↔ the controller's canvases.

The controller draws its built-in effects itself, with one set of colors per canvas, and has no color
balance of its own. So a group whose ports need different balance (e.g. fans and a GPU with weaker blue)
is written as one canvas per balance, each with the balance applied to its colors. The groups as you set
them up are remembered on the PC (state.py) and shown again as long as the controller still has what was
written for them; otherwise its canvases are shown as they are, one group each.

Effects that make their own colors (the rainbows) or take them from a palette can't be balanced this way.
"""

import json
from dataclasses import asdict, dataclass, replace

from . import calibration, state
from . import protocol as p
from .device import Nollie16
from .protocol import BootSteady, CanvasLayout, CanvasParams

COLORS = ("color1", "color2", "color3")


@dataclass
class Group:
    """Ports running one built-in effect, as set up in the app."""
    ports: list[int]
    params: CanvasParams
    frame_delay: int = 33
    palette: int = 0
    blend: int = 0


@dataclass
class Canvas:
    """One canvas as on the controller."""
    chans: list[int]                 # fw channels
    params: CanvasParams
    frame_delay: int = 33
    palette: int = 0
    blend: int = 0
    group: int = 0                   # index of the group it was made from


def effect_name(params: CanvasParams, boot: bool = False) -> str:
    names = p.START_EFFECTS if boot else p.EFFECTS
    return names[params.effect] if params.effect < len(names) else ""


def balanceable(params: CanvasParams, boot: bool = False) -> bool:
    """Does the effect draw with the group's colors? (The rainbows and the palette effect make their own.)"""
    return bool(p.EFFECT_PARAMS.get(effect_name(params, boot), set()) & {"color1", "color2"})


def expand(groups: list[Group], gains: dict, boot: bool = False) -> tuple[list[Canvas], bool]:
    """Groups → canvases with the color balance applied. Returns (canvases, complete); complete is False if
    the controller hasn't enough canvases to split every group that needs it (then none is split)."""
    def build(split: bool) -> list[Canvas]:
        out = []
        for gi, g in enumerate(groups):
            parts: dict = {}
            for pt in g.ports:
                parts.setdefault(gains.get(pt) if balanceable(g.params, boot) else None, []).append(pt)
            if len(parts) > 1 and not split:
                parts = {None: list(g.ports)}
            for gain, ports in parts.items():
                prm = replace(g.params, ch_count=len(ports),
                              **{k: calibration.apply(tuple(getattr(g.params, k)), gain) for k in COLORS})
                out.append(Canvas([p.PORT_TO_CH[pt - 1] for pt in ports], prm, g.frame_delay, g.palette, g.blend, gi))
        return out
    out = build(True)
    if len(out) <= (p.BOOT_CANVAS_MAX if boot else p.LOOP_CANVAS_MAX):
        return out, True
    return build(False), False


def _arrays(dev: Nollie16) -> list[list[int]]:
    """Per-canvas frame delay, palette group (dynamic 1) and blend (dynamic 2), 16 entries each."""
    return [dev.frame_delay(), dev.dynamic(1), dev.dynamic(2)]


def read(dev: Nollie16, boot: bool = False, arrays: list[list[int]] | None = None) -> list[Canvas]:
    """The canvases on the controller."""
    fd, d1, d2 = arrays or _arrays(dev)
    layout, params = dev.canvases(boot)
    base = p.BOOT_CANVAS_BASE if boot else 0
    return [Canvas(list(chans), prm, fd[base + i] or 33, d1[base + i], d2[base + i], i)
            for i, (chans, prm) in enumerate(zip(layout.canvases, params))]


def groups(dev: Nollie16, have: list[Canvas], boot: bool = False) -> list[Group]:
    """The groups behind the controller's canvases `have`: as set up in OpenNollie if the canvases are still
    what was written for them, else one group per canvas."""
    rec = state.get(dev.serial).get(_key(boot))
    if rec and rec.get("canvases") == _sig(have):
        try:
            return [_group(d) for d in rec["groups"]]
        except (KeyError, TypeError, ValueError):
            pass
    return [Group([p.CH_TO_PORT[ch] for ch in c.chans], c.params, c.frame_delay, c.palette, c.blend) for c in have]


def write(dev: Nollie16, groups: list[Group], gains: dict, boot: bool = False) -> tuple[list[Canvas], bool]:
    """Put the groups on the controller with the color balance applied, writing only what differs from what
    it has, and remember them. Returns what expand() returns."""
    want, complete = expand(groups, gains, boot)
    arrays = _arrays(dev)
    have = read(dev, boot, arrays)
    base = p.BOOT_CANVAS_BASE if boot else 0
    if [c.chans for c in want] != [c.chans for c in have]:
        dev.set_canvases(CanvasLayout([c.chans for c in want]), [c.params for c in want], boot=boot)
    else:
        # a group's canvases are written together, so they stay in step
        changed = {w.group for w, h in zip(want, have) if w.params != h.params}
        for i, w in enumerate(want):
            if w.group in changed:
                dev.set_canvas_params(base + i, w.params)
    new = [list(a) for a in arrays]
    for i, w in enumerate(want):
        new[0][base + i], new[1][base + i], new[2][base + i] = w.frame_delay, w.palette, w.blend
    if new[0] != list(arrays[0]):
        dev.set_frame_delay(new[0])
    for which in (1, 2):
        if new[which] != list(arrays[which]):
            dev.set_dynamic(which, new[which])
    state.remember(dev.serial, **{_key(boot): {"groups": [asdict(g) for g in groups], "canvases": _sig(want)}})
    return want, complete


def update(dev: Nollie16) -> bool | None:
    """After a color balance change: write the remembered groups again with the new balance, where the
    controller still has what was written for them. Returns None if it has none of them, False if they
    were up to date, True if rewritten. (Normal lighting is left for later while a stored animation is the
    lighting: it's rewritten when you switch back to built-in effects.)"""
    gains = calibration.load()
    result = None
    for boot in (False, True):
        rec = state.get(dev.serial).get(_key(boot))
        if not rec or (not boot and dev.boot_playlist()[1] != BootSteady.HW_EFFECTS):
            continue
        have = read(dev, boot)
        if rec.get("canvases") != _sig(have):
            continue
        grps = groups(dev, have, boot)
        if _sig(expand(grps, gains, boot)[0]) == rec["canvases"]:
            result = result or False
        else:
            write(dev, grps, gains, boot)
            result = True
    return result


def _key(boot: bool) -> str:
    return "boot" if boot else "loop"


def _sig(canvases: list[Canvas]) -> list:
    """What identifies the written canvases (JSON-normalized, to compare with the state file)."""
    return json.loads(json.dumps([[c.chans, asdict(c.params), c.frame_delay, c.palette, c.blend] for c in canvases]))


def _group(d: dict) -> Group:
    prm = CanvasParams(**{k: tuple(int(x) for x in v) if k in COLORS else int(v) for k, v in d["params"].items()})
    return Group([int(x) for x in d["ports"]], prm, int(d["frame_delay"]), int(d["palette"]), int(d["blend"]))
