# OpenNollie — Project Source of Truth

> This file is the single source of truth for the project: goals, decisions, status and
> findings. **Update it as work progresses**: tick off tasks, record findings, log decisions.
> Detailed protocol notes live in `docs/PROTOCOL.md` once that file exists; summarize them here.

## Goal

An open-source Linux app for the **Nollie 16CH RGB** controller (USB `3061:4716`):

1. Set a color (or a few colors) **per channel**, live. Per-LED control is not needed.
2. **Effects engine**: static, breathing, rainbow, wave, gradient, etc. Fully configurable,
   user-defined effects that can be assigned per channel or to all channels.
3. **Save to device**, so lighting keeps working with no software running. OpenRGB can't do
   this; the official Windows app can.
4. Scope: the user's CachyOS (Arch) machine first. Portability comes later.

## Environment facts

- Host: CachyOS, kernel 7.x, shell `fish`. Python 3 is available. No Rust toolchain.
- Nollie: `3061:4716 nollie.cn Nollie 16CH RGB`, serial `00B7FE6232DC30F6`, bcdDevice `0.10`,
  USB bus 1, port `1-8`.
- One HID interface (`1-8:1.0`): EP `0x81` IN (64 B), EP `0x01` OUT (**1024 B**).
- OpenRGB 1.0+ (git66) is installed and has a Nollie driver. Use it as the reference for the
  live protocol.
- Python libs come from pacman (`python-hidapi`, `pyside6`), with no venv. The PKGBUILD will depend on the same packages.
- The Windows VM with the official Nollie app runs under **libvirt/QEMU**. With USB passthrough,
  the host sees all USB traffic, so we **sniff on the host with `usbmon` + Wireshark**.
- After the VM uses the Nollie via passthrough, `usbhid` may stay unbound and no hidraw node
  appears. Fix: `tools/setup-dev.sh bind`.
- HID report descriptor: vendor page `0xFF00`. **Output report = 1024 bytes, input report = 64 bytes,
  no report IDs** (hidapi writes need a leading `0x00` byte, so 1025 bytes in total).
- Capturing: `tools/setup-dev.sh usbmon` (once per boot, sudo), then `tools/capture.sh <name> [secs]`
  writes `captures/<name>.pcapng`. The user is in the `wireshark` group, but it isn't active until
  they re-login; `capture.sh` falls back to a private dumpcap copy until then.

## Architecture (decided)

**Python 3 + hidapi + PySide6 (Qt)**. It iterates fast during reverse engineering, an effects
loop at 30–60 FPS on 16 channels needs very little CPU, and Qt fits KDE/CachyOS.

```
opennollie/
  protocol.py    # byte-level packet builders
  device.py      # hidapi transport: open, channel config, send frame, save-to-device
  effects/       # effect plugins: f(t, channel_cfg) -> list[RGB]
  engine.py      # frame loop, layering, per-channel / global assignment
  config.py      # profiles in ~/.config/opennollie/*.toml
  daemon.py      # systemd --user service; owns the device; local socket/D-Bus API
  cli.py         # `opennollie set ch3 ff0000`, `opennollie effect rainbow --all`, `opennollie save`
  gui/           # Qt app; talks to the daemon
packaging/       # 70-opennollie.rules (udev; <73 so uaccess applies), opennollie.service, PKGBUILD
tools/           # capture.sh, decode_pcap.py (RE helpers)
captures/        # .pcapng files from the RE sessions
docs/PROTOCOL.md # protocol documentation
```

**Lighting modes**
- **Software mode**: the daemon streams frames, so any user-designed effect works.
- **Hardware (saved) mode**: limited to what the firmware can store, probably static colors
  plus built-in effects with parameters. Map our effects onto those where possible. Whether the
  firmware can store custom animations is still unknown (see Open questions).

## Roadmap & status

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

