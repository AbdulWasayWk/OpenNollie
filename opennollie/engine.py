"""Render profiles to frames; run them live or store them on the device."""

import json
import math
import os
import threading
import time
from pathlib import Path
from collections.abc import Callable
from dataclasses import replace

from . import protocol as p
from .device import Nollie16
from .flash import FSEQ_BASE, Flash, crc32_nollie
from .fseq import build_fseq
from .profile import Layer, Profile
from .protocol import BootIntro, BootSteady, CanvasLayout, CanvasParams, Mode, RGB

FLASH_SIZE = 16 << 20
MAX_LOOP_S = 120


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
                colors = [fn(t, us[0])] * n if len(us) == 1 else [fn(t, u) for u in us]
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


def static_canvases(profile: Profile) -> tuple[CanvasLayout, list[CanvasParams]]:
    """An all-static profile as device canvases: one Solid canvas per layer that still owns ports."""
    if not profile.is_static:
        raise ValueError("profile has animated effects; render it instead")
    owner = profile.port_layers()
    chans, params = [], []
    for layer in profile.layers:
        ports = [pt for pt in layer.ports if owner.get(pt) is layer]
        if ports:
            params.append(CanvasParams(effect=p.EFFECTS.index("Solid"), brightness=round(100 * layer.spec.brightness),
                                       color1=layer.spec.colors[0]))
            chans.append([p.PORT_TO_CH[pt - 1] for pt in ports])
    if len(params) > p.LOOP_CANVAS_MAX:
        raise ValueError(f"at most {p.LOOP_CANVAS_MAX} static layers")
    return CanvasLayout(chans), params


def save_to_device(dev: Nollie16, profile: Profile, progress: Callable[[str, int, int], None] | None = None) -> str:
    """Store the profile so it runs with no host software. Returns a short description."""
    progress = progress or (lambda *_: None)
    flash = Flash(dev)
    if profile.is_static:
        layout, params = static_canvases(profile)
        dev.set_mode(Mode.HARDWARE)
        dev.set_canvases(layout, params)
        dev.set_boot_playlist(BootIntro.START_CANVAS, BootSteady.HW_EFFECTS)
        dev.set_mode(Mode.HARDWARE)
        return f"stored as {len(params)} hardware canvas(es)"
    data, led_map = render_fseq(profile, lambda i, n: progress("render", i, n))
    fingerprint = {"crc": crc32_nollie(data), "size": len(data), "led_map": led_map}
    stored = _stored_animation(dev.serial)
    info = flash.fseq_info()
    unchanged = stored == fingerprint and info.present and info.size == len(data) and flash.fseq_led_map() == led_map
    if not unchanged:        # (checked without touching flash: any flash access pauses the LEDs)
        info = flash.upload_fseq(data, led_map, progress)
        if not info.present:
            raise IOError("device does not report the uploaded animation")
        _stored_animation(dev.serial, fingerprint)
    dev.set_boot_playlist(BootIntro.NONE, BootSteady.FSEQ_LOOP)
    if not flash.play(loop=True):
        flash.play(loop=True)
    n, _ = loop_frames(profile)
    what = "already stored, now playing" if unchanged else f"stored as animation, {len(data) // 1024} KiB"
    return f"{what}: {n} frames ({n / profile.fps:.1f} s loop)"


STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "opennollie" / "stored.json"


def _stored_animation(serial: str, fingerprint: dict | None = None) -> dict | None:
    """Remember (or recall) what we last uploaded to this controller, so an unchanged profile isn't
    rewritten: the controller pauses its LEDs while its flash is written."""
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    if fingerprint is None:
        return state.get(serial)
    state[serial] = fingerprint
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
    return fingerprint


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
