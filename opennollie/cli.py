"""Command-line interface: `python -m opennollie ...`."""

import argparse
import json
import sys
import time

from . import protocol as p
from .device import DeviceNotFound, NoResponse, Nollie16
from .model import DeviceState
from .protocol import Mode, RGB

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


def live_frame(assign: dict[int, RGB], leds: int) -> dict[int, list[RGB]]:
    """port -> color  ==>  fw channel -> LED colors (all channels; unassigned ones off)."""
    frame = {ch: [(0, 0, 0)] * leds for ch in range(p.NUM_CHANNELS)}
    for port, color in assign.items():
        frame[p.PORT_TO_CH[port - 1]] = [color] * leds
    return frame


def cmd_set(dev: Nollie16, args) -> None:
    if len(args.assignments) % 2:
        sys.exit("usage: set PORTS COLOR [PORTS COLOR ...]")
    assign = {}
    for ports_s, color_s in zip(args.assignments[::2], args.assignments[1::2]):
        color = scale(parse_color(color_s), args.brightness)
        assign.update({port: color for port in parse_ports(ports_s)})
    dev.send_frame(live_frame(assign, args.leds))


def cmd_off(dev: Nollie16, args) -> None:
    dev.send_frame(live_frame({}, args.leds))


def cmd_identify(dev: Nollie16, args) -> None:
    interactive = sys.stdin.isatty() and args.delay is None
    for port in parse_ports(args.ports):
        color = scale(IDENTIFY_COLORS[port % len(IDENTIFY_COLORS)], args.brightness)
        dev.send_frame(live_frame({port: color}, args.leds))
        print(f"Port {port:2d} (fw ch {p.PORT_TO_CH[port - 1]:2d}) is lit: {fmt_rgb(color)}", flush=True)
        if interactive:
            input("  press Enter for the next port...")
        else:
            time.sleep(args.delay or 3)
    dev.send_frame(live_frame({}, args.leds))


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
    sp.set_defaults(func=cmd_set)
    sub.add_parser("off", help="live: turn all ports off").set_defaults(func=cmd_off)
    sp = sub.add_parser("identify", help="live: light ports one at a time")
    sp.add_argument("ports", nargs="?", default="all")
    sp.add_argument("--delay", type=float, help="seconds per port (default: wait for Enter)")
    sp.set_defaults(func=cmd_identify)

    sp = sub.add_parser("mode", help="show or set the mode (hardware = built-in effects)")
    sp.add_argument("mode", nargs="?", choices=[m.name.lower() for m in Mode])
    sp.set_defaults(func=cmd_mode)

    args = ap.parse_args(argv)
    if not 1 <= args.leds <= p.LEDS_PER_CHANNEL:
        ap.error(f"--leds must be 1-{p.LEDS_PER_CHANNEL}")
    if not 0 <= args.brightness <= 100:
        ap.error("--brightness must be 0-100")

    try:
        with Nollie16() as dev:
            args.func(dev, args)
    except (DeviceNotFound, NoResponse) as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass
