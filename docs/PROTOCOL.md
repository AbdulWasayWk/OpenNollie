# Nollie 16CH protocol: firmware 2.3.0

Device: `16d5:2a16` "nollie.cn Nollie 16CH RGB", firmware 2.3.0 (config version 19, hardware id 165).

Sources: NollieRGB 2.3.0 bytecode (`app/core_driver/*`), the USB capture of that app driving this
device (`captures/fw_update_2.3.0.pcapng`), and our own tests on the hardware.
Tags: **[verified]** tested on our device · **[app]** read from the vendor app, not yet exercised by us.

Implementation: `opennollie/protocol.py` (encoding), `device.py` (transport), `flash.py`, `fseq.py`.

## Transport

- Interface 0 (vendor page `0xFF00`) carries the protocol: **1024-byte output and input reports, no
  report IDs** (hidapi: write `0x00` + 1024 bytes). Interface 1 is a boot keyboard (8-byte input); we
  ignore it. **[verified]**
- One command per output report; byte 0 is the command. **GET** commands are answered by one
  input report whose payload starts at byte 0 (the flash command and REC_CTRL echo their command
  byte first, see below). **SET** commands have no reply. **[verified]**
- All multi-byte integers are **little-endian**. Text is UTF-16LE. **[verified]**
- hidapi detail: `read(n, 0)` blocks forever; poll with a 1 ms timeout to drain stale input.

## Commands

| Cmd | Name | Notes |
|---|---|---|
| `FF` | SHOW | displays the channels streamed since the last SHOW |
| `FD` | JUMP_BL | ⚠ enters the bootloader. **Never sent by OpenNollie.** |
| `FC` | GET_CONFIG | `01` version → `[_, cfg_ver, hw_lo, hw_hi]` · `02` fw → `[maj, min, patch]` · `03 x` crash flag (`x=1` clears) · `1A` power |
| `FA` / `F9` | SET / GET_EFFECT | effect engine, see below |
| `F8` / `F7` | SET / GET_LED_INDEX | per-LED order remap ("vmap") |
| `F6` / `F5` | SET / GET_CORE | name, timestamps, write count, idle |
| `F4` | FLASH | external SPI flash, see below |
| `F3` | REC_CTRL | stored-animation playback |
| `F2` | SET_LED_STREAM | chunked live stream with an 8-byte header; not needed on the 16CH |
| `81 83 84` | bootloader/OTA | ⚠ never sent |

### Effect engine (`FA` set / `F9` get, second byte = item)