### Phase 0: Environment ✅ (2026-10-10)
- [x] Rebind `usbhid` to the Nollie (`tools/setup-dev.sh bind`). It's now `/dev/hidraw11`, user rw via uaccess ACL
- [x] udev rule `packaging/70-opennollie.rules` (for packaging; OpenRGB's `60-openrgb.rules` already grants `uaccess` for `3061:4716` on this machine)
- [x] Install wireshark-cli, load `usbmon`, user capture access (`tools/setup-dev.sh usbmon`, once per boot; `tools/capture.sh` records)
- [x] Python deps from pacman: `python-hidapi` 0.14 (module `hid`, hidraw backend), `pyside6` 6.11. No venv
- [x] `git init`

### Phase 1: Baseline live control
- [ ] Read OpenRGB's Nollie controller source → summarize in `docs/PROTOCOL.md`
- [ ] Python script: set a solid color per channel
- [ ] Python script: set the per-channel LED count

### Phase 2: Capture sessions (Windows app in the VM, sniffed on the host)
One action per capture, about 3 s between steps:
- [ ] `01_startup`: launch the app, 10 s idle (handshake, version query)
- [ ] `02_static_ch1_red`: ch1 static red, then green, then blue
- [ ] `03_static_ch2`: ch2 static white (channel addressing)
- [ ] `04_ledcount`: change the ch1 LED count (10 → 20)
- [ ] `05_brightness`: global brightness 100 → 50
- [ ] `06_effect_*`: each built-in effect, then change its speed and color
- [ ] `08_save_static`: static colors on 2–3 channels → **Save to device**
- [ ] `09_save_effect`: effect → **Save to device**
- [ ] Observation: does the saved lighting come back after a reboot with the VM off?
- [ ] Collect: app name/version, screenshots of all screens, the install folder copied to the
      host (decompile it if it's .NET or Electron)

### Phase 3: Protocol
- [ ] Decode captures → `docs/PROTOCOL.md`
- [ ] Implement in `protocol.py`, replay each command from Linux, verify on hardware
- [ ] Save-to-device works from Linux

### Phase 4: Headless product
- [ ] Effects engine + built-in effects (static, breathing, rainbow, wave, gradient, …)
- [ ] User-defined effects (config-driven)
- [ ] Config/profiles (TOML)
- [ ] Daemon (systemd user service) + CLI

### Phase 5: GUI
- [ ] Qt app: channel list, color pickers, effect editor, live preview, Save to device

### Phase 6: Packaging
- [ ] PKGBUILD, udev rule, systemd unit, README

## Open questions
- What's connected to each channel (device type and LED count)? *(waiting on the user)*
- Windows app name and version? Is it .NET or Electron, i.e. can it be decompiled?
- Which effects can the firmware store? Can it store custom or keyframe animations, or only
  built-in ones?
- Does the device need a periodic keep-alive in software mode, or does it hold the last frame?

## Findings log
<!-- Newest first. Format: YYYY-MM-DD: finding (source: capture/file/test) -->
- 2026-10-10: Host usbmon capture verified: every Nollie URB on bus 1 is recorded. (source: capture self-test)
- 2026-10-10: HID descriptor gives 1024 B output / 64 B input reports, vendor page 0xFF00, no report IDs.
  (source: sysfs report_descriptor)
- 2026-10-10: `/usr/lib/udev/rules.d/60-openrgb.rules` already tags `3061:4716` with `uaccess`, so no extra
  udev rule is needed on this machine. (source: grep)
- 2026-10-10: `sudo` needs a password, so privileged steps go through `tools/setup-dev.sh`, which the user runs.
  Commands typed by the Terminal-panel tool get lost while fish starts up, so the user runs them by hand.
- 2026-10-10: Device enumerates as `3061:4716`, OUT EP is 1024 B, no host driver bound
  (passthrough left it detached). (source: lsusb, sysfs)

## Decision log
<!-- Newest first. Format: YYYY-MM-DD: decision; reason -->
- 2026-10-10: Python + hidapi + PySide6; fast iteration during RE, light enough CPU use, Qt fits CachyOS/KDE.
- 2026-10-10: Sniff USB on the host via usbmon rather than USBPcap in Windows; libvirt passthrough traffic is visible on the host.
- 2026-10-10: Use OpenRGB's Nollie driver as the reference for the live protocol; reverse engineer only save-to-device and hardware effects.

## Working conventions
- Ask the user before any `sudo` command or system change (packages, udev, modules).
- Keep this file current: update the status checkboxes, the findings log and the decision log
  in the same session the work happens.
- Hardware writes, especially save-to-device and anything touching flash, are replayed
  only from captured, understood packets. Never fuzz unknown commands at the device.
