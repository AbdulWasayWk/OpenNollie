"""Render profiles to frames; run them live or store them on the device."""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from . import protocol as p
from .device import Nollie16
from .flash import FSEQ_BASE, Flash
from .fseq import build_fseq
from .profile import Layer, Profile
from .protocol import BootIntro, BootSteady, CanvasLayout, CanvasParams, Mode, RGB

FLASH_SIZE = 16 << 20
MAX_LOOP_S = 120


class Renderer:
    """Turns a (software) profile into frames: fw channel -> LED colors."""

    def __init__(self, profile: Profile):
        if any(l.hw_effect is not None for l in profile.layers):
            raise ValueError("hw: effects run on the device; they can't be rendered or mixed with software effects")
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
        out = {}
        for fn, targets in self._jobs:
            for port, us in targets:
                n = self.profile.leds_for(port)
                colors = [fn(t, us[0])] * n if len(us) == 1 else [fn(t, u) for u in us]
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
        led_map[p.PORT_TO_CH[port - 1]] = profile.leds_for(port)
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


def hardware_canvases(profile: Profile) -> tuple[CanvasLayout, list[CanvasParams]]:
    """Map an all-hardware profile to device canvases (one per layer that still owns ports)."""
    if not profile.is_hardware:
        raise ValueError("profile uses software effects; render it instead")
    owner = profile.port_layers()
    chans, params = [], []
    for layer in profile.layers:
        ports = [pt for pt in layer.ports if owner.get(pt) is layer]
        if not ports:
            continue
        s = layer.spec
        cols = (s.colors + [(255, 255, 0), (0, 255, 255)])[:3]
        effect = layer.hw_effect if layer.hw_effect is not None else p.EFFECTS.index("Solid")
        params.append(CanvasParams(
            effect=effect, brightness=round(100 * s.brightness),
            step=int(s.params.get("step", 1)), size=int(s.params.get("size", 1)),
            delay=int(s.params.get("delay", 0)), vmap_process=int(s.params.get("vmap", 0)),
            color1=cols[0], color2=cols[1], color3=cols[2],
        ))
        chans.append([p.PORT_TO_CH[pt - 1] for pt in ports])
    if len(params) > p.LOOP_CANVAS_MAX:
        raise ValueError(f"at most {p.LOOP_CANVAS_MAX} hardware layers")
    return CanvasLayout(chans), params


def save_to_device(dev: Nollie16, profile: Profile, progress: Callable[[str, int, int], None] | None = None) -> str:
    """Store the profile so it runs with no host software. Returns a short description."""
    progress = progress or (lambda *_: None)
    flash = Flash(dev)
    if profile.is_hardware:
        layout, params = hardware_canvases(profile)
        flash.stop()
        dev.set_canvases(layout, params)
        dev.set_boot_playlist(BootIntro.START_CANVAS, BootSteady.HW_EFFECTS)
        dev.set_mode(Mode.HARDWARE)
        return f"stored as {len(params)} hardware canvas(es)"
    data, led_map = render_fseq(profile, lambda i, n: progress("render", i, n))
    info = flash.upload_fseq(data, led_map, progress)
    if not info.present:
        raise IOError("device does not report the uploaded animation")
    dev.set_boot_playlist(BootIntro.NONE, BootSteady.FSEQ_LOOP)
    if not flash.play(loop=True):
        flash.play(loop=True)
    n, _ = loop_frames(profile)
    return f"stored as animation: {n} frames ({n / profile.fps:.1f} s loop), {len(data) // 1024} KiB"


def run_live(dev: Nollie16, profile: Profile, stop: threading.Event | None = None,
             duration: float | None = None) -> None:
    """Stream the profile until `stop` is set (or `duration` seconds pass)."""
    r = Renderer(profile)      # rejects hw: layers, which only run on the device
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
