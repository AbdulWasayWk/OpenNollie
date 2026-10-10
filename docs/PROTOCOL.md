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
