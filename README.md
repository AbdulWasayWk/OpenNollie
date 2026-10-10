# OpenNollie

Open-source Linux app for the **Nollie 16CH** RGB controller: per-port colors, custom effects, and
**save-to-device**, so your lighting keeps running with no software and survives reboots.

- **Per-port colors**, live or stored on the controller
- **Effects:** rainbow, breathe, wave, comet, flicker, gradient, color cycle, blink, and fully **custom
  keyframe timelines**, per port or flowing across all ports in the order you choose
- **Save to device:** effects are rendered into a seamless loop and stored in the controller's flash; it plays
  at power-on with nothing running on the PC
- The controller's own **24 built-in effects** in groups of ports, a **startup animation**, palettes, idle fallback
- **LED counts and LED order** per port, with an on-device LED test, and SignalRGB / Nollie layout presets
- **Color balance** per port, so devices with different LEDs (e.g. a GPU) show the same colors as the fans
- An **editable picture of your case**: arrange your fans and strips (daisy-chained fans too) for the previews
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

**Arch / CachyOS, system-wide** (recommended; `makepkg -si` installs the dependencies, builds the latest version
from GitHub and installs the app, the service, the device access rule, the app-menu entry and the tray at login):

```bash
sudo pacman -S --needed git base-devel
git clone https://github.com/AbdulWasayWk/OpenNollie.git
cd OpenNollie/packaging && makepkg -si
systemctl --user enable --now opennollie
```

**For your user only, from a checkout** (no package; puts `opennollie`, `opennollie-gui` and `opennollie-tray` in
`~/.local/bin`, adds the app-menu entry, the tray at login and the service, all running the code in the checkout):

```bash
sudo pacman -S --needed git python-hidapi pyside6
git clone https://github.com/AbdulWasayWk/OpenNollie.git
cd OpenNollie && tools/install-user.sh
```

Use one of the two, not both (to switch from the user install to the package, run `tools/install-user.sh --remove`
first). Then open **OpenNollie** from the app menu; the tray icon starts at your next login (or run
`opennollie-tray`).

**Updating:** package: run `makepkg -si` again in `OpenNollie/packaging`. User install: `git pull` in the checkout.
Then restart the service: `systemctl --user restart opennollie`.

## Uninstall

The controller keeps its saved lighting either way and runs it without the app.

**Package:**

```bash
systemctl --user disable --now opennollie
pkill -f opennollie.gui.tray
sudo pacman -Rns opennollie-git
```

If you switched the tray at login on or off on the *Settings* page, also delete
`~/.config/autostart/opennollie-tray.desktop`.

**User install** (from the checkout; then you can delete the `OpenNollie` folder):

```bash
tools/install-user.sh --remove
```

**Your settings stay** so a reinstall picks them up. To remove them too (this deletes your profiles, case layout
and color balance): `~/.config/opennollie/` and `~/.local/state/opennollie/`. Effects, port names, LED counts and the
stored animation live on the controller; to wipe those, run *Factory reset* on the *Device* page before uninstalling.

## Using it

**Desktop app** (*OpenNollie* in the app menu, or `opennollie-gui`):

| Page | What it does |
|---|---|
| **Effects studio** | Pick a profile from the dropdown (yours, then the examples) or make one: layers of ports + effect + colors + speed + pattern (in sync / flowing). Watch it on the case preview, *Preview on device* (live), **Save to device** (stored). The last profile you edited reopens next time |
| **Built-in effects** | The controller's own effects. *Normal lighting*: groups of ports, each with an effect, showing only the settings that effect uses. *Startup animation*: plays once when the PC turns on. Changes apply as you make them, with each port's color balance |
| **Ports & LEDs** | Port names, LED counts, *Identify*, *LED test* (find the count), *LED order* (reverse, start LED, presets), *Color balance* (match a device's colors to the others, e.g. a GPU) |
| **Layout** | The case picture used for previews: add fans and strips, drag them into place, set their port (and which LEDs, for daisy chains), size, angle and name; light one up on the device to find it |
| **Palettes** | The controller's 8 palettes of 16 colors. Only the built-in effect *ColorFromPalette* uses them: give a group that effect on *Built-in effects* and pick a palette. The page shows which groups use each palette |
| **Stored animation** | What's in the controller's flash; play/stop; upload an FSEQ file |
| **Device** | Name, firmware, power readings, startup behaviour, idle fallback, backup/restore, restart, factory reset |
| **Settings** | Whether the tray icon and the background service start when you log in |

**Tray icon:** *Run live* or *Save to device* any profile, stop the live effect, open the app. It starts at login
unless you turn that off in *Settings*; the app window never opens by itself.

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
opennollie balance 9 75 80 100                   # color balance of a port (red/green/blue %); updates the saved profile
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
- LED counts and LED order come from the controller unless a profile sets `leds`; color balance comes from
  `~/.config/opennollie/calibration.toml`. Previews show the intended colors; the balance only changes what is
  sent to a device so that it looks like the preview.

**Color balance:** the controller has no color balance of its own, so OpenNollie adjusts the colors it gives it:
live effects on the fly, saved profiles when they are written to the controller (static colors and animations alike),
and built-in effects. The controller draws a built-in effect with one set of colors per group, so a group whose ports
need different balance (e.g. fans and a GPU) is stored as one controller group per balance, each with adjusted
colors; the app still shows it as your one group. Rainbows and the palette effect can't be balanced (the controller
makes those colors itself); the app says so on the group. When you change a balance (or an LED count) later,
everything OpenNollie put on the controller is updated automatically.

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
  stretch them over their LEDs (per-LED patterns get smoothed), and their colors can differ from the fans'
  (e.g. weaker blue): fix that with *Color balance*.
- The 16CH firmware has no automatic LED-count detection or power test; use `ledtest` / *LED test*.

## Your files

| File | What |
|---|---|
| `~/.config/opennollie/profiles/*.toml` | your profiles |
| `~/.config/opennollie/layout.json` | the case picture |
| `~/.config/opennollie/calibration.toml` | color balance per port |
| `~/.local/state/opennollie/` | the last edited profile, and what OpenNollie put on the controller (your built-in effect groups, the last saved profile), to update it when the balance or LED counts change |

The controller itself keeps its effects, LED counts, LED orders, port names and the stored animation.

## Troubleshooting

- **"Nollie 16CH 16d5:2a16 not found":** check `lsusb | grep -i nollie`. `3061:4716` means old firmware (see
  Requirements). If a VM used the controller, it may not be bound to Linux again; replug, or
  `tools/setup-dev.sh bind`.
- **Permission denied:** install the udev rule (the package does), then replug.
- **Service:** `systemctl --user status opennollie`, logs with `journalctl --user -u opennollie`.

## Development

```bash
python3 -m unittest discover tests        # unit + end-to-end tests against a simulated controller (no hardware)
python3 -m tests.hw_roundtrip             # writes test values to a real controller and restores them
```

- `opennollie/protocol.py`, `device.py`, `flash.py`, `fseq.py`: the controller protocol, documented in
  [docs/PROTOCOL.md](docs/PROTOCOL.md)
- `effects.py`, `profile.py`, `engine.py`, `layouts.py`, `calibration.py`, `caselayout.py`: effects, profiles,
  rendering, LED orders, color balance, the case picture
- `daemon.py`, `client.py`, `autostart.py`: the background service and login switches; `cli.py`; `gui/`: the Qt
  app and tray. `tests/fakedevice.py` simulates the controller for offline tests
- `tools/`: USB capture and analysis helpers used to work out the protocol
- [CLAUDE.md](CLAUDE.md): project notes, decisions, and the release checklist

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
