"""Command-line interface: `python -m opennollie ...`."""

import argparse
import json
import sys
import threading
import time

from . import calibration, client, engine, fseq, layouts, model, profile
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


def cmd_backup(dev: Nollie16, args) -> None:
    state = DeviceState.read(dev, with_led_index=True)
    with open(args.file, "w") as f:
        json.dump(state.to_dict(), f, indent=1)
    print(f"saved the controller's settings to {args.file} (the stored animation itself is not included)")


def cmd_restore(dev: Nollie16, args) -> None:
    with open(args.file) as f:
        target = DeviceState.from_dict(json.load(f))
    if target.serial and target.serial != dev.serial:
        print(f"note: backup is from controller {target.serial}, this is {dev.serial}")
    changes = model.restore(dev, target, include_name=args.name, dry_run=args.dry_run)
    verb = "would change" if args.dry_run else "restored"
    print(f"{verb}: " + ", ".join(changes) if changes else "nothing to change: the controller already matches")


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
    print("the controller's own effects (Built-in effects page / canvases):", ", ".join(p.EFFECTS))


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
        self.daemon = dev.transport if isinstance(dev.transport, client.DaemonTransport) else None

    def show(self, frame) -> None:
        self.frame = frame
        self.dev.send_frame(frame)
        if not self.thread.is_alive():
            self.thread.start()

    def _run(self) -> None:
        while not self.stop.wait(1.0):
            self.dev.send_frame(self.frame)

    def __enter__(self):
        if self.daemon:
            self.daemon.call("live_pause")       # the daemon's live effect yields while we drive the LEDs
        return self

    def __exit__(self, *exc):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.daemon:
            self.daemon.call("live_resume")


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


def cmd_leds(dev: Nollie16, args) -> None:
    """Show or set LED counts per port (stored on the device; built-in effects use them too)."""
    counts = dev.led_counts()
    if args.assignments:
        for item in args.assignments:
            ports_s, _, n = item.partition("=")
            if not n.isdigit() or not 1 <= int(n) <= p.LEDS_PER_CHANNEL:
                sys.exit(f"bad assignment {item!r}: use PORTS=COUNT, e.g. 4-8,12-16=8 9=10")
            for port in parse_ports(ports_s):
                counts[p.PORT_TO_CH[port - 1]] = int(n)
        dev.set_led_counts(counts)
        time.sleep(0.15)
        if dev.led_counts() != counts:
            sys.exit("device did not accept the LED counts (LED + zLED must be <= 256 per port)")
    for port in range(1, p.NUM_CHANNELS + 1):
        print(f"port {port:2d}: {counts[p.PORT_TO_CH[port - 1]]:3d} LEDs")


def order_chase(order: list[int], leds: int, step: int, wire: int) -> list[RGB]:
    """A light running in logical order: head white, short tail; the first LED of the order stays dim blue."""
    frame = [(0, 0, 0)] * wire
    frame[order[0]] = (0, 0, 60)
    for k, level in ((0, 255), (1, 60), (2, 12)):
        frame[order[(step - k) % leds]] = (level,) * 3
    return frame


def _fmt_order(order: list[int], leds: int) -> str:
    lit = [x + 1 for x in order[:leds]]
    return str(lit) if len(lit) <= 24 else f"[{', '.join(map(str, lit[:12]))}, …, {', '.join(map(str, lit[-4:]))}]"


def cmd_order(dev: Nollie16, args) -> None:
    """Show or set the order effects travel over a port's LEDs (stored on the device)."""
    port, ch = args.port, p.PORT_TO_CH[args.port - 1]
    counts, zleds = dev.led_counts(), dev.zled_counts()
    if args.file:
        layout = layouts.load(args.file)
    else:
        layout = layouts.strip(counts[ch], args.dark if args.dark is not None else zleds[ch])
    changing = args.file or args.reverse or args.start or args.how != "as wired" or args.reset or args.dark is not None
    if not changing:
        cur = layouts.from_device(dev.led_index()[ch], counts[ch], zleds[ch]) or list(range(counts[ch] + zleds[ch]))
        print(f"port {port}: {counts[ch]} LEDs + {zleds[ch]} dark, order {layouts.describe(cur, counts[ch])}: "
              f"{_fmt_order(cur, counts[ch])}")
        return
    order = layouts.order(layout, args.how, args.reverse, (args.start or 1) - 1)
    print(f"port {port}: {layout.leds} LEDs + {layout.zleds} dark ({layout.name}), "
          f"order {layouts.describe(order, layout.leds)}: {_fmt_order(order, layout.leds)}")
    if args.preview:
        print("preview: a white light runs in this order (blue = first LED); Ctrl-C to stop")
        with KeepAlive(dev) as ka:
            step = 0
            try:
                while True:
                    ka.show({c: [] for c in range(p.NUM_CHANNELS)} | {ch: order_chase(order, layout.leds, step, len(order))})
                    step += 1
                    time.sleep(0.15)
            except KeyboardInterrupt:
                pass
        if input("store this order? [y/N] ").strip().lower() != "y":
            return
    layouts.apply(dev, port, order, layout.leds, layout.zleds, layout.name[:p.NAME_MAX_CHARS] if args.file else None)
    print("stored")


