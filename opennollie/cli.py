"""Command-line interface: `python -m opennollie ...`."""

import argparse
import json
import sys
import threading
import time

from . import engine, fseq, profile
from . import protocol as p
from .device import DeviceNotFound, NoResponse, Nollie16
from .flash import Flash
from .model import DeviceState
from .protocol import CanvasLayout, CanvasParams, Mode, RGB

NAMED_COLORS = {
    "off": (0, 0, 0), "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "orange": (255, 100, 0), "purple": (128, 0, 255),
}
IDENTIFY_COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def parse_color(text: str) -> RGB:
    text = text.strip().lower()
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]
    hexstr = text.removeprefix("#")
    try:
        if len(hexstr) != 6:
            raise ValueError
        return tuple(int(hexstr[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad color {text!r}: use a name or RRGGBB hex") from None


def parse_ports(text: str) -> list[int]:
    """'all', '3', '1,4', '5-8' (board port numbers, 1-based) -> sorted ports."""
    if text.strip().lower() == "all":
        return list(range(1, p.NUM_CHANNELS + 1))
    ports = set()
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        try:
            lo_i, hi_i = int(lo), int(hi or lo)
        except ValueError:
            raise argparse.ArgumentTypeError(f"bad port list {text!r}") from None
        if not 1 <= lo_i <= hi_i <= p.NUM_CHANNELS:
            raise argparse.ArgumentTypeError(f"ports must be 1-{p.NUM_CHANNELS}: {text!r}")
        ports.update(range(lo_i, hi_i + 1))
    return sorted(ports)


def scale(color: RGB, brightness: int) -> RGB:
    return tuple(c * brightness // 100 for c in color)


def fmt_ports(chans: list[int]) -> str:
    return ",".join(str(p.CH_TO_PORT[ch]) for ch in chans) or "-"


def fmt_rgb(c: RGB) -> str:
    return "#%02x%02x%02x" % tuple(c)


def effect_name(idx: int, boot: bool) -> str:
    names = p.START_EFFECTS if boot else p.EFFECTS
    return names[idx] if idx < len(names) else f"#{idx}"


# --- commands ---------------------------------------------------------------------------------

def cmd_info(dev: Nollie16, args) -> None:
    s = DeviceState.read(dev)
    print(f"Nollie 16CH  serial {s.serial}  fw {s.fw_version} (cfg {s.config_version}, hw {s.hardware})")
    print(f"name: {s.name or '-'}   mode: {s.mode.name.lower()}   flash writes: {s.write_count}")
    print(f"boot playlist: intro={s.boot_playlist[0]} steady={s.boot_playlist[1]}   "
          f"idle fallback: {s.ol_idle_s or 'off'}{'s' if s.ol_idle_s else ''}")
    for label, cs, boot in (("Loop", s.loop, False), ("Boot", s.boot, True)):
        print(f"\n{label} canvases ({len(cs.params)}), {p.frame_ms_to_fps(cs.frame_ms):.0f} fps ({cs.frame_ms} ms):")
        for i, (chans, prm) in enumerate(zip(cs.layout.canvases, cs.params)):
            idx = (p.BOOT_CANVAS_BASE if boot else 0) + i
            print(f"  [{idx:2d}] {effect_name(prm.effect, boot):<16} ports {fmt_ports(chans):<24} "
                  f"bright {prm.brightness:3d} step {prm.step} size {prm.size} delay {prm.delay} "
                  f"colors {fmt_rgb(prm.color1)} {fmt_rgb(prm.color2)} {fmt_rgb(prm.color3)}")
    print("\nPort  LEDs  zLED  vmap name")
    for port in range(1, p.NUM_CHANNELS + 1):
        ch = p.PORT_TO_CH[port - 1]
        print(f"  {port:2d}  {s.led_counts[ch]:4d}  {s.zled_counts[ch]:4d}  {s.vmap_names[ch] or '-'}")


def cmd_dump(dev: Nollie16, args) -> None:
    json.dump(DeviceState.read(dev, with_led_index=args.vmap).to_dict(), sys.stdout, indent=1)
    print()


def cmd_power(dev: Nollie16, args) -> None:
    v = dev.voltage()
    units = {name: unit for name, _, _, unit in p.VOLTAGE_FIELDS}
    for name, value in v.items():
        print(f"{name:<14} {value:8.3f} {units[name]}")


def cmd_render(args) -> None:
    prof = profile.load(args.profile)
    data, led_map = engine.render_fseq(prof)
    open(args.output, "wb").write(data)
    hdr = fseq.parse_header(data)
    ports = ",".join(f"{p.CH_TO_PORT[ch]}:{n}" for ch, n in enumerate(led_map) if n)
    print(f"{args.output}: {hdr.frame_count} frames @ {1000 // hdr.step_ms} fps, {len(data) // 1024} KiB; "
          f"upload with: opennollie play {args.output} --map {ports}")


def cmd_effects(args) -> None:
    from .effects import EFFECTS
    print("software effects:", ", ".join(sorted(EFFECTS)))
    print("hardware effects (effect = \"hw:<name>\"):", ", ".join(p.EFFECTS))


def live_frame(assign: dict[int, RGB], leds: int) -> dict[int, list[RGB]]:
    """port -> color  ==>  fw channel -> LED colors (all channels; unassigned ones off)."""
    frame = {ch: [(0, 0, 0)] * leds for ch in range(p.NUM_CHANNELS)}
    for port, color in assign.items():
        frame[p.PORT_TO_CH[port - 1]] = [color] * leds
    return frame


class KeepAlive:
    """Re-send the current live frame every second: the device drops live output ~3 s after the last
    frame (idle timeout) and returns to its stored lighting."""

    def __init__(self, dev: Nollie16):
        self.dev, self.frame = dev, None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def show(self, frame) -> None:
        self.frame = frame
        self.dev.send_frame(frame)
        if not self.thread.is_alive():
            self.thread.start()

    def _run(self) -> None:
        while not self.stop.wait(1.0):
            self.dev.send_frame(self.frame)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop.set()


def hold(args) -> None:
    if args.duration is None:
        print("holding; Ctrl-C to stop (the device returns to its stored lighting ~3 s after)", flush=True)
        threading.Event().wait()
    time.sleep(args.duration)


def cmd_set(dev: Nollie16, args) -> None:
    if len(args.assignments) % 2:
        sys.exit("usage: set PORTS COLOR [PORTS COLOR ...]")
    assign = {}
    for ports_s, color_s in zip(args.assignments[::2], args.assignments[1::2]):
        color = scale(parse_color(color_s), args.brightness)
        assign.update({port: color for port in parse_ports(ports_s)})
    with KeepAlive(dev) as ka:
        ka.show(live_frame(assign, args.leds))
        hold(args)


def cmd_off(dev: Nollie16, args) -> None:
    with KeepAlive(dev) as ka:
        ka.show(live_frame({}, args.leds))
        hold(args)


def cmd_identify(dev: Nollie16, args) -> None:
    interactive = sys.stdin.isatty() and args.delay is None
    with KeepAlive(dev) as ka:
        for port in parse_ports(args.ports):
            color = scale(IDENTIFY_COLORS[port % len(IDENTIFY_COLORS)], args.brightness)
            ka.show(live_frame({port: color}, args.leds))
            print(f"Port {port:2d} (fw ch {p.PORT_TO_CH[port - 1]:2d}) is lit: {fmt_rgb(color)}", flush=True)
            if interactive:
                input("  press Enter for the next port...")
            else:
                time.sleep(args.delay or 3)


def cmd_static(dev: Nollie16, args) -> None:
    """Stored per-port solid colors: ports sharing a color share one Solid canvas."""
    if len(args.assignments) % 2:
        sys.exit("usage: static PORTS COLOR [PORTS COLOR ...]")
    port_color: dict[int, RGB] = {}
    for ports_s, color_s in zip(args.assignments[::2], args.assignments[1::2]):
        color = parse_color(color_s)
        port_color.update({port: color for port in parse_ports(ports_s)})
    groups: dict[RGB, list[int]] = {}
    for port, color in port_color.items():
        groups.setdefault(color, []).append(p.PORT_TO_CH[port - 1])
    if len(groups) > p.LOOP_CANVAS_MAX:
        sys.exit(f"at most {p.LOOP_CANVAS_MAX} different colors (one canvas each)")
    solid = p.EFFECTS.index("Solid")
    dev.set_canvases(
        CanvasLayout(list(groups.values())),
        [CanvasParams(effect=solid, brightness=args.brightness, color1=c) for c in groups],
    )
    dev.set_mode(Mode.HARDWARE)


_last_progress: list = [None, -1]


def _progress(phase: str, done: int, total: int) -> None:
    """Print each phase's progress in 10 % steps."""
    pct = 100 * done // max(total, 1) // 10 * 10
    if [phase, pct] != _last_progress:
        _last_progress[:] = [phase, pct]
        end = "\n" if pct >= 100 else ""
        start = "\n" if pct == 0 and phase != "render" else ""
        print(f"{start}  {phase} {pct}%" if pct == 0 else f" {pct}%", end=end, flush=True)


def cmd_live(dev: Nollie16, args) -> None:
    prof = profile.load(args.profile)
    print(f"streaming '{prof.name}' at {prof.fps} fps; Ctrl-C to stop "
          f"(the device returns to its stored lighting ~3 s after)")
    engine.run_live(dev, prof, duration=args.duration)


def cmd_save(dev: Nollie16, args) -> None:
    prof = profile.load(args.profile)
    print(f"saving '{prof.name}' to the device…")
    print(engine.save_to_device(dev, prof, _progress))


def cmd_play(dev: Nollie16, args) -> None:
    f = Flash(dev)
    if args.file:
        data = open(args.file, "rb").read()
        hdr = fseq.parse_header(data)
        led_map = f.fseq_led_map() if args.keep_map else _led_map_arg(args.map)
        if (why := fseq.led_map_matches(hdr, led_map)) is not None:
            sys.exit(f"channel map doesn't fit the file: {why}")
        print(f.upload_fseq(data, led_map, _progress))
    print("playing" if f.play(loop=not args.once) else "device did not start playback")


def _led_map_arg(spec: str | None) -> list[int]:
    """'4:32,9:40' (port:LEDs) -> FSEQ channel map by fw channel."""
    if not spec:
        sys.exit("give --map PORT:LEDS,... (or --keep-map) so the device knows how the file maps to ports")
    led_map = [0] * p.NUM_CHANNELS
    for item in spec.split(","):
        port, _, n = item.partition(":")
        led_map[p.PORT_TO_CH[int(port) - 1]] = int(n)
    return led_map


def cmd_stop(dev: Nollie16, args) -> None:
    Flash(dev).stop()
    dev.set_mode(Mode.HARDWARE)


def cmd_mode(dev: Nollie16, args) -> None:
    if args.mode is None:
        print(dev.mode().name.lower())
        return
    dev.set_mode(Mode[args.mode.upper()])


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="opennollie", description="Control a Nollie 16CH RGB controller (fw 2.3.0).")
    ap.add_argument("--leds", type=int, default=p.LEDS_PER_CHANNEL,
                    help="LEDs to drive per port for live commands (default: %(default)s)")
    ap.add_argument("--brightness", type=int, default=100, metavar="0-100", help="scale live colors")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="show the device configuration (read-only)").set_defaults(func=cmd_info)
    sp = sub.add_parser("dump", help="dump the device configuration as JSON (read-only)")
    sp.add_argument("--vmap", action="store_true", help="include the per-LED index maps")
    sp.set_defaults(func=cmd_dump)
    sub.add_parser("power", help="input voltage/current, MCU temperature (read-only)").set_defaults(func=cmd_power)

    sp = sub.add_parser("set", help="live: set ports to solid colors; unlisted ports turn off",
                        description="e.g.  set all red   |   set 1-4 ff0000 5,6 00ff00")
    sp.add_argument("assignments", nargs="+", metavar="PORTS COLOR")
    sp.add_argument("--duration", type=float, help="seconds to hold (default: until Ctrl-C)")
    sp.set_defaults(func=cmd_set)
    sp = sub.add_parser("off", help="live: turn all ports off")
    sp.add_argument("--duration", type=float, help="seconds to hold (default: until Ctrl-C)")
    sp.set_defaults(func=cmd_off)
    sp = sub.add_parser("identify", help="live: light ports one at a time")
    sp.add_argument("ports", nargs="?", default="all")
    sp.add_argument("--delay", type=float, help="seconds per port (default: wait for Enter)")
    sp.set_defaults(func=cmd_identify)

    sp = sub.add_parser("static", help="stored: solid colors per port, kept on the device (unlisted ports: no canvas)",
                        description="e.g.  static 4,5 red 6-8 00ff00 9 white   (uses --brightness)")
    sp.add_argument("assignments", nargs="+", metavar="PORTS COLOR")
    sp.set_defaults(func=cmd_static)

    sp = sub.add_parser("mode", help="show or set the mode (hardware = built-in effects)")
    sp.add_argument("mode", nargs="?", choices=[m.name.lower() for m in Mode])
    sp.set_defaults(func=cmd_mode)

    sp = sub.add_parser("live", help="stream a profile live (runs until Ctrl-C)")
    sp.add_argument("profile")
    sp.add_argument("--duration", type=float, help="stop after this many seconds")
    sp.set_defaults(func=cmd_live)
    sp = sub.add_parser("save", help="store a profile on the device (runs with no software)")
    sp.add_argument("profile")
    sp.set_defaults(func=cmd_save)
    sp = sub.add_parser("play", help="play the stored animation, or upload an FSEQ file first")
    sp.add_argument("file", nargs="?", help="FSEQ v2 file to upload (uncompressed, colors in GRB order)")
    sp.add_argument("--map", help="PORT:LEDS,... for the file's ranges, e.g. 4:32,5:32,9:40")
    sp.add_argument("--keep-map", action="store_true", help="reuse the channel map already on the device")
    sp.add_argument("--once", action="store_true", help="play once instead of looping")
    sp.set_defaults(func=cmd_play)
    sub.add_parser("stop", help="stop the stored animation, back to built-in effects").set_defaults(func=cmd_stop)

    # Offline commands (no device needed)
    sp = sub.add_parser("render", help="render a profile to an FSEQ file (no device needed)")
    sp.add_argument("profile")
    sp.add_argument("-o", "--output", required=True)
    sp.set_defaults(func=cmd_render, offline=True)
    sp = sub.add_parser("effects", help="list software and hardware effects")
    sp.set_defaults(func=cmd_effects, offline=True)

    args = ap.parse_args(argv)
    if getattr(args, "offline", False):
        try:
            args.func(args)
        except (ValueError, OSError) as e:
            sys.exit(str(e))
        return
    if not 1 <= args.leds <= p.LEDS_PER_CHANNEL:
        ap.error(f"--leds must be 1-{p.LEDS_PER_CHANNEL}")
    if not 0 <= args.brightness <= 100:
        ap.error("--brightness must be 0-100")

    try:
        with Nollie16() as dev:
            args.func(dev, args)
    except (DeviceNotFound, NoResponse, ValueError, OSError) as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass
