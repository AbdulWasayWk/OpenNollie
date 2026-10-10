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
- Git: public repo **github.com/AbdulWasayWk/OpenNollie** (`origin`, SSH; branch `master`), GPL-3.0-or-later.
  Push only when the user asks.

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
| Home / Basic settings | controller name; fw version; boot playlist; direct-drive idle timeout; (GPIO: not on 16CH) | [x] GUI Device page + CLI |
| Loop canvas / Boot canvas | canvases, ports per canvas, per-canvas effect + params (incl. palette group, blend, frame delay), loop/start FPS | [x] Built-in effects page reworked (2026-10-11): "groups", tabs Normal lighting / Startup animation, only the settings each effect uses (`protocol.EFFECT_PARAMS`), applies as you edit (diff-only writes), color balance per port (`builtin.py`). Verified by the user (2026-10-11): split group (fans + GPU, Breath) stays in step; startup animation plays once after a power cycle |
| LED settings | LED count per port, names | [x] |
| LED settings | LED order remap (vmap), component/vmap presets | [x] `layouts.py`, Ports page "LED order", CLI `order`; our engine applies the order to live/stored effects itself (fw only applies it to built-in effects with vmap_process) |
| LED settings | auto-detect LED count | N/A on 16CH (fw lacks it); our `ledtest` replaces it |
| Palette editor | 8 palettes × 16 colors; import/export JSON | [x] (palette write verified; page explains ColorFromPalette + shows which groups use each palette; GUI write verified by the user) |
| FSEQ player | upload FSEQ (erase/write/verify), channel map, play/stop/loop | [x] verified on hw |
| Device info | power (V/I/temp/load), flash chip, write count, stored FSEQ size | [x] |
| Backup | export/restore config JSON, factory reset, restart | [x] all verified on hw (factory reset + restore from the GUI by the user, 2026-10-11) |
| Power test | | N/A on 16CH |
| Identify | light one port | [x] |
| **OpenNollie extras** | live per-port colors; software effects engine (10 effects + custom keyframes), live or stored as FSEQ; profiles + dropdown; daemon; tray; studio with editable case picture; per-port color balance; start-at-login switches | [x] verified on hw and by the user |

## Architecture (decided)

**Python 3 + hidapi + PySide6 (Qt)**.

```
opennollie/
  protocol.py    # fw 2.3.0 command builders + response parsers (pure, no I/O)
  device.py      # hidapi transport (if0), request/response, high-level ops
  model.py       # DeviceConfig dataclasses mirroring the device state; read_all / diff / write
  flash.py       # F4 flash ops + FSEQ upload/verify (mirrors app n_usb_flash_mixin / fseq_upload_service)
  fseq.py        # FSEQ v2 reader/writer
  effects.py     # software effects: f(t, position) -> RGB, all periodic
  profile.py     # TOML profiles (layers, groups); user dir + examples (opennollie/examples/)
  engine.py      # Renderer, prepare() (LED counts/orders + color balance), live loop, render→FSEQ, save_profile,
                 # update_saved (redo what's stored after a balance / LED count change)
  builtin.py     # built-in effect groups ↔ canvases with color balance (split per balance, remembered)
  state.py       # what OpenNollie stored per controller (~/.local/state/opennollie/stored.json)
  layouts.py     # LED order (vmap) + SignalRGB/Nollie layout presets
  calibration.py # per-port color balance (PC side)
  caselayout.py  # the case picture (PC side)
  autostart.py   # tray / service at login
  daemon.py      # systemd --user service owning the device; local API for GUI/CLI; hosts live effects
  client.py      # connect() → daemon if running, else USB
  cli.py         # `opennollie …`
  gui/           # Qt app (pages: studio, built-in effects, ports, layout, palettes, animation, device, settings) + tray
tests/           # unit tests, fakedevice.py (simulated controller), test_offline_device.py (end to end), hw_roundtrip.py
packaging/       # 70-opennollie.rules, opennollie.service, PKGBUILD
tools/           # setup-dev.sh, capture.sh, vm-follow-nollie.sh, re/ (pyi_extract.py, disasm.py)
captures/        # .pcapng from RE sessions (gitignored)
vendor-app/      # official app + extracted bytecode (gitignored, never commit)
docs/PROTOCOL.md
```

Saved lighting has two layers: **(a)** built-in HW effects on canvases (cheap, firmware-rendered), and
**(b)** custom effects from our engine, rendered to FSEQ and stored in flash, which plays with no host.

## Definition of done (release checklist)