def ledtest_frame(n: int) -> list[RGB]:
    """LEDs 1..n-1 green, LED n blue (the last one if n is the count), LED n+1 red (shouldn't exist)."""
    return [(0, 60, 0)] * (n - 1) + [(0, 0, 255), (255, 0, 0)]


def cmd_ledtest(dev: Nollie16, args) -> None:
    port = args.port
    ch = p.PORT_TO_CH[port - 1]
    n = args.start or min(dev.led_counts()[ch], 64)
    with KeepAlive(dev) as ka:
        if args.chase:
            print(f"port {port}: a light runs along the first {n} LEDs; Ctrl-C to stop")
            step = 0
            while True:
                frame = [(0, 0, 0)] * n
                for k in range(3):
                    frame[(step - k) % n] = scale((255, 255, 255), 100 >> k)
                ka.show({c: [] for c in range(p.NUM_CHANNELS)} | {ch: frame})
                step += 1
                time.sleep(0.12)
        print(f"port {port}: green = LEDs 1..N-1, BLUE = LED N, RED = LED N+1.\n"
              "Adjust N until the blue LED is lit and no red LED is: then N is the LED count.\n"
              "(Devices with their own controller, e.g. a GPU in ARGB-sync mode, may stretch the values over\n"
              " their LEDs: then N is how many values they read, i.e. the largest N that still shows blue.)\n"
              "Enter a number, + / - (step 1), ++ / -- (step 10), or empty to accept.")
        while True:
            ka.show({c: [] for c in range(p.NUM_CHANNELS)} | {ch: ledtest_frame(n)})
            cmd = input(f"N = {n}> ").strip()
            if not cmd:
                break
            step = {"+": 1, "-": -1, "++": 10, "--": -10}.get(cmd)
            if step is not None:
                n += step
            elif cmd.isdigit():
                n = int(cmd)
            else:
                print("  ?")
            n = max(1, min(p.LEDS_PER_CHANNEL - 1, n))
    if args.save or input(f"store {n} LEDs for port {port} on the device? [y/N] ").strip().lower() == "y":
        counts = dev.led_counts()
        counts[ch] = n
        dev.set_led_counts(counts)
        print(f"port {port}: {n} LEDs stored")


def cmd_balance(args) -> None:
    """Show or set a port's color balance (red/green/blue levels in %), kept on this PC."""
    gains = calibration.load()
    if args.port is not None:
        if args.reset:
            gains.pop(args.port, None)
        elif len(args.levels) == 3:
            if any(not 0 <= x <= 100 for x in args.levels):
                sys.exit("levels are percentages, 0-100")
            gains[args.port] = tuple(x / 100 for x in args.levels)
        elif args.levels:
            sys.exit("give three levels: RED GREEN BLUE (in %), e.g.  balance 9 75 80 100")
        calibration.save(gains)
    if not gains:
        print("no color balance set (all ports at 100/100/100)")
    for port, g in sorted(gains.items()):
        print(f"port {port:2d}: red {g[0]:.0%}  green {g[1]:.0%}  blue {g[2]:.0%}")
    if args.port is not None:
        print("applies to effects you start or save from now on")


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
    """One line per phase, e.g. '  write 0% 10% … 100%'."""
    pct = 100 * done // max(total, 1) // 10 * 10
    phase_changed = phase != _last_progress[0]
    if not phase_changed and pct == _last_progress[1]:
        return
    if phase_changed:
        print(("" if _last_progress[0] is None else "\n") + f"  {phase}", end="")
    _last_progress[:] = [phase, pct]
    print(f" {pct}%", end="", flush=True)


def load_profile(dev: Nollie16, name: str) -> profile.Profile:
    return engine.prepare(dev, profile.load(name))


def cmd_live(dev: Nollie16, args) -> None:
    daemon = dev.transport if isinstance(dev.transport, client.DaemonTransport) else None
    if args.stop or args.status:
        if not daemon:
            sys.exit("the daemon isn't running (foreground live effects stop with Ctrl-C)")
        if args.stop:
            daemon.call("live_stop")
        print(daemon.call("live_status"))
        return
    if not args.profile:
        sys.exit("usage: live PROFILE | live --stop | live --status")
    if daemon and args.duration is None:
        path = profile.find(args.profile)
        name = daemon.call("live_start", profile=path.read_text(), source=str(path))["name"]
        print(f"'{name}' is running in the daemon; stop it with: opennollie live --stop")
        return
    prof = load_profile(dev, args.profile)
    print(f"streaming '{prof.name}' at {prof.fps} fps; Ctrl-C to stop "
          f"(the device returns to its stored lighting ~3 s after)")
    if daemon:
        daemon.call("live_pause")
    try:
        engine.run_live(dev, prof, duration=args.duration)
    finally:
        if daemon:
            daemon.call("live_resume")


