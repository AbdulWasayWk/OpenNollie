# Nollie 16CH protocol

Device: `3061:4716` "nollie.cn Nollie 16CH RGB". Status tags: **[OpenRGB]** taken from OpenRGB's
driver · **[verified]** confirmed on our hardware · **[hypothesis]** not tested yet.

## Transport

- One USB HID interface, vendor usage page `0xFF00`, usage `0x01`. **[verified]**
- Output report: **1024 bytes**, no report ID (with hidapi, write `0x00` + 1024 bytes). **[verified]**
- Input report: 64 bytes. OpenRGB never reads it. **[verified]** descriptor; usage unknown.
- Endpoints: `0x01` OUT interrupt (1024 B), `0x81` IN interrupt (64 B). **[verified]**

## Live ("direct") mode

Source: OpenRGB `Controllers/NollieController` (commit `b190d3e`, 2026-10-09). **[OpenRGB]**

### Channel packet

```
offset  size  field
0       1     wire channel (16..31 on the 16CH, see port map)
1       1     flag: 0 = normal, 2 = latch (on wire channel 31 only)
2       2     LED count, big-endian, 0..256
4       3*n   colors, G R B per LED
...           zero padding to 1024 bytes
```

### Frame

1. Send one packet per port that has LEDs, in **ascending wire-channel order**.
2. Wire channel **31** is always sent, last, with flag `2`, even when it has 0 LEDs.
   **[hypothesis]** This latches/displays the frame.
3. OpenRGB sleeps 8 ms after the flag packet.
4. The device **holds the last frame** after the host stops sending; no keep-alive needed. **[verified]**

On this model the LED count lives in each packet. There's no separate LED-count command.
(Nollie 1CH uses `FE 03 <counts>`. Nollie 8 / 1 / 28 use 65-byte packets with a `0xFF` commit.
Neither applies here.)

### Port map

OpenRGB's port order (`Channel 1..16`) → wire channel:

| Port | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|------|---|---|---|---|---|---|---|---|---|----|----|----|----|----|----|----|
| Wire | 19| 18| 17| 16| 24| 25| 26| 27| 20| 21 | 22 | 23 | 31 | 30 | 29 | 28 |

**[hypothesis]** that these match the labels printed on the board. Check with
`python3 -m opennollie identify`.

### Wire capture (our implementation)

`captures/p1_set_all_blue.pcapng` (`set all blue --brightness 30`, 256 LEDs): 16 OUT reports
`10 00 01 00 | 00 00 4c ...` through `1f 02 01 00 | ...`, about 2 ms in total, no IN reports.
**[verified]** on-wire format, and on hardware: all fans + GPU (port 9) showed the expected color.

## Open questions

- Is a frame without channel 31 ever displayed (i.e. is flag 2 really the latch)?
- What do the 64-byte input reports carry (firmware version, status)?
- Hardware effects and **save-to-device**: not in OpenRGB, so they come from the Phase 2 captures.

---

# Vendor app analysis (NollieRGB v2.3.0)

Source: `NollieRGB_Portable_v2.3.0.zip` (sha256 `47a35eb0…dc79`), a PyInstaller build (Python 3.11 + PySide6 + cython-hidapi).
Extract with `tools/re/pyi_extract.py`, then disassemble with `tools/re/disasm.py` (under Python 3.11).
Module of interest: `app/core_driver/` (`n_usb_driver`, `n_dev_config`, `n_usb_flash_mixin`, `fseq_file`, `n_usb_event`).
Tag: **[app]** = read from vendor bytecode, not yet tried on hardware.

## Firmware generations **[app]**

| Catalog name | VID:PID | tx/rx | Notes |
|---|---|---|---|
| `Nollie16_old` | `3061:4716` | 1024/64 | **Our device.** Legacy firmware. |
| `Nollie16_2025` | `16D5:4716` | 1024/64 | Intermediate. |
| `Nollie16` | `16D5:2A16` | 1024/1024 | Current firmware (`FIRMWARE_VERSION` 16). Features: flash ✔, fseq ✔, gpio ✘. |
| `Bootloader_*` | `3061:F0xx` | — | Bootloader mode (`type` 1). |