The app is **done** when every item below is checked. Keep this list current; it's the single to-do list.
**Status 2026-10-11: every item is checked; the app is done.** New work goes in as new items.

**Verification**
- [x] User has tested the GUI on the real desktop (KDE/Breeze): basic functionality works, saved settings survived
      a PC reboot (2026-10-11). Re-test the pages that change in the remaining work.
- [x] Boot canvases (14+) tested on hardware: apply → power cycle → plays once at power-on (user, 2026-10-11)
- [x] Factory reset run from our app (GUI) and restored from a backup (user, 2026-10-11). Port names weren't in that
      backup (none were stored when it was taken); name restore verified on hw separately
- [x] Built-in group split for color balance (fans + GPU, Breath) stays in step (user, 2026-10-11)
- [x] Palettes page: write from the GUI verified on hardware by eye (a group with ColorFromPalette; user, 2026-10-11)

**Installation**
- [x] `tools/install-user.sh` run 2026-10-11 (user's OK): `opennollie` / `opennollie-gui` in ~/.local/bin, app-menu
      entry, `opennollie.service` enabled; after a PC reboot the service started at login (~25 s after boot) and
      answers the CLI

**Missing features (parity with NollieRGB 2.3.0)**
- [x] Restore settings from a backup JSON (GUI + CLI): diff-based `model.restore`, `--dry-run`; verified on hw
- [x] LED order editor (vmap / LED index: as wired / by position, reverse, start LED, dark LEDs), GUI dialog with
      on-device preview + CLI `order`; LED-index write verified on hw (port 1 reverse/reset round trip).
      User checked the preview/visual direction on a fan (2026-10-11)
- [x] Layout presets: SignalRGB component JSON (`Components/*.json`) and Nollie vmap JSON (`vmap/*.json`), user-supplied
      (not bundled); 237/238 vendor files load (1 vendor file is internally inconsistent and rejected, as in the app)

**Polish**
- [x] Tray icon (`opennollie-tray`, `gui/tray.py`): run live / save to device per profile, stop, open app; autostart at
      login via the installer. Live start/stop through the daemon verified; it's running on the user's desktop
- [x] README: install, GUI pages, tray, CLI, profile format, save-to-device, limits, troubleshooting, development
- [x] Arch PKGBUILD (`packaging/PKGBUILD` + `opennollie.install`): `makepkg` builds (2026-10-11, from the local repo),
      check() runs the 65 tests, the package has all modules/examples/unit/udev/desktop files, and the packaged CLI
      runs. Not installed (the user install from tools/install-user.sh stays)

**Known limits (not to-dos):** GPU smooths per-LED patterns (its own controller reads 6 values); a stored setup is
either built-in canvases or one animation; no LED auto-detect or power test on 16CH firmware.

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
- [x] LED index write verified (restore + `order` round trips). Boot canvases and factory reset verified (user)
- [x] Live streaming (OL) + identify with keep-alive (engine loop, KeepAlive, LiveSession)
- [x] Voltage/power + flash info (`power`). LED-count detection and power test: **N/A on the 16CH** (fw lacks them)
- [x] Flash ops + FSEQ writer/upload/play + boot-loop persistence, verified on hardware (GRB storage!)
- [x] Backup/restore JSON (our own format; diff-based restore), reboot. Factory reset implemented, not run from our app

### Phase 4: Effects engine + headless product
- [x] `effects.py`: static, gradient, breathe, cycle, rainbow, wave, comet, blink, flicker, **keyframes** (user-defined);
      all periodic (seamless loops); `unit` port/led, `span` port/group, port order = travel order
- [x] `profile.py`: TOML profiles with groups, per-port LED counts, layers (later wins). (`hw:` layers removed 2026-10-11)
- [x] `engine.py`: Renderer, loop length = LCM of periods (≤ 120 s), live streaming, render→FSEQ, `save_to_device`
      (all static → Solid canvases; otherwise FSEQ + boot loop). CLI: `live`, `save`, `render`, `play`, `stop`, `effects`
- [x] Verified on hardware: `live profiles/airflow-rainbow.toml` (user confirmed), `save` same profile (3.9 s,
      plays from the device, boot playlist FSEQ loop)
- [x] Daemon (`opennollie/daemon.py`): owns the device (auto-reconnect), forwards raw packets for clients
      (`client.connect()` → daemon if running else USB), hosts live effects (`live PROFILE` / `--stop` / `--status`),
      pauses them while a client drives LEDs, autostart via `~/.config/opennollie/daemon.toml: live_profile`.
      Verified: CLI via daemon, live in daemon, pause/resume, controller reboot → reconnect, clean SIGTERM.
- [x] systemd user unit + install script (installed 2026-10-11, survives reboot)
- [x] Backup/restore of the device config (JSON)

### Phase 5: GUI (PySide6) — `python3 -m opennollie.gui`
- [x] `gui/app.py` main window; `common.py` (Device: one background thread for all device calls, reconnects;
      ColorButton, PortPicker); `caseview.py` (schematic of this case, animated LEDs)
- [x] Pages: Effects studio (layers, software effects, keyframe timeline, live case preview, preview on
      device, save to device, open/save TOML), Built-in effects (loop/boot canvases), Ports & LEDs (names, counts,
      identify, LED test dialog), Palettes (+ import/export), Stored animation (status, play/stop, FSEQ upload),
      Device (name, power, startup playlist, idle fallback, backup JSON, restart, factory reset)
- [x] Offscreen screenshot check of every page; every effect exercised in the editor without errors
- [x] User test on the real desktop (basic functionality OK)
- [x] Studio: profile dropdown (your profiles + examples), Save as by name into ~/.config/opennollie/profiles, Delete,
      Save/Discard/Cancel on unsaved changes, examples read-only; autosave + reopen last session (user requests)
- [x] Restore from backup JSON; LED-order (vmap) editor + component presets; tray icon / quick profile switch
- [x] Settings page: tray / service start at login (`autostart.py`) (user request 2026-10-11)
- [x] Built-in effects page reworked for clarity (user: canvases confusing): effect groups, two tabs, per-effect
      settings, palette/blend/frame delay, live apply with diff-only writes, banner when a studio animation is active
- [x] Editable case layout (`caselayout.py`, `~/.config/opennollie/layout.json`, GUI Layout page): fans/strips,
      drag to arrange, port + first LED + LED count per item (daisy chains), size/angle/name, light-it-up; studio
      preview uses it. The previously hard-coded layout of this machine was migrated into the user's layout.json

### Phase 6: Packaging
- [x] pyproject.toml (commands: opennollie, opennollie-daemon, opennollie-gui, opennollie-tray), examples as package data
- [x] PKGBUILD builds and passes check(). README done. User install via tools/install-user.sh (incl. tray autostart)

## Open questions
- What's on interface 1 (keyboard, 8 B IN)? Probably irrelevant.
- What exactly is mode SL (2)? It's seen for ~2–3 s after an FSEQ upload/stop; PLAY is ignored then.

## Findings log
- 2026-10-11: **After a factory reset the controller names unnamed ports "None"** (the official app treats "None" /
  "null" as no name; we do too, in `device.vmap_name`). Writing an empty name clears it. Port names only reach the
  controller on Ports & LEDs → Apply; the page now shows unapplied edits. (source: hw + app `_norm_vmap_name`)
- 2026-10-11: **Bug: the unit tests overwrote the user's real color balance** with their test value (port 9 red 50 %).
  `calibration.load/save(path=PATH)` bound the path as a default argument, so the tests' `mock.patch(PATH)` never
  applied; `caselayout` had the same pattern (its tests pass explicit paths, so layout.json was safe). Fixed: paths are
  looked up per call; the offline tests fail if the real calibration.toml changes. User's 15/20/100 restored.
  Second cause of "balance resets": the balance is baked into what's saved on the controller, and changing it didn't
  re-save, so the controller kept the old 50 % GPU color after the balance dialog closed. (source: user report + repro)
<!-- Newest first. Format: YYYY-MM-DD: finding (source: capture/file/test) -->
- 2026-10-11: **User's GPU color balance: red 15 %, green 20 %, blue 100 %** (`calibration.toml`, port 9). The user
  confirms these are the values that make the GPU match the fans; it's only slightly dimmer, which is fine. Previews
  show intended colors (balance applies only to device output). (source: user)
- 2026-10-11: **GPU color balance differs**: same data, same timing, but its blue is weaker (cyan → greener, purple →
  pinker, white → yellowish), so a rainbow looks "ahead" on the GPU. Fixed with per-port color balance
  (`calibration.py`, `~/.config/opennollie/calibration.toml`; GUI Ports & LEDs → Color balance; CLI `balance`),
  applied in Renderer and static canvases via `engine.prepare()`; balance/LED-count changes re-save the stored
  profile (`engine.update_saved`, verified on hw: GPU canvas #3333ff ↔ #2633ff). (source: user step test)
- 2026-10-11: Regression found: `opennollie save` / foreground `live --duration` were broken since d44a293 (a text
  replacement deleted `cli.load_profile`). Unit tests never ran CLI commands. Added `tests/fakedevice.py` (simulated
  controller incl. flash + CRC) and `tests/test_offline_device.py` (CLI + save + balance + backup end to end).
- 2026-10-11: Identify crashed the GUI (segfault): a parentless QTimer owned by a garbage-collected Python object.
  Qt objects that outlive a function call need a Qt parent. (source: offscreen repro with faulthandler)
- 2026-10-11: **Mode SL = LEDs paused (dark) while the flash is busy.** Triggered by the STOP command (`F3 03`) and by
  any flash access (CRC, writes; erase didn't seem to on its own). It lasts until ~1.2 s after the last flash op, ignores
  live frames and PLAY, and can't be cut short; afterwards the device returns to its stored lighting. **`FA 01 01`
  (mode HW) stops FSEQ playback instantly with no SL.** So: stop via mode switch, static saves never touch flash, and
  unchanged animations aren't re-uploaded (fingerprint kept in `~/.local/state/opennollie/stored.json`; FSEQ files
  are now deterministic). New-animation save: 1.8 s dark (was ~2 s+); re-save / static save: 0 s. (source: hw traces)
- 2026-10-11: User feedback: effects looked out of sync because new studio layers used spread 1.0 (every fan a different
  phase) and that carried over to every effect. Now in sync by default with an explicit "Pattern" choice.
- 2026-10-11: The Bash tool kills background children when a command ends, so start GUI/tray processes for the user
  with `systemd-run --user …` (the user manager has WAYLAND_DISPLAY etc.). No pip on the system: build wheels with
  `setuptools.build_meta` directly. (source: this session)
- 2026-10-11: **LED order (vmap) semantics** (app `led_layout.py`): `index[i]` = physical LED at logical position i; lit LEDs
  first, then zLEDs (dark). The firmware applies it only to built-in effects (canvas `vmap_process`); **live frames are
  physical** (the app's preview chase writes `order[pos]` itself), so our Renderer maps logical→physical. Component
  files are SignalRGB format (coords `[y, x]`; app orders = stable sort by x / y, ± reversed). Some vendor vmaps repeat
  LEDs on purpose; one (Yico 2联) is inconsistent. Restore + LED-index writes verified on hw. (source: bytecode + hw)
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
- 2026-10-11: **Built-in effects get the color balance too** (user request). The firmware draws one color set per
  canvas, so a group whose ports have different balance is written as one canvas per balance with adjusted colors
  1–3 (`builtin.expand`); the user's groups are remembered (state "loop"/"boot") and shown while the controller still
  has exactly what was written, else canvases show 1:1. A changed group's canvases are always written together (to
  keep them in step). Rainbows / ColorFromPalette can't be balanced (firmware colors; the card says so). If the split
  would exceed the canvas limit (14 loop / 2 boot), groups aren't split. Static profiles and CLI `static` use the
  same path. Not done: balanced palette copies for ColorFromPalette (would take palette slots the user owns).
- 2026-10-11: Color balance stays PC-side and is applied when sending/saving (the firmware has none). OpenNollie
  remembers the last profile it saved per controller (`stored.json` "saved") and **re-saves it when the balance or
  LED counts change** (`engine.update_saved`: Ports page balance Save / Apply, CLI `balance` / `leds`), but only if
  the controller still holds that save (same canvases, or same animation + boot loop); other lighting is left alone.
  Built-in effects page colors are sent as picked (firmware-drawn; one color set per group).
<!-- Newest first. Format: YYYY-MM-DD: decision; reason -->
- 2026-10-11: Built-in effects apply **as you edit** (debounced 600 ms, only changed pieces written), like the official
  app; firmware "canvases" are presented as "groups". Colors are named "Color 1/2/3" (what color 3 does isn't known).
- 2026-10-11: The case picture is user data (`layout.json`), not code; new installs start from an automatic layout.
- 2026-10-11: "Save to device" **writes immediately** (user's choice) and accepts the ~2 s firmware LED pause for new
  animations. Rejected: deferring the flash write to shutdown/logout while the daemon plays the effect live.
- 2026-10-11: Studio autosaves the edited profile (`~/.local/state/opennollie/studio.toml`) and reopens it on start
  (user: app forgot the last settings).
- 2026-10-11: The firmware's built-in effects are **not** profile effects any more (`hw:` removed from profiles, studio,
  tray). They couldn't be previewed or fully edited there (user request). They stay on the Built-in effects page
  (canvases). Static-only profiles are still stored as Solid canvases.
- 2026-10-11: Ports are **in sync by default** (spread 0); flowing across ports / around fans / along all LEDs is an
  explicit choice (user feedback).
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
