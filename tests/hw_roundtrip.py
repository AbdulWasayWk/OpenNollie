"""Hardware round-trip check for setters: write a test value, read it back, restore the original.

Needs the real device; not part of the unit tests. Run: python3 -m tests.hw_roundtrip
Only touches settings that don't change what the LEDs show (except briefly via LED counts on
ports with nothing connected). Every original value is restored, even on failure.
"""

import sys
import time

from opennollie import protocol as p
from opennollie.device import Nollie16

SETTLE_S = 0.15


def check(name: str, read, write, test_value) -> bool:
    original = read()
    try:
        write(test_value)
        time.sleep(SETTLE_S)
        got = read()
        ok = got == test_value
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": wrote {test_value!r}, read {got!r}"))
        return ok
    finally:
        write(original)
        time.sleep(SETTLE_S)
        if read() != original:
            print(f"!!    {name}: could not restore {original!r}")


def main() -> int:
    results = []
    with Nollie16() as d:
        wc0 = d.write_count()
        counts = d.led_counts()
        test_counts = list(counts)
        test_counts[p.PORT_TO_CH[0]] = 12            # port 1: nothing connected
        results.append(check("LED counts", d.led_counts, d.set_led_counts, test_counts))

        # LED + zLED must stay <= 256 per channel or the firmware ignores the write.
        d.set_led_counts(test_counts)
        time.sleep(SETTLE_S)
        test_z = [0] * p.NUM_CHANNELS
        test_z[p.PORT_TO_CH[0]] = 3
        results.append(check("zLED counts", d.zled_counts, d.set_zled_counts, test_z))
        d.set_led_counts(counts)

        rainbow = [(i * 16, 255 - i * 16, (i * 37) % 256) for i in range(16)]
        results.append(check("palette 7", lambda: d.palette(7), lambda c: d.set_palette(7, c), rainbow))

        results.append(check("loop frame ms", d.frame_ms, d.set_frame_ms, 20))
        results.append(check("boot frame ms", lambda: d.frame_ms(True), lambda v: d.set_frame_ms(v, True), 25))
        results.append(check("boot playlist", d.boot_playlist, lambda v: d.set_boot_playlist(*v),
                             (p.BootIntro.NONE, p.BootSteady.HW_EFFECTS)))
        results.append(check("idle timeout", d.ol_idle, d.set_ol_idle, 30))
        results.append(check("vmap name port 1", lambda: d.vmap_name(p.PORT_TO_CH[0]),
                             lambda v: d.set_vmap_name(p.PORT_TO_CH[0], v), "Test Strip"))
        results.append(check("dynamic 1", lambda: d.dynamic(1), lambda v: d.set_dynamic(1, v), list(range(16))))
        results.append(check("dynamic 2", lambda: d.dynamic(2), lambda v: d.set_dynamic(2, v), [1] * 16))
        results.append(check("frame delay", d.frame_delay, d.set_frame_delay, [40] * 16))
        results.append(check("name", d.name, d.set_name, d.name()))   # rewrite the same name
        time.sleep(3)
        print(f"flash writes: {wc0} -> {d.write_count()}")
    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
