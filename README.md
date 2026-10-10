# OpenNollie

Open-source Linux app for the **Nollie 16CH** RGB controller: per-port colors, custom effects, and
save-to-device.

> **Status:** work in progress. Protocol, effects engine, CLI and daemon are working and tested on real
> hardware; the GUI is awaiting real-world testing. See the *Definition of done* checklist in
> [CLAUDE.md](CLAUDE.md).

## Features

- Set colors per port, live or **stored on the controller** (runs with no software, survives reboots)
- Software effects: rainbow, breathe, wave, comet, flicker, gradient, cycle, blink, and **custom keyframe
  timelines**, per port or across all ports; rendered into a seamless loop and saved to the controller
- The controller's own 24 built-in effects (canvases), palettes, LED counts, startup behaviour
- Upload xLights FSEQ animations; LED-count test; power readings
- Desktop GUI (PySide6) with a live preview of your case; CLI; background daemon

Requires **firmware 2.3.0** (USB `16d5:2a16`). Older firmware has to be updated once with the official
NollieRGB app. OpenNollie never touches firmware.

## Quick start (Arch / CachyOS)

```bash
sudo pacman -S --needed python-hidapi pyside6
python3 -m opennollie info              # read the controller (read-only)
python3 -m opennollie.gui               # desktop app
python3 -m opennollie save profiles/airflow-rainbow.toml
```

User access to the device comes from the udev rule in `packaging/70-opennollie.rules` (OpenRGB's rules
cover it too). Protocol notes: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). Not affiliated with Nollie.