def cmd_save(dev: Nollie16, args) -> None:
    prof = load_profile(dev, args.profile)
    print(f"saving '{prof.name}' to the device…")
    result = engine.save_to_device(dev, prof, _progress)
    print(("\n" if _last_progress[0] else "") + result)


def cmd_play(dev: Nollie16, args) -> None:
    f = Flash(dev)
    if args.file:
        data = open(args.file, "rb").read()
        hdr = fseq.parse_header(data)
        led_map = f.fseq_led_map() if args.keep_map else _led_map_arg(args.map)
        if (why := fseq.led_map_matches(hdr, led_map)) is not None:
            sys.exit(f"channel map doesn't fit the file: {why}")
        info = f.upload_fseq(data, led_map, _progress)
        print(f"\nuploaded: {info.size} bytes")
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
    sp = sub.add_parser("backup", help="save all controller settings to a JSON file (read-only)")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_backup)
    sp = sub.add_parser("restore", help="write settings from a backup file (only what differs)")
    sp.add_argument("file")
    sp.add_argument("--dry-run", action="store_true", help="only show what would change")
    sp.add_argument("--name", action="store_true", help="also restore the controller name")
    sp.set_defaults(func=cmd_restore)
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

    sp = sub.add_parser("leds", help="show or set LED counts per port (stored on the device)",
                        description="e.g.  leds   |   leds 4-8,12-16=8 9=10")
    sp.add_argument("assignments", nargs="*", metavar="PORTS=COUNT")
    sp.set_defaults(func=cmd_leds)
    sp = sub.add_parser("order", help="show or set the order effects travel over a port's LEDs",
                        description="e.g.  order 4   |   order 4 --reverse --start 3 --preview   |   "
                                    "order 9 --file 'Components/08LED_通用 - 8 LED.json' --how 'left → right'")
    sp.add_argument("port", type=int, choices=range(1, p.NUM_CHANNELS + 1), metavar="PORT")
    sp.add_argument("--file", help="SignalRGB component or Nollie vmap JSON (sets LED count, dark LEDs and order)")
    sp.add_argument("--how", default="as wired", choices=layouts.ORDERS, help="needs a component file with coordinates")
    sp.add_argument("--reverse", action="store_true", help="run the other way round")
    sp.add_argument("--start", type=int, help="LED (1-based, as wired) the order starts at")
    sp.add_argument("--dark", type=int, help="number of dark LEDs after the lit ones (strips only)")
    sp.add_argument("--reset", action="store_true", help="back to the plain wiring order")
    sp.add_argument("--preview", action="store_true", help="show a running light in the new order first")
    sp.set_defaults(func=cmd_order)
    sp = sub.add_parser("ledtest", help="interactive: find how many LEDs a port has")
    sp.add_argument("port", type=int, choices=range(1, p.NUM_CHANNELS + 1), metavar="PORT")
    sp.add_argument("--start", type=int, help="initial guess (default: stored count, max 64)")
    sp.add_argument("--chase", action="store_true", help="show a running light instead (Ctrl-C to stop)")
    sp.add_argument("--save", action="store_true", help="store the result without asking")
    sp.set_defaults(func=cmd_ledtest)

    sp = sub.add_parser("static", help="stored: solid colors per port, kept on the device (unlisted ports: no canvas)",
                        description="e.g.  static 4,5 red 6-8 00ff00 9 white   (uses --brightness)")
    sp.add_argument("assignments", nargs="+", metavar="PORTS COLOR")
    sp.set_defaults(func=cmd_static)

    sp = sub.add_parser("mode", help="show or set the mode (hardware = built-in effects)")
    sp.add_argument("mode", nargs="?", choices=[m.name.lower() for m in Mode])
    sp.set_defaults(func=cmd_mode)

    sp = sub.add_parser("live", help="run a profile live (in the daemon if it's running, else until Ctrl-C)")
    sp.add_argument("profile", nargs="?", help="a profile file, or a name (see `opennollie profiles`)")
    sp.add_argument("--stop", action="store_true", help="stop the daemon's live effect")
    sp.add_argument("--status", action="store_true", help="show the daemon's live effect")
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
    sp = sub.add_parser("balance", help="color balance per port (red/green/blue %%), e.g. to match a GPU to the fans",
                        description="e.g.  balance   |   balance 9 75 80 100   |   balance 9 --reset")
    sp.add_argument("port", type=int, nargs="?", choices=range(1, p.NUM_CHANNELS + 1), metavar="PORT")
    sp.add_argument("levels", type=int, nargs="*", metavar="R G B")
    sp.add_argument("--reset", action="store_true")
    sp.set_defaults(func=cmd_balance, offline=True)
    sp = sub.add_parser("profiles", help="list your profiles and the examples")
    sp.set_defaults(func=lambda args: print("\n".join(
        f"{f.stem:<22} {f.parent}" for f in profile.available())), offline=True)
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
        with client.connect() as dev:
            args.func(dev, args)
    except (DeviceNotFound, NoResponse, client.DaemonError, ValueError, OSError) as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        pass