| Item | Name | Payload (SET) / reply (GET) |
|---|---|---|
| `01` | MODE | `m`: 0 ONLINE (host stream), 1 HARDWARE (built-in effects, default), 2 SL (transient, see FSEQ), 3 FSEQ |
| `02` | CANVAS_PARAMS | SET `[canvas, effect, brightness, step, size, ch_count, vmap_process, delay, c1 RGB, c2 RGB, c3 RGB]`. GET `[02 canvas]` → same fields starting at reply byte 3. Colors are **RGB** |
| `03` | CANVAS_MAP | SET `[0, 32 × fw ch]` · GET `[03 00]` → 16 bytes. Loop canvases |
| `04` | CANVAS_LEN | number of loop canvases (0–14) |
| `05` | START_CANVAS_LEN | number of boot canvases (0–2) |
| `06` / `07` | FPS / START_FPS | frame time in ms (33 → 30 fps); loop / boot canvases |
| `08` | LED_NUM | 16 × u16, LEDs per fw channel (default 256) |
| `09` | ZLED_NUM | 16 × u16 dark LEDs after the lit ones; **ignored unless LED + zLED ≤ 256** on every channel |
| `0A` | GPIO | not on the 16CH |
| `0B` | START_CANVAS_MAP | like `03`, for boot canvases |
| `0D` | PALETTE | `[group 0–7, 16 × GRB]` · GET `[0D g]` → 48 bytes GRB |
| `0E` / `0F` | DYNAMIC_1 / _2 | 16 bytes; per canvas: palette group / blend |
| `10` | FSEQ_LED_NUM | 16 × u16, the stored animation's channel map |
| `11` | FSEQ_INFO | GET → `[present, size u32]` |
| `12` | FRAME_DELAY | 16 bytes, per canvas (default 33) |
| `13` | BOOT_PLAYLIST | `[intro, steady]`. intro: 0 start canvas, 1 none, 2 FSEQ once. steady: 0 HW effects, 1 FSEQ loop |
| `14` | LED_DETECT | **not supported** by the 16CH firmware (status stays 0; app gates it to the Nollie8 Plus) |
| `20` | INIT_START | play the boot canvases now (app's "play" on the boot-canvas page) |
| `21` | RESTORE | factory reset; keeps calibration and controller name |
| `22` | RESTORE_SUPER | deeper reset; **not used** |
| `23` | SOFT_RESET | reboot; the device re-enumerates **[verified]** |

**Canvases.** A canvas is a group of channels running one effect. The device stores one flat
channel order (`03` for loop, `0B` for boot) plus `ch_count` in each canvas's params: canvas *i*
owns the next `ch_count` entries; channels not in any canvas follow. Loop canvases are 0–13 and boot
canvases are 14–15. To add or replace canvases the app sends: params for each canvas → map → length.
**[verified]** (5-canvas Solid layout.)

Which settings each effect uses (from the app's effect catalog) is in `protocol.EFFECT_PARAMS`; slider ranges
in the app: brightness 0–100, step (speed) 1–10, size 0–50, delay 0–8, frame delay 1–255 ms. Per-canvas extras:
`FRAME_DELAY[canvas]`, `DYNAMIC_1[canvas]` = palette group, `DYNAMIC_2[canvas]` = blend.

**Effects** (`effect` = index): 0 Off, 1 Solid, 2 Breath, 3 Scanning, 4 Rainbow, 5 Rainbow cycle,
6 Meteor, 7 Shake, 8 Slowly wipe, 9 Color wipe, 10 Rainbow strip, 11 Rainbow strip l, 12 Wave, 13 Sunny,
14 Pull flower, 15 Stackab1, 16 Stackab2, 17 Stackab3, 18 Integration1, 19 Integration2, 20 Interchange,
21 Bullets, 22 Sequence, 23 ColorFromPalette. Boot canvases use: 0 Light up, 1 Color wipe,
2 Slowly wipe, 3 Pull flower, 4 Stackab1, 5 Stackab2, 6 Stackab3, 7 Bullets. Which parameters each
effect uses: `EFFECT_WIDGET_VISIBILITY` in the app's `effect_catalog`.

Factory default: 1 loop canvas, all 16 channels, Breath, brightness 100, colors `#00ff00 #0000ff #ff0000`.

### Core (`F6` set / `F5` get)

| Item | Name | Payload |
|---|---|---|
| `01` / `02` | FIRST_UNIX / UNIX | u32 timestamps (the app sets UNIX on every connect) |
| `03` | WRITE_COUNT | GET u32: flash program operations (goes up a few seconds after a SET) |
| `04` `05` `06` | NAME_COUNT / NAME_LEN / NAME | set name: `04` u32 (count+1), `05` u32 byte length, `06` UTF-16LE (≤ 30 chars) |
| `07` | VMAP_NAME | SET `[ch, byte_len, UTF-16LE]` · GET `[07 ch]` → `[len, text…]` |
| `16` | OL_IDLE | u16 seconds before falling back from host streaming to HW effects. 0 = default (**3.0 s**, measured), 65535 = never. App choices: 5, 10, 30, 60, 300, 1800, 3600 |

### LED index / vmap (`F8` set, `F7` get)

16 × 256 bytes; byte `ch*256 + i` = physical LED for logical position *i*. Read `[F7, off u16, n ≤ 255]`
→ n bytes. Write `[F8, addr u16, n ≤ 255, bytes…]`.

### Power (`FC 1A`) **[verified]**

u16 values: input voltage (mV), input current (×10 mA), MCU supply (mV), MCU temp (×0.01 °C),
CPU load (%), ROM state. Example: 5.128 V, 0.75 A, 3.297 V, 41.0 °C.

## Live streaming **[verified]**

Per channel: `[ch, 0, 0, 0, 256 × (G, R, B)]`, then `FF`. The first frame switches the mode to ONLINE.
The device falls back to HW effects `OL_IDLE` seconds after the last frame (3 s by default), so live
output must be streamed continuously. `FA 01 01` returns to HW immediately.

## Flash (`F4`) **[verified]**

Request: `[F4, op, addr (3 B), len (2 B), 0]` + up to 1016 bytes of data. Reply: `[F4, …, status @7, data @8…]`.
Status: 0 OK, 1 BUSY, 2 ERROR, 3 ADDR_ERR, 4 LEN_ERR, 5 NO_DEV, 6 RETRY (resend).

| op | Name | Use |
|---|---|---|
| 1 | READ | poll until not BUSY; data at reply byte 8 |
| 2 | WRITE | (unused by the app for FSEQ) |
| 3 | STATUS | poll after erase/write |
| 4 | INFO | reply bytes 8–10 = manufacturer, type, log2(capacity). Ours: GigaDevice, 16 MB |
| 5 | ERASE | 64 KiB block at `addr`, `len` = 1; acked BUSY, then poll STATUS (≤ 2 s) |
| 6 | WRITE_RAW | acked BUSY, then poll STATUS until OK |
| 7 | CRC | start: `addr`, `len` low 16 bits, byte 7 = `len >> 16`; poll STATUS; result `[F4 07 0…]` → u32 at byte 8 |

CRC = CRC-32 poly `0x04C11DB7`, init `0xFFFFFFFF`, MSB-first, no final XOR, over little-endian 32-bit
words, with trailing bytes fed as `b << 24` (STM32/CH32 hardware CRC). Matches the app's `nos_crc32` on
46 test inputs. **The FSEQ region starts at `0x10000`; OpenNollie refuses to erase or write below it.**

## Stored animations (FSEQ) **[verified]**

- File: xLights **FSEQ v2, uncompressed**, stored as-is at `0x10000`. Frame data is packed by sparse
  range; the firmware maps the ranges in order onto the channels with nonzero `FSEQ_LED_NUM`, in
  ascending fw channel order. We write one range per active channel.
- **Frame bytes go to the LEDs unconverted, so store colors as G, R, B.** (Bytes `00 66 00` showed red.)
- Upload (as the app does it): `F3 03` stop → `FA 10` channel map → erase blocks → WRITE_RAW in 1016-byte chunks
  → device CRC (fallback: read back) → poll `F9 11` until present. 190 KB took 1.6 s. OpenNollie stops with
  `FA 01 01` instead of `F3 03` (see SL below) and skips the upload when the same file is already stored.
- Play: `[F3, 02, 0, 0, loop]`. Stop: `F3 03`. Status: `F3 04` → `[F3, playing, position u32, code]`.
  While playing, mode = FSEQ.
- **Mode SL = LEDs paused (dark) while the flash is busy.** Entered on `F3 03` (stop) and on flash access (CRC,
  writes); it lasts until ~1.2 s after the last flash operation, ignores live frames and PLAY, and can't be cut
  short. Afterwards the device returns to its stored lighting. **`FA 01 01` (mode HW) stops playback instantly
  without SL**, so OpenNollie stops playback that way and never sends `F3 03`. **[verified]**
- Persist: boot playlist steady = 1 (FSEQ loop). After a reboot the device plays the animation by itself.
- Play result codes: 0 OK, 1 erased, 2 busy, 3 no channel map, 4 no file, 5 bad header, 6 map mismatch,
  7 compressed, 8 read error, 255 pending.

## Channel numbering

Board/app port N → fw channel `PORT_TO_CH[N-1]` = `3 2 1 0 8 9 10 11 4 5 6 7 15 14 13 12`. **[verified]**

## Legacy firmware (pre-update, `3061:4716`)

The live-only protocol from OpenRGB: per channel `[16 + ch', flag, count_hi, count_lo, GRB…]`, ascending,
with channel 31 sent last with flag 2 to display. Our code for it is in git history (`0127bce`).
