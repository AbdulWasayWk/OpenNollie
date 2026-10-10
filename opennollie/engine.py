"""Render profiles to frames; run them live or store them on the device."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from . import builtin, calibration, layouts, state
from . import protocol as p
from .device import Nollie16
from .flash import FSEQ_BASE, Flash, crc32_nollie
from .fseq import build_fseq
from .profile import Layer, Profile, dumps as dumps_profile, loads as loads_profile
from .protocol import BootIntro, BootSteady, CanvasLayout, CanvasParams, Mode, RGB

FLASH_SIZE = 16 << 20
MAX_LOOP_S = 120
UP_TO_DATE = "already up to date"


class Renderer:
    """Turns a (software) profile into frames: fw channel -> LED colors."""

    def __init__(self, profile: Profile):
        self.profile = profile
        owner = profile.port_layers()
        self._jobs = []        # (effect fn, [(port, [u per LED] or u)])
        for layer in profile.layers:
            ports = [pt for pt in layer.ports if owner.get(pt) is layer]
            if ports:
                self._jobs.append((layer.spec.make(), self._positions(layer, ports)))
        self.ports = sorted(owner)

    def _positions(self, layer: Layer, ports: list[int]) -> list[tuple[int, list[float]]]:
        """Per port, the position u of each pixel (one entry for unit=port)."""
        all_ports = layer.ports            # positions span the layer as written, even if ports are overridden
        idx = {pt: i for i, pt in enumerate(all_ports)}
        if layer.unit == "port":
            n = len(all_ports)
            return [(pt, [idx[pt] / n]) for pt in ports]
        if layer.span == "port":
            out = []
            for pt in ports:
                n = self.profile.leds_for(pt)
                out.append((pt, [i / n for i in range(n)]))
            return out
        total = sum(self.profile.leds_for(pt) for pt in all_ports)
        start, offs = 0, {}
        for pt in all_ports:
            offs[pt] = start
            start += self.profile.leds_for(pt)
        return [(pt, [(offs[pt] + i) / total for i in range(self.profile.leds_for(pt))]) for pt in ports]

    def frame(self, t: float) -> dict[int, list[RGB]]:
        """fw channel -> colors of the LEDs on the wire (the port's LED order applied, dark LEDs off)."""
        out = {}
        orders = self.profile.port_orders
        for fn, targets in self._jobs:
            for port, us in targets:
                n = self.profile.leds_for(port)
                gain = self.profile.port_gains.get(port)
                if len(us) == 1:
                    colors = [calibration.apply(fn(t, us[0]), gain)] * n
                else:
                    colors = [calibration.apply(fn(t, u), gain) for u in us]
                order = orders.get(port)
                if order:
                    wire = [(0, 0, 0)] * self.profile.wire_len(port)
                    for i, c in enumerate(colors):
                        wire[order[i]] = c
                    colors = wire
                out[p.PORT_TO_CH[port - 1]] = colors
        return out


def loop_frames(profile: Profile) -> tuple[int, list[float]]:
    """Frames in one seamless loop: the LCM of every layer's period (in whole frames), capped.

    Returns (frame count, quantized period per layer). Periods are rounded to whole frames so the
    loop closes exactly; if the LCM is too long, the longest period is used and shorter, non-dividing
    periods will show a small seam.
    """
    fps = profile.fps
    lens = [max(1, round(l.spec.period * fps)) for l in profile.layers]
    animated = [k for k, l in zip(lens, profile.layers) if l.spec.animated] or [1]
    n = 1
    for k in animated:
        n = n * k // math.gcd(n, k)
    if n > MAX_LOOP_S * fps:
        n = max(animated)
    return n, [k / fps for k in lens]


def render_fseq(profile: Profile, progress: Callable[[int, int], None] | None = None) -> tuple[bytes, list[int]]:
    """Render one seamless loop to an FSEQ file. Returns (file, FSEQ channel map)."""
    n, periods = loop_frames(profile)
    quantized = replace(profile, layers=[replace(l, spec=replace(l.spec, period=q))
                                         for l, q in zip(profile.layers, periods)])
    r = Renderer(quantized)
    led_map = [0] * p.NUM_CHANNELS
    for port in r.ports:
        led_map[p.PORT_TO_CH[port - 1]] = profile.wire_len(port)
    frame_bytes = sum(led_map) * 3
    if FSEQ_BASE + 64 + n * frame_bytes > FLASH_SIZE:
        raise ValueError(f"animation too large: {n} frames × {frame_bytes} B; lower fps, LEDs or period")

    def frames():
        for i in range(n):
            if progress and i % 30 == 0:
                progress(i, n)
            yield r.frame(i / profile.fps)
    data = build_fseq(led_map, round(1000 / profile.fps), frames())
    return data, led_map


def static_groups(profile: Profile) -> list[builtin.Group]:
    """An all-static profile as built-in groups: one Solid group per layer that still owns ports."""
    if not profile.is_static:
        raise ValueError("profile has animated effects; render it instead")
    owner = profile.port_layers()
    out = []
    for layer in profile.layers:
        ports = [pt for pt in layer.ports if owner.get(pt) is layer]
        if ports:
            out.append(builtin.Group(ports, CanvasParams(effect=p.EFFECTS.index("Solid"),
                                                         brightness=round(100 * layer.spec.brightness),
                                                         color1=tuple(layer.spec.colors[0]))))
    if len(out) > p.LOOP_CANVAS_MAX:
        raise ValueError(f"at most {p.LOOP_CANVAS_MAX} static layers")
    return out


def static_canvases(profile: Profile) -> tuple[CanvasLayout, list[CanvasParams]]:
    """What a static profile becomes on the controller (ports with different color balance get their own canvas)."""
    canvases, _ = builtin.expand(static_groups(profile), profile.port_gains)
    return CanvasLayout([c.chans for c in canvases]), [c.params for c in canvases]


def prepare(dev: Nollie16, profile: Profile) -> Profile:
    """Fill in what lives outside the profile: the controller's LED counts and LED orders, and the
    PC-side color balance per port."""
    return profile.with_device_leds(*layouts.read_ports(dev)).with_calibration(calibration.load())


def save_to_device(dev: Nollie16, profile: Profile, progress: Callable[[str, int, int], None] | None = None,
                   source: Profile | None = None) -> str:
    """Store the profile so it runs with no host software. Returns a short description.

    `profile` comes from prepare() (LED counts, LED orders and color balance filled in). `source` is the
    profile as written; when given it's remembered, so update_saved() can store it again after the color
    balance or the LED counts change (they are baked into what the controller stores)."""
    progress = progress or (lambda *_: None)
    if profile.is_static:
        dev.set_mode(Mode.HARDWARE)
        canvases, _ = builtin.write(dev, static_groups(profile), profile.port_gains)
        dev.set_boot_playlist(BootIntro.START_CANVAS, BootSteady.HW_EFFECTS)
        dev.set_mode(Mode.HARDWARE)
        state.remember(dev.serial, saved=_saved_entry("canvases", source))
        return f"stored as {len(canvases)} hardware canvas(es)"
    rendered = _render_stored(profile, progress)
    return _store_animation(dev, profile, rendered, progress, source)


def save_profile(dev: Nollie16, source: Profile, progress: Callable[[str, int, int], None] | None = None) -> str:
    """Save a profile as written (not yet prepared) to the controller, and remember it for update_saved()."""
    return save_to_device(dev, prepare(dev, source), progress, source=source)


def update_saved(dev: Nollie16, progress: Callable[[str, int, int], None] | None = None) -> str | None:
    """After a color balance or LED count change: store again what OpenNollie put on the controller, with the
    current color balance, LED counts and LED orders: built-in effect groups (from the Built-in effects page or
    a static profile) and the animation last saved from the studio. Only where the controller still has what
    was written; lighting set up some other way is left alone. Returns what happened, UP_TO_DATE, or None if
    there was nothing of ours to update."""
    progress = progress or (lambda *_: None)
    done = []
    found = builtin.update(dev)
    if found:
        done.append("built-in effects updated")
    saved = state.get(dev.serial).get("saved")
    if saved and saved.get("kind") == "animation" and saved.get("profile"):
        flash, anim = Flash(dev), state.get(dev.serial).get("animation") or {}
        info = flash.fseq_info()      # (not a flash access: these don't pause the LEDs)
        if (dev.boot_playlist()[1] == BootSteady.FSEQ_LOOP and info.present and info.size == anim.get("size")
                and flash.fseq_led_map() == anim.get("led_map")):
            found = bool(found)
            source = loads_profile(saved["profile"])
            prof = prepare(dev, source)
            rendered = _render_stored(prof, progress)
            if rendered[2] != anim:
                done.append(_store_animation(dev, prof, rendered, progress, source))
    if done:
        return "; ".join(done)
    return None if found is None else UP_TO_DATE


def _render_stored(profile: Profile, progress) -> tuple[bytes, list[int], dict]:
    data, led_map = render_fseq(profile, lambda i, n: progress("render", i, n))
    return data, led_map, {"crc": crc32_nollie(data), "size": len(data), "led_map": led_map}


def _store_animation(dev: Nollie16, profile: Profile, rendered: tuple[bytes, list[int], dict], progress,
                     source: Profile | None) -> str:
    data, led_map, fingerprint = rendered
    flash = Flash(dev)
    info = flash.fseq_info()
    unchanged = (state.get(dev.serial).get("animation") == fingerprint and info.present and info.size == len(data)
                 and flash.fseq_led_map() == led_map)
    if not unchanged:        # (checked without touching flash: any flash access pauses the LEDs)
        info = flash.upload_fseq(data, led_map, progress)
        if not info.present:
            raise IOError("device does not report the uploaded animation")
        state.remember(dev.serial, animation=fingerprint)
    dev.set_boot_playlist(BootIntro.NONE, BootSteady.FSEQ_LOOP)
    if not flash.play(loop=True):
        flash.play(loop=True)
    state.remember(dev.serial, saved=_saved_entry("animation", source))
    n, _ = loop_frames(profile)
    what = "already stored, now playing" if unchanged else f"stored as animation, {len(data) // 1024} KiB"
    return f"{what}: {n} frames ({n / profile.fps:.1f} s loop)"


def _saved_entry(kind: str, source: Profile | None, **extra) -> dict:
    return {"kind": kind, "profile": dumps_profile(source) if source else None, **extra}


def run_live(dev: Nollie16, profile: Profile, stop: threading.Event | None = None,
             duration: float | None = None) -> None:
    """Stream the profile until `stop` is set (or `duration` seconds pass)."""
    r = Renderer(profile)
    stop = stop or threading.Event()
    blank = {ch: [] for ch in range(p.NUM_CHANNELS)}
    dt = 1 / profile.fps
    t0 = time.monotonic()
    next_t = t0
    while not stop.is_set():
        now = time.monotonic()
        if duration is not None and now - t0 >= duration:
            break
        dev.send_frame(blank | r.frame(now - t0))
        next_t += dt
        delay = next_t - time.monotonic()
        if delay > 0:
            stop.wait(delay)
        else:
            next_t = time.monotonic()       # fell behind; don't try to catch up
