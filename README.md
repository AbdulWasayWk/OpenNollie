# OpenNollie

Open-source Linux app for the **Nollie 16CH** RGB controller: per-port colors, custom effects, and
**save-to-device**, so your lighting keeps running with no software and survives reboots.

- **Per-port colors**, live or stored on the controller
- **Effects:** rainbow, breathe, wave, comet, flicker, gradient, color cycle, blink, and fully **custom
  keyframe timelines**, per port or flowing across all ports in the order you choose
- **Save to device:** effects are rendered into a seamless loop and stored in the controller's flash; it plays
  at power-on with nothing running on the PC
- The controller's own **24 built-in effects** (canvases), palettes, boot animations, idle fallback
- **LED counts and LED order** per port, with an on-device LED test, and SignalRGB / Nollie layout presets
- Upload **xLights FSEQ** animations; **backup and restore** of all controller settings; power readings
- **Desktop app** (Qt) with a live preview of your case, a **tray icon** for quick switching, a **CLI**, and a
  small background **service**

> Not affiliated with Nollie. Tested on one Nollie 16CH (firmware 2.3.0) on CachyOS / KDE Plasma 6.

## Requirements

- A **Nollie 16CH on firmware 2.3.0** (USB ID `16d5:2a16`). Controllers on older firmware show up as
  `3061:4716`; update them **once** with the official NollieRGB 2.3.0 app on Windows (a VM with USB passthrough
  works). OpenNollie never touches firmware.
- Linux with Python ≥ 3.11, `python-hidapi`, `pyside6` (Arch package names)
- Access to the device: the udev rule in `packaging/70-opennollie.rules` (OpenRGB's rules also cover it)

## Install

**Arch / CachyOS, system-wide:**

```bash
cd packaging && makepkg -si
systemctl --user enable --now opennollie
```

**From a checkout, for your user only** (no root; puts `opennollie`, `opennollie-gui` and `opennollie-tray` in
`~/.local/bin`, adds the app-menu entry, the tray at login and the service):

```bash
sudo pacman -S --needed python-hidapi pyside6
tools/install-user.sh            # undo with: tools/install-user.sh --remove
```

Use one of the two, not both.

## Using it

**Desktop app** (*OpenNollie* in the app menu, or `opennollie-gui`):

| Page | What it does |
|---|---|
| **Effects studio** | Build a profile from layers (ports + effect + colors + speed …), watch it on the case preview, *Preview on device* (live), **Save to device** (stored), save/open profiles |
| **Built-in effects** | The controller's own effects: canvases of ports, each with an effect and settings; boot canvases that play once at power-on |
| **Ports & LEDs** | Port names, LED counts, *Identify*, *LED test* (find the count), *LED order* (reverse, start LED, presets) |
| **Palettes** | The controller's 8 palette groups |
| **Stored animation** | What's in the controller's flash; play/stop; upload an FSEQ file |
| **Device** | Name, firmware, power readings, startup behaviour, idle fallback, backup/restore, restart, factory reset |

**Tray icon:** *Run live* or *Save to device* any profile, stop the live effect, open the app.

**CLI** (`opennollie --help` for everything):

```bash
opennollie info                                  # what's on the controller (read-only)
opennollie static 4,5 red 6-8 00ff00 9 white     # stored solid colors per port
opennollie save airflow-rainbow                  # store a profile (name or path) on the controller
opennollie live calm-breathe                     # run a profile live (in the service); --stop to end
opennollie profiles                              # your profiles and the examples
opennollie leds 4-8,12-16=8 9=6                  # LED counts per port
opennollie ledtest 9                             # interactive: find a port's LED count
opennollie order 4 --reverse --start 3 --preview # LED order of a port
opennollie backup settings.json                  # all controller settings; restore with: opennollie restore
opennollie power                                 # input voltage/current, MCU temperature
```

Ports are numbered **1–16** as on the board (the official app's "Channel N").

## Profiles

Profiles are small TOML files in `~/.config/opennollie/profiles/` (examples ship in `opennollie/examples/`).
Layers apply in order; a later layer wins on ports it shares with an earlier one.

```toml
name = "Evening"
fps = 30

[groups]
intake = "8,7,6,15,14,16"          # order = the direction effects travel

[[layer]]
ports = "intake"
effect = "keyframes"               # your own color timeline
period = 12                        # seconds per cycle
spread = 0.25                      # neighbouring ports lag behind each other
keyframes = [[0.0, "#ff2000"], [0.3, "#ff8000"], [0.6, "#a000ff"], [0.85, "#200060"]]

[[layer]]
ports = "4,5,12,13"
effect = "breathe"
colors = ["#0040ff", "#8000ff"]
period = 6
brightness = 0.7
```

- **Effects:** `static`, `gradient`, `breathe`, `cycle`, `rainbow`, `wave`, `comet`, `blink`, `flicker`,
  `keyframes` (`opennollie effects`). The controller's own built-in effects are set on the *Built-in effects* page.
- `spread = 0` (the default) keeps every port **in sync**; `spread > 0` makes the pattern **flow** across the ports
  (1 = one full cycle across them). `unit = "led"` runs it along the LEDs instead: `span = "port"` around each fan
  (fans in sync), `span = "group"` along all LEDs as one strip.
- LED counts and LED order come from the controller unless a profile sets `leds`.

**How "Save to device" stores a profile:** if every layer is `static`, it becomes the controller's built-in
Solid canvases (instant). Otherwise the whole loop is rendered (frames = the least common multiple of the layers'
cycle times, up to 120 s) and stored as an animation that plays at power-on. While the controller writes a new
animation to its memory, its LEDs pause for about 2 seconds (firmware behaviour; the official app has it too).
Saving an unchanged profile again skips the write.

## Limits

- The controller holds **either** built-in canvases **or** one stored animation as its startup lighting, not a mix.
- **Live** effects need the service (or the app) running; the controller drops live output ~3 s after the last
  frame and returns to its stored lighting.
- Devices with their own lighting controller in ARGB-sync mode (e.g. some GPUs) may read only a few values and
  stretch them over their LEDs. Per-port colors stay exact; per-LED patterns get smoothed.
- The 16CH firmware has no automatic LED-count detection or power test; use `ledtest` / *LED test*.

## Troubleshooting

- **"Nollie 16CH 16d5:2a16 not found":** check `lsusb | grep -i nollie`. `3061:4716` means old firmware (see
  Requirements). If a VM used the controller, it may not be bound to Linux again; replug, or
  `tools/setup-dev.sh bind`.
- **Permission denied:** install the udev rule (the package does), then replug.
- **Service:** `systemctl --user status opennollie`, logs with `journalctl --user -u opennollie`.

## Development

```bash
python3 -m unittest discover tests        # unit tests, no hardware needed
python3 -m tests.hw_roundtrip             # writes test values to a real controller and restores them
```

- `opennollie/protocol.py`, `device.py`, `flash.py`, `fseq.py`: the controller protocol, documented in
  [docs/PROTOCOL.md](docs/PROTOCOL.md)
- `effects.py`, `profile.py`, `engine.py`, `layouts.py`: effects, profiles, rendering, LED orders
- `daemon.py`, `client.py`: the background service; `cli.py`; `gui/`: the Qt app and tray
- `tools/`: USB capture and analysis helpers used to work out the protocol
- [CLAUDE.md](CLAUDE.md): project notes, decisions, and the release checklist

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