The app routes **every legacy device (not in `N_DEV_CURRENT_NAMES`) to `OTA_UPDATE`**, and current devices with an
older fw version too (`app/services/device_flow.py: DeviceFlowService.decide`). The prompt says the update erases
settings stored on the device. Firmware images ship in `config/Nollie16.bin` (RISC-V, likely a WCH CH32V).
**Our tool must never send bootloader/OTA commands** (`0xFD` jump-BL, `0x81/0x83/0x84` bootloader).

## Current-firmware command set **[app]**

All commands go out as a single output report whose first byte is the command; responses come back as an input report.

| Cmd | Name | Sub-commands |
|---|---|---|
| `0xFF` | SHOW_LED | |
| `0xFD` | JUMP_BL | ⚠ bootloader, never send |
| `0xFC` | GET_CONFIG | 1 version (buf[1]=fw, buf[2..3]=hw), 2 fw version `a.b.c`, 3 crash flag, 26 voltage |
| `0xFA`/`0xF9` | SET/GET_EFFECT | 1 MODE, 2 CH_PARAM (per canvas), 3 CANVAS map, 4 CANVAS_LEN, 5 START_CANVAS_LEN, 6 FPS, 7 START_FPS, 8 LED_NUM (u16/ch), 9 ZLED_NUM, 10 GPIO, 11 START_CANVAS, 13 PALETTE, 14/15 DYNAMIC_1/2, 16 FSEQ_LED_NUM, 17 FSEQ_INFO, 18 FRAME_DELAY, 19 BOOT_PLAYLIST, 20 LED_DETECT, 32 INIT_START, 33/34 RESTORE_0/1, 35 SOFT_RESET |
| `0xF8`/`0xF7` | SET/GET_LED_INDEX | |
| `0xF6`/`0xF5` | SET/GET_CORE | 1 FIRST_UNIX, 2 UNIX, 3 WRITE_COUNT, 4 NAME_COUNT, 5 NAME_LEN, 6 NAME (UTF-16LE, ≤30 chars), 7 VMAP_NAME, 22 OL_IDLE (u16 s) |
| `0xF4` | FLASH_OPERATION | 1 READ, 2 WRITE, 3 STATUS, 4 INFO, 5 ERASE, 6 WRITE_RAW, 7 CRC. 1024 B packets, ≤1016 B payload |
| `0xF3` | REC_CTRL (FSEQ) | 1 ECHO, 2 PLAY (`[F3,02,0,0,loop]`), 3 STOP, 4 STATUS |
| `0xF2` | SET_LED_STREAM | live stream, 8 B header, flag bit0 = show |

Effect modes (`HID_EFFECT_MODE`): `0` FX_OL (online/streamed), `1` FX_HW (built-in effects), `2` FX_SL, `3` FX_FSEQ (play
stored animation). `CH_PARAM` = `[FA, 02, canvas, mode, brightness, step, size, canvas_ch_len, vmap_process, delay,
color1(3), color2(3), color3(3)]`. Palette = 8 groups × 16 colors (GRB). FSEQ files (xLights v2, uncompressed) are stored
at flash `0x10000+` on a 16 MB GD25Q128.

Built-in HW effects: Off, Solid, Breath, Scanning, Rainbow, Rainbow cycle, Meteor, Shake, Slowly wipe, Color wipe,
Rainbow strip (+ l), Wave, Sunny, Pull flower, Stackab1–3, Integration1–2, Interchange, Bullets, Sequence,
ColorFromPalette, Light up.

The channel order (`N_DEV_CH_NAME['Nollie16']`: Channel 1→3, 2→2, 3→1, 4→0, 5→8 …) matches OpenRGB's `n16`, i.e. our
`PORT_TO_HW` minus 16. **[app]** confirms the port map.
