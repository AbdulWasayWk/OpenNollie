"""Command-line interface: `python -m opennollie ...`."""

import argparse
import sys
import time

from . import protocol
from .device import DeviceNotFound, Nollie16
from .protocol import RGB

NAMED_COLORS = {
    "off": (0, 0, 0), "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "orange": (255, 100, 0), "purple": (128, 0, 255),
}

# Distinct colors for `identify`, so neighbouring ports are easy to tell apart.
IDENTIFY_COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def parse_color(text: str) -> RGB:
    text = text.strip().lower()
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]
    hexstr = text.removeprefix("#")
    if len(hexstr) != 6:
        raise argparse.ArgumentTypeError(f"bad color {text!r}: use a name or RRGGBB hex")
    try:
        return tuple(int(hexstr[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad color {text!r}: use a name or RRGGBB hex") from None


def parse_ports(text: str) -> list[int]:
    """'all', '3', '1,4', '5-8' (1-based) -> sorted 0-based port indices."""
    if text.strip().lower() == "all":
        return list(range(protocol.NUM_PORTS))
    ports = set()
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        try:
            lo_i, hi_i = int(lo), int(hi or lo)
        except ValueError:
            raise argparse.ArgumentTypeError(f"bad port list {text!r}") from None
        if not 1 <= lo_i <= hi_i <= protocol.NUM_PORTS:
            raise argparse.ArgumentTypeError(f"ports must be 1-{protocol.NUM_PORTS}: {text!r}")
        ports.update(range(lo_i - 1, hi_i))
    return sorted(ports)


def scale(color: RGB, brightness: int) -> RGB:
    return tuple(c * brightness // 100 for c in color)


def cmd_set(dev: Nollie16, args) -> None:
    if len(args.assignments) % 2:
        sys.exit("usage: set PORTS COLOR [PORTS COLOR ...]")
    frame = {p: [(0, 0, 0)] * args.leds for p in range(protocol.NUM_PORTS)}
    for ports_s, color_s in zip(args.assignments[::2], args.assignments[1::2]):
        color = scale(parse_color(color_s), args.brightness)
        for p in parse_ports(ports_s):
            frame[p] = [color] * args.leds
    dev.send_frame(frame)


def cmd_identify(dev: Nollie16, args) -> None:
    interactive = sys.stdin.isatty() and args.delay is None
    for port in parse_ports(args.ports):
        color = scale(IDENTIFY_COLORS[port % len(IDENTIFY_COLORS)], args.brightness)
        frame = {p: [(0, 0, 0)] * args.leds for p in range(protocol.NUM_PORTS)}
        frame[port] = [color] * args.leds
        dev.send_frame(frame)
        print(f"Port {port + 1:2d} (wire ch {protocol.PORT_TO_HW[port]}) is lit: rgb{color}", flush=True)
        if interactive:
            input("  press Enter for the next port...")
        else:
            time.sleep(args.delay or 3)
    dev.send_frame({p: [(0, 0, 0)] * args.leds for p in range(protocol.NUM_PORTS)})


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="opennollie", description="Control a Nollie 16CH RGB controller.")
    ap.add_argument("--leds", type=int, default=protocol.MAX_LEDS_PER_PORT,
                    help="LEDs to drive per port (default: %(default)s, the max)")
    ap.add_argument("--brightness", type=int, default=100, metavar="0-100")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("set", help="set ports to solid colors; unlisted ports turn off",
                       description="e.g.  set all red   |   set 1-4 ff0000 5,6 00ff00")
    p.add_argument("assignments", nargs="+", metavar="PORTS COLOR")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("off", help="turn all ports off")
    p.set_defaults(func=lambda dev, args: dev.send_frame(
        {i: [(0, 0, 0)] * args.leds for i in range(protocol.NUM_PORTS)}))

    p = sub.add_parser("identify", help="light ports one at a time to see which is which")
    p.add_argument("ports", nargs="?", default="all")
    p.add_argument("--delay", type=float, help="seconds per port (default: wait for Enter)")
    p.set_defaults(func=cmd_identify)

    args = ap.parse_args(argv)
    if not 1 <= args.leds <= protocol.MAX_LEDS_PER_PORT:
        ap.error(f"--leds must be 1-{protocol.MAX_LEDS_PER_PORT}")
    if not 0 <= args.brightness <= 100:
        ap.error("--brightness must be 0-100")

    try:
        with Nollie16() as dev:
            args.func(dev, args)
    except DeviceNotFound as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass
