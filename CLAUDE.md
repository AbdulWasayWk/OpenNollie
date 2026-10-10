# OpenNollie — Project Source of Truth

> This file is the single source of truth for the project: goals, decisions, status and
> findings. **Update it as work progresses**: tick off tasks, record findings, log decisions.
> Wire-level detail lives in `docs/PROTOCOL.md`; summarize it here.

## Goal

An open-source Linux app for the **Nollie 16CH RGB** controller (now on firmware 2.3.0, USB `16d5:2a16`):

1. Set a color (or a few colors) **per channel**, live. Per-LED control is not needed.
2. **Effects**: static, breathing, rainbow, wave, gradient, etc. Fully configurable, user-defined
   effects that can be assigned per channel or to all channels.
3. **Save to device**, so lighting keeps working with no software running.
4. **Feature parity with the official NollieRGB 2.3.0 app** (user request 2026-10-10). See *Feature catalog*.
5. Scope: the user's CachyOS (Arch) machine first. Portability comes later.

## Environment facts

- Host: CachyOS, KDE Plasma 6 (Wayland), kernel 7.x, shell `fish`. Python 3.14. No Rust toolchain.
- Nollie: **`16d5:2a16`** "nollie.cn Nollie 16CH RGB", **fw 2.3.0** (internal version 19, hw 165), serial
  `00B7FE6232DC30F6`, USB bus 1, port `1-8`. Updated from legacy fw (`3061:4716`) on 2026-10-10.
- HID interfaces: **if0** = vendor page `0xFF00`, 1024 B OUT + **1024 B IN**, no report IDs (hidapi: write
  `0x00` + 1024 B). This is the protocol interface. **if1** = boot keyboard (usage 1/6, 8 B IN), purpose unknown;
  ignore it. Pick if0 by `interface_number == 0`.
- OpenRGB's `60-openrgb.rules` already grants `uaccess` for `16d5:2a16`. Ours: `packaging/70-opennollie.rules`.
- Python libs come from pacman (`python-hidapi`, `pyside6`), no venv. Python 3.11 for RE is at
  `~/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/bin/python3.11`.
- Windows VM `win10` (libvirt, `qemu:///system`, user is in the `libvirt` group) has NollieRGB 2.3.0. Its persistent
  hostdev is still the old ID `3061:4716`; use `tools/vm-follow-nollie.sh` or update the XML (ask first).
- The host sees passthrough USB traffic: `tools/setup-dev.sh usbmon` (once per boot, sudo), then
  `tools/capture.sh <name> [secs]` → `captures/<name>.pcapng`.
- After the VM releases the Nollie, `usbhid` normally re-binds by itself. If not: `tools/setup-dev.sh bind`.

## Port map (this machine)

"Port N" = OpenRGB's / NollieRGB app's "Channel N" = our CLI's port N. `fw ch` = channel index on the wire for
fw 2.3.0 (app `N_DEV_CH_NAME['Nollie16']`). Devices verified 2026-10-10 with `identify` on the legacy fw.

Port → fw ch mapping **re-verified on fw 2.3.0** (2026-10-11): `static 4,5 red 6-8 green 9 white 12,13 purple 14-16 cyan`
showed every group correctly.

| Port | fw ch | Device |
|------|-------|--------|
| 1  | 3  | — (not connected) |
| 2  | 2  | — (not connected) |
| 3  | 1  | — (not connected) |
| 4  | 0  | Back exhaust fan |
| 5  | 8  | Top exhaust fan |
| 6  | 9  | Bottom back intake fan |
| 7  | 10 | Bottom middle intake fan |
| 8  | 11 | Bottom front intake fan |
| 9  | 4  | GPU (its own ARGB controller in external-sync mode; **reads only the first 6 values** and stretches/blends them over its ~8 LEDs) |
| 10 | 5  | — (not connected) |
| 11 | 6  | — (not connected) |
| 12 | 7  | CPU cooler fan, back |
| 13 | 15 | CPU cooler fan, front |
| 14 | 14 | Side middle intake fan |
| 15 | 13 | Side bottom intake fan |
| 16 | 12 | Side top intake fan |

Groups: exhaust = 4, 5 · bottom intake = 6–8 · side intake = 14–16 · CPU cooler = 12, 13 · GPU = 9.
Used ports: `4-9,12-16`. **LED counts (stored on the device): fans 8, GPU 6** (= values the GPU reads, found with `ledtest`).
16CH firmware has no LED auto-detection; use `opennollie ledtest PORT`.

## Firmware model (fw 2.3.0), summary

Full detail: `docs/PROTOCOL.md`. Every command is a 1024 B output report; GETs answer with a 1024 B input
report (data starts at byte 0). All multi-byte values are **little-endian**.

- **Settings persist automatically.** Every SET is stored on the device. The firmware flushes to flash a few
  seconds after a change (`WRITE_COUNT` goes up). The new firmware has **no separate "save" step**; the app pushes each change immediately.
- **Mode** (`FA 01 m`): 0 = OL (host streams frames), 1 = HW (built-in effects, default), 2 = SL, 3 = FSEQ.
- **Canvases**: groups of channels that share one effect. Loop canvases are 0..13 (`CANVAS_LEN`, map `FA 03`);
  start/boot canvases are 14+ (`START_CANVAS_LEN`, map `FA 0B`). Each canvas has `CH_PARAM` (`FA 02 canvas …`):
  effect, brightness, step (speed), size, ch-count, vmap-process, delay, 3 colors.
- **Effects** = index into `NOLLIE_EFFECT_RAW` (24: Off, Solid, Breath, Scanning, Rainbow, …, ColorFromPalette).
  Boot effects: `NOLLIE_START_EFFECT_RAW` (8: Light up, Color wipe, …).
- **Per channel**: LED count (`FA 08`, u16 ×16), zLED (`FA 09`), LED index remap ("vmap", `F8`), vmap name (`F6 07`).
- **Global**: FPS as frame ms (`FA 06`/`FA 07` start), palettes 8×16 GRB (`FA 0D`), dynamic 1/2, frame delay,
  boot playlist (intro, steady) (`FA 13`), idle fallback seconds after streaming stops (`F6 16`), controller
  name (`F6 06`), timestamps.
- **Live**: per channel `[ch, 0, 0, 0, GRB × 256]`, then `FF` = show. The first frame switches the mode to ONLINE
  automatically. With idle = 0 (default) the device **falls back to HW effects 3.0 s after the last frame**, so live
  output needs continuous streaming (or idle 5 s…1 h / 65535 = never). Revert manually with `FA 01 01`.
- **Static per-port colors** = HW mode with one `Solid` canvas per color (stored, no streaming).
- **Stored animations**: FSEQ v2 (uncompressed) in SPI flash (16 MB GD25Q128, region from `0x10000`), uploaded via
  `F4` flash ops; played with `F3 02` or as the boot "steady" item.
- **Maintenance**: factory reset `FA 21` (keeps calibration), super reset `FA 22`, soft reboot `FA 23`,
  LED-count detection `FA 14`, voltage/power `FC 1A`.
- ⚠ Never send `FD` (jump to bootloader) or `81/83/84` (bootloader/OTA). Not implemented, by decision.

## Feature catalog (NollieRGB 2.3.0 device pages → OpenNollie)

| App page | Features | Status |
|---|---|---|
| Home / Basic settings | controller name; fw version; boot playlist (Start effect: start canvas; Loop effect: HW effects / FSEQ loop / FSEQ once); direct-drive idle timeout; (GPIO: not on 16CH) | [ ] |
| Loop canvas / Boot canvas | add/delete/edit canvases, drag channels into canvases, per-canvas effect + params (brightness, speed, size, delay, frame delay, vmap process, palette, blend, colors 1–3), loop FPS, start FPS | [ ] |
| LED settings | per-channel LED count, component presets (`Components/*.json`, `vmap/*.json`), LED order (first/reverse) = vmap, vmap name (auto-detect: N/A on 16CH) | [ ] |
| Palette editor | 8 groups × 16 colors; import/export JSON | [ ] |
| FSEQ player | upload FSEQ to flash (erase/write/verify), channel map, play/stop/loop | [ ] |
| Device info | canvas overview, power (rails V/I), MCU temp/load, flash chip ID, ROM state, write count, stored FSEQ size | [ ] |
| Backup | export/import config JSON, factory reset, restart | [ ] |
| Power test | N/A on 16CH (Nollie8 Plus only) | — |
| Identify | light one channel red (`make_ch`) | [ ] |
| **OpenNollie extras** | live per-channel colors; **software effects engine** (any user-defined effect) streamed live, or **rendered to FSEQ and stored on the device** | [ ] |

## Architecture (decided)

**Python 3 + hidapi + PySide6 (Qt)**.

```
opennollie/
  protocol.py    # fw 2.3.0 command builders + response parsers (pure, no I/O)
  device.py      # hidapi transport (if0), request/response, high-level ops
  model.py       # DeviceConfig dataclasses mirroring the device state; read_all / diff / write
  flash.py       # F4 flash ops + FSEQ upload/verify (mirrors app n_usb_flash_mixin / fseq_upload_service)
  fseq.py        # FSEQ v2 reader/writer
  effects/       # software effects: f(t, channel ctx) -> colors; also rendered to FSEQ
  engine.py      # live frame loop (OL mode)
  daemon.py      # systemd --user service owning the device; local API for GUI/CLI
  cli.py         # `opennollie …`
  gui/           # Qt app with the pages above
packaging/       # 70-opennollie.rules, opennollie.service, PKGBUILD
tools/           # setup-dev.sh, capture.sh, vm-follow-nollie.sh, re/ (pyi_extract.py, disasm.py)
captures/        # .pcapng from RE sessions (gitignored)
vendor-app/      # official app + extracted bytecode (gitignored, never commit)
docs/PROTOCOL.md
```

Saved lighting has two layers: **(a)** built-in HW effects on canvases (cheap, firmware-rendered), and
**(b)** custom effects from our engine, rendered to FSEQ and stored in flash, which plays with no host.

## Roadmap & status

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

### Phase 0: Environment ✅ (2026-10-10)
- [x] udev/hidraw access, usbmon capture tooling, Python deps from pacman, git

### Phase 1: Baseline live control (legacy fw) ✅ (2026-10-10)
- [x] OpenRGB-derived live protocol; CLI `set`/`off`/`identify`; port map verified on hardware
- Superseded by the firmware update. Legacy fw code is removed from `main`, kept in git history (`0127bce`).

### Phase 2: Reverse engineering ✅ (2026-10-10)
- [x] Vendor app NollieRGB 2.3.0 decompiled (PyInstaller, Py 3.11), with the full protocol from bytecode
- [x] Firmware updated to 2.3.0 via the official app (captured: `captures/fw_update_2.3.0.pcapng`)
- [x] Capture of the user exploring every page on the new fw (same file, USB addr 7, t≈232–957 s)
- [x] Read-only queries from Linux match the app (mode, version, canvases, LED counts)

### Phase 3: Protocol library (fw 2.3.0)
- [x] `docs/PROTOCOL.md`: rewritten for fw 2.3.0
- [x] `protocol.py` + `device.py`: request/response, GET/SET except OTA; unit tests vs captured bytes
- [x] `model.py`: read full device config (`opennollie info` / `dump` / `power`), verified on hardware
- [x] Writes verified on hardware: canvas params (readback + visual), loop canvas layout via `static`, mode
- [x] Setters round-trip verified (`python3 -m tests.hw_roundtrip`, 12/12): LED/zLED counts, palette, loop/boot
      frame ms, boot playlist, idle, vmap name, dynamic 1/2, frame delay, name. Reboot verified.
- [ ] Not yet exercised: boot canvases (14+), LED index write, factory reset (user did one via the app)
- [~] Live streaming (OL) + identify: single frames verified; needs a keep-alive loop (3 s fallback)
- [x] Voltage/power + flash info (`power`). LED-count detection and power test: **N/A on the 16CH** (fw lacks them)
- [x] Flash ops + FSEQ writer/upload/play + boot-loop persistence, verified on hardware (GRB storage!)
- [ ] Backup/restore JSON (compatible with the app's export format if practical), factory reset, reboot

### Phase 4: Effects engine + headless product
- [x] `effects.py`: static, gradient, breathe, cycle, rainbow, wave, comet, blink, flicker, **keyframes** (user-defined);
      all periodic (seamless loops); `unit` port/led, `span` port/group, port order = travel order
- [x] `profile.py`: TOML profiles with groups, per-port LED counts, layers (later wins), `hw:<firmware effect>`
- [x] `engine.py`: Renderer, loop length = LCM of periods (≤ 120 s), live streaming, render→FSEQ, `save_to_device`
      (all static/hw → canvases; otherwise FSEQ + boot loop). CLI: `live`, `save`, `render`, `play`, `stop`, `effects`
- [x] Verified on hardware: `live profiles/airflow-rainbow.toml` (user confirmed), `save` same profile (3.9 s,
      plays from the device, boot playlist FSEQ loop)
- [x] Daemon (`opennollie/daemon.py`): owns the device (auto-reconnect), forwards raw packets for clients
      (`client.connect()` → daemon if running else USB), hosts live effects (`live PROFILE` / `--stop` / `--status`),
      pauses them while a client drives LEDs, autostart via `~/.config/opennollie/daemon.toml: live_profile`.
      Verified: CLI via daemon, live in daemon, pause/resume, controller reboot → reconnect, clean SIGTERM.
- [ ] systemd user unit + install script (needs the user's OK)
- [ ] Backup/restore of the device config (JSON)

### Phase 5: GUI (PySide6) — `python3 -m opennollie.gui`
- [x] `gui/app.py` main window; `common.py` (Device: one background thread for all device calls, reconnects;
      ColorButton, PortPicker); `caseview.py` (schematic of this case, animated LEDs)
- [x] Pages: Effects studio (layers, all effects incl. built-in, keyframe timeline, live case preview, preview on
      device, save to device, open/save TOML), Built-in effects (loop/boot canvases), Ports & LEDs (names, counts,
      identify, LED test dialog), Palettes (+ import/export), Stored animation (status, play/stop, FSEQ upload),
      Device (name, power, startup playlist, idle fallback, backup JSON, restart, factory reset)
- [x] Offscreen screenshot check of every page; every effect exercised in the editor without errors
- [ ] User test on the real desktop (Breeze style); restore-from-backup; tray icon?

### Phase 6: Packaging
- [ ] PKGBUILD, udev rule, systemd unit, README

## Open questions
- What's on interface 1 (keyboard, 8 B IN)? Probably irrelevant.
- What exactly is mode SL (2)? It's seen for ~2–3 s after an FSEQ upload/stop; PLAY is ignored then.

## Findings log
<!-- Newest first. Format: YYYY-MM-DD: finding (source: capture/file/test) -->
- 2026-10-11: Power draw with the 60 % rainbow FSEQ: 3.43 A @ 4.95 V (vs 0.75 A for 40 % static colors). Worth showing in
  the GUI; consider a brightness cap. Gotcha: `pgrep -f` also matches the Bash tool's zsh wrapper, so signal the
  python pid. (source: hardware)
- 2026-10-11: **GPU on port 9 is not 1:1.** A 12-color per-LED pattern showed as 8 blended colors from only the first few
  inputs. `ledtest` N=6 (1–5 green, 6 blue, 7 red) showed ~80 % green + 20 % blue and no red, so the GPU reads 6 values and
  interpolates them over its own LEDs. Per-port colors are exact; per-LED effects are smoothed by the GPU. Stored
  counts: fans 8, GPU 6. The user had thought 10. (source: user observation)
- 2026-10-11: Effects engine works end to end: live streaming of `airflow-rainbow` (~5 % CPU, mode stays ONLINE while
  streaming; user confirmed the drifting per-fan hues), and `save` rendered it to a 300-frame loop (309 KiB) stored and
  playing from the device in 3.9 s. Profiles assume 32 LEDs/port until real counts are known. (source: hardware)
- 2026-10-11: **Stored animations work end to end**: FSEQ upload 190 KB in 1.6 s with matching device CRC, PLAY loops
  (mode FSEQ), boot playlist steady=FSEQ loop survives a controller reboot and plays at power-on. **FSEQ frame bytes
  are sent to the LEDs unconverted, so store GRB** (bytes 00 66 00 showed red). PLAY is ignored for ~2–3 s after an
  upload/stop (mode SL). Flash chip: GigaDevice 16 MB. LED detect is unsupported on 16CH. Settings reach flash
  ~5 s after a SET. (source: hardware + user observation)
- 2026-10-11: **Stored per-port colors work** (`static`): 5 Solid canvases, readback exact, user confirmed every
  group's color, so the port map is right on fw 2.3.0. Canvas-params write: readback exact; WRITE_COUNT 25→26 a few
  seconds later (lazy flash flush). Live: one frame → mode ONLINE, back to HARDWARE after 3.0 s (idle=0 default).
  Power reading: 5.13 V, 0.75 A, MCU 41 °C. Name kept through factory reset: "Wasay_PC_Nollie16". (source: hardware)
- 2026-10-10: **fw 2.3.0 verified from Linux (read-only)**: `FC 01` → ver 19 / hw 165, `FC 02` → 2.3.0, mode 1 (HW),
  1 canvas × 16 ch running effect 2 (Breath), LED num 256 per channel, idle 0, boot playlist 0/0, which are the
  factory defaults after the user's reset. if1 is a HID keyboard. (source: hidapi on host)
- 2026-10-10: The capture of the user exploring the app shows: config edits are pushed immediately with no save
  step; LED count `FA 08`; vmap `F8` and name `F6 07`; all 23 effects + brightness sliders `FA 02`; FPS `FA 06`;
  boot canvas 14 + `FA 0B/05/04`; `FA 20` after boot-canvas edits; idle `F6 16`; factory reset `FA 21` at the end.
  Live preview streams frames without switching mode first, then sends `FA 01 01` to go back to HW.
  (source: captures/fw_update_2.3.0.pcapng)
- 2026-10-10 23:29: **Firmware updated** with the official NollieRGB v2.3.0 in the VM: 3061:4716 → bootloader
  3061:f037 → 16d5:2a16 (24 s). The VM shut down cleanly at ~23:44. Gotcha: every device reset re-enumerates with a new
  USB address, which leaves the VM's hostdev stale; `vm-follow-nollie.sh` handles it. (source: capture, lsusb)
- 2026-10-10: Vendor app v2.3.0 = PyInstaller (Py 3.11). The app routes legacy devices straight to OTA update.
  `config/Nollie16.bin` = RISC-V firmware image. (source: vendor-app/dis2)
- 2026-10-10: Legacy-fw era: live protocol from OpenRGB worked; the device held the last frame; ports mapped by
  `identify`. (source: hardware tests)
- 2026-10-10: `sudo` needs a password, so privileged steps go through scripts the user runs. Commands typed by the
  Terminal-panel tool get lost while fish starts up, so the user runs them by hand.

## Decision log
<!-- Newest first. Format: YYYY-MM-DD: decision; reason -->
- 2026-10-10: Target **fw 2.3.0 only**; legacy-fw code removed (in git history). Reason: the user's device is updated,
  and the scope is this machine.
- 2026-10-10: Goal expanded to **parity with NollieRGB 2.3.0** plus our software effects engine (user request).
- 2026-10-10: OpenNollie will **not** implement firmware updates; it's left to the official app. Reasons: brick risk
  from our own bugs, the firmware binary can't be redistributed, and it's a one-time operation.
- 2026-10-10: Python + hidapi + PySide6.
- 2026-10-10: Sniff USB on the host via usbmon; libvirt passthrough traffic is visible there.

## Working conventions
- Ask the user before any `sudo` command or system change (packages, udev, modules, VM config).
- Keep this file current: update status, findings and decisions in the same session the work happens.
- Hardware writes follow the vendor app's exact packets (bytecode + capture). Never fuzz unknown commands.
  Read-only GETs first; verify each SET by reading it back. Flash ops only after reading the app's code fully.
- Never send bootloader/OTA commands (`FD`, `81`, `83`, `84`).
- The vendor app is proprietary: keep it and its extracted code out of git; our code is written fresh from the
  documented protocol, not copied.
