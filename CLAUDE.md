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
- Nollie: **`16d5:2a16`** (current fw since 2026-10-10; was `3061:4716` legacy) "nollie.cn Nollie 16CH RGB",
  serial `00B7FE6232DC30F6`, bcdDevice `0.10`, USB bus 1, port `1-8`.
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

## Port map (this machine)

Port numbers are OpenRGB's / our CLI's (`Channel 1..16`). Verified 2026-10-10 with `opennollie identify`.

| Port | Wire ch | Device |
|------|---------|--------|
| 1  | 19 | — (not connected) |
| 2  | 18 | — (not connected) |
| 3  | 17 | — (not connected) |
| 4  | 16 | Back exhaust fan |
| 5  | 24 | Top exhaust fan |
| 6  | 25 | Bottom back intake fan |
| 7  | 26 | Bottom middle intake fan |
| 8  | 27 | Bottom front intake fan |
| 9  | 20 | GPU (GPU's own ARGB set to external sync via OpenRGB) |
| 10 | 21 | — (not connected) |
| 11 | 22 | — (not connected) |
| 12 | 23 | CPU cooler fan, back |
| 13 | 31 | CPU cooler fan, front |
| 14 | 30 | Side middle intake fan |
| 15 | 29 | Side bottom intake fan |
| 16 | 28 | Side top intake fan |

Groups (for default profiles): exhaust = 4, 5 · bottom intake = 6–8 · side intake = 14–16 ·
CPU cooler = 12, 13 · GPU = 9. Used ports: `4-9,12-16`.

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

### Phase 1: Baseline live control ✅ (2026-10-10)
- [x] Read OpenRGB's Nollie controller source → `docs/PROTOCOL.md`
- [x] `opennollie/protocol.py` (packet builders) + `device.py` (hidapi) + `cli.py` (`set`, `off`, `identify`)
      with unit tests (`python3 -m unittest discover tests`)
- [x] LED count per channel: N/A as a separate command; on the 16CH the count is part of every packet
- [x] Verified on wire (capture) and on hardware: all fans + GPU showed dim blue, so GRB order is correct
- [x] Map ports to physical devices (`identify`). See **Port map**. Board labels aren't visible (case closed)
- [x] Device holds the last frame after the host stops sending (several minutes observed); no keep-alive needed

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
- [x] Got the vendor app (NollieRGB v2.3.0 portable, in `vendor-app/`, gitignored). It's PyInstaller/Python 3.11, so
      it decompiles to readable bytecode; see `docs/PROTOCOL.md` § Vendor app analysis. Tools: `tools/re/`
- [x] Firmware updated to current fw via the official app (2026-10-10). The device is now `16d5:2a16`

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
- ~~Legacy firmware~~ Resolved 2026-10-10: updated to current fw (16d5:2a16). Background kept below for reference.
- **Firmware (was): our Nollie ran LEGACY firmware (`Nollie16_old`, 3061:4716).** App v2.3.0 forces an OTA update for
  legacy devices and only supports save-to-device / effects / FSEQ on the current firmware (16D5:2A16). The VM has
  NollieRGB **1.0** (old app). Recommended: update via the official v2.3.0 app in the VM, never from our tool.
  OTA flow: app → `0xFD` jump-BL → device re-enumerates as **3061:f037** (bootloader) → PROM + VERIFY (CRC) → reboot
  as **16d5:2a16**. A CRC failure leaves it in the bootloader, and the app can re-flash it (recoverable).
  The VM (`win10`, libvirt) passes through by ID 3061:4716 only, so run `tools/vm-follow-nollie.sh` during the update
  to hot-attach the new IDs.
- LED count per device? Not needed yet: we drive 256 LEDs/port and the extra data is ignored.
- Windows app name and version? Is it .NET or Electron, i.e. can it be decompiled?
- Which effects can the firmware store? Can it store custom or keyframe animations, or only
  built-in ones?
- Do OpenRGB's port numbers match the labels printed on the board? Unknown (case closed); doesn't matter in practice.

## Findings log
<!-- Newest first. Format: YYYY-MM-DD: finding (source: capture/file/test) -->
- 2026-10-10 23:29: **Firmware updated** with the official NollieRGB v2.3.0 in the VM. 3061:4716 → bootloader 3061:f037
  (attached 23:29:03) → **16d5:2a16** (23:29:27), same serial `00B7FE6232DC30F6`. New fw exposes 2 HID interfaces:
  if0 = 1024 B IN/OUT (RGB protocol), if1 = 8 B IN only (unknown). The user reports the app shows success.
  The capture (16 min, 36k pkts) also has the user's exploration of v2.3.0 on the new fw (USB addr 7, ~23:29–23:44).
  Gotcha: every device reset re-enumerates with a new USB address, which leaves the VM's hostdev stale.
  `vm-follow-nollie.sh` now replaces stale entries. (source: captures/fw_update_2.3.0.pcapng, lsusb)
- 2026-10-10: Vendor app v2.3.0 = PyInstaller (Py 3.11). Full current-firmware protocol recovered from bytecode
  (commands 0xF2–0xFF: effects, palettes, boot playlist, idle fallback, flash, FSEQ animations). Our device is
  `Nollie16_old`; the app sends legacy devices straight to OTA update. `config/Nollie16.bin` = RISC-V firmware image.
  OpenRGB's udev rules already cover the post-update ID 16d5:2a16. (source: vendor-app/dis2)
- 2026-10-10: Port → device mapping done via `identify` (see Port map); ports 1-3, 10, 11 are unused.
  (source: user observation)
- 2026-10-10: Hardware test passed: `set all blue --brightness 30` turned all fans and the GPU dim blue, and the colors
  stayed after the program exited, so the device holds the last frame. GPU is on port 9 (OpenRGB "Channel 9"), with
  the GPU's own ARGB set to external sync. (source: user observation)
- 2026-10-10: Live protocol (from OpenRGB): one 1024 B packet per port `[wire_ch, flag, cnt_hi, cnt_lo, GRB...]`,
  wire channels 16..31, ascending, ch 31 always last with flag 2 (latch?). Our encoder matches on the wire.
  The device sends no IN reports during live updates. (source: OpenRGB b190d3e, captures/p1_set_all_blue.pcapng)
- 2026-10-10: OpenRGB config has the Nollie with all 16 zones at 0 LEDs, so no LED counts are known yet. (source: ~/.config/OpenRGB)
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
- 2026-10-10: OpenNollie will **not** implement firmware updates; it's left to the official app. Reasons: brick risk
  from our own bugs, the firmware binary can't be redistributed, and it's a one-time operation.
- 2026-10-10: Python + hidapi + PySide6; fast iteration during RE, light enough CPU use, Qt fits CachyOS/KDE.
- 2026-10-10: Sniff USB on the host via usbmon rather than USBPcap in Windows; libvirt passthrough traffic is visible on the host.
- 2026-10-10: Use OpenRGB's Nollie driver as the reference for the live protocol; reverse engineer only save-to-device and hardware effects.

## Working conventions
- Ask the user before any `sudo` command or system change (packages, udev, modules).
- Keep this file current: update the status checkboxes, the findings log and the decision log
  in the same session the work happens.
- Hardware writes, especially save-to-device and anything touching flash, are replayed
  only from captured, understood packets. Never fuzz unknown commands at the device.
